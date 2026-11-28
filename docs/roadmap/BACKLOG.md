# Backlog

**This is the only file in `docs/roadmap/`.** It holds what is OPEN, what is
blocked and on whom, what was declined and why, and an index of what shipped.
It does not hold implementation history.

Written 2026-09-20, replacing eight roadmap files totalling 3,626 lines.

## The three rules this file exists under

1. **Shipped work LEAVES this file.** When an item ships, its narrative moves to
   `docs/changelog/epic-<letter>.md` — PR numbers, review rounds, what was
   refuted and why — and its row moves to the shipped index at the bottom. That
   is `docs/changelog/README.md`'s own resolution rule, and it stopped being
   applied around PR #120: Epic Z's sixteen pull requests, W2's package split
   and Y7 all accumulated here instead. That is how one file reached 1,604
   lines with 633 of them — about two fifths — inside entries marked DONE,
   under a heading called "Backlog".
2. **Status is stated ONCE.** It was previously stated in five places — a
   shipped table, a `— DONE` marker on the entry, the sequencing step, the
   not-started list, and sometimes a ruling annotation — and a 396-line test
   class existed to keep those five synchronized. That is a mirror, and this
   repository's answer to a mirror is to have one copy (AGENTS.md §1 on the PII
   gate, CLAUDE.md on the `.claude/` three-way mirror). An id appears in the
   Open section or in the shipped index, never both.
3. **A review is a dated snapshot and is never maintained.** The measurements in
   a review go stale the week after it is written. Five of the eight deleted
   files were reviews by their own titles, still asserting figures that had
   moved — the 09-05 review's Part
   2 said 3,424 tests against a suite half again that size today, put
   `preflight.py` at 2,732 lines when it has since grown by about 7%, and said
   "No linter, no type checker, no `pyproject.toml`", all three of which shipped
   in PR #90. A reader acting on that files a ticket for finished work. Reviews
   now live in git history; their CONCLUSIONS live here as items.

## Where the deleted files went

| Deleted | Its content now |
|---|---|
| `2026-09-05-staff-agent-review.md` | Items → the Open section or shipped index below. Epic Z narrative → `docs/changelog/epic-Z.md`. W2 and W3 slice 3 → `epic-W.md`. Y7 → `epic-Y.md`. The review itself → git history. |
| `2026-08-12-product-owner-review.md` | Shipped Epics R-U → `docs/changelog/epic-R-U.md` (new), including **R1's audit baseline**. Distribution analysis → below. |
| `2026-state-support-ny-fl-wa.md` | Its WA stories shipped as Z5/Z6; see `docs/changelog/epic-Z.md`. Its FL and NY story tables are NOT moved — that work shipped and is narrated in `epic-P.md` and `epic-Z.md` from other sources; the tables themselves are in git history. |
| `2026-tax-planning-features.md` | All twelve features shipped; see `docs/changelog/pre-epic.md` and the historical design snapshot below. Its `/tax-retirement` v1.1 deferrals subsequently shipped as X10; see `docs/changelog/epic-X.md`. |
| `2026-multi-agent-portability.md` | Epic Q shipped in PR #66; plan retained at [2026-08-03-multi-agent-portability.md](https://github.com/yarikmsu/tax-prep-skills/blob/017d2dd45ead/docs/superpowers/plans/2026-08-03-multi-agent-portability.md). |
| `2026-07-12`, `2026-07-17`, `2026-07-20` reviews | Epics I-O, all shipped; `docs/changelog/epic-{I,J,K,L,M,N,O}.md`. |

The historical design directory was retired after the 2026-10-03 review.
The extensions it recovered have all shipped; AA44, the last, in #82.
Maintained state contracts and the template moved to
`docs/contributing/state-module-{ny,nc,wa,template}.md`,
indexed in AGENTS.md §9. Origins below and historical changelog citations link
to the [pre-migration Git snapshot](https://github.com/yarikmsu/tax-prep-skills/tree/017d2dd45ead/docs/superpowers).
Old plans remain available there, including their contemporary roadmap
citations; they no longer direct contributor work in the current checkout.

---

## Status

**Order: California filers first** (owner, 2026-10-24). Every current user
files in California, so the plan finishes what a CA filer needs before anything
else: CA work, then federal work a CA filer reaches, then other states, then
the repository itself. An entry's priority is its severity for a filer it
reaches; the plan's order is who those filers are.

**Within that, by outcome** (owner, 2026-11-18, Q18): filing quality first —
the filed return is complete and correct, and nothing incomplete passes as
validated — then the household's after-tax outcome, then ease of use. A small
planning fix whose value ends on a fixed date, such as a December 31 decision,
goes ahead of filing work that cannot be used before that date.

**Shipped from the owner's 2026-11-26 queue:** AA127 step 2 (#146), AA138 (#147),
AA137 (#148-#151) and AA139 (#152-#155), each fixed in review before merge.

**Next for implementation (owner, 2026-11-27):** AA127 step 2b; AA140 steps 1-3;
AA122 step 1; AA134 step 1; AA133; AA124 steps 1-2; AA122 step 2; AA134 step 2;
AA121; AA120 steps 1-2 — in that order, each step a pull request of its own.
Their entries name the code, the behavior, the tests and when each is done, and
none waits on a publication or a ruling. Each tier-1 fix adds its AA115 negative
case. Tiers 5-7 are not yet written to that standard.

**Engineering: the original V, W and X queue is complete.** The 2026-10-03
review of the retired design directory recovered useful, unimplemented extensions;
AA42 to AA46 carried the rest of that scope, and all five have shipped.
The suite's size is stated nowhere: since AA8 the
MEASURED COSTS block in `.claude/hooks/skill-invariants.sh` counts only the
seven modules that hook selects, and a count restated here would be the mirror
this file was collapsed to remove.

**Product: blocked on one decision.** The repository is public, but Discussions
are off and there are zero non-owner users. Every distribution item has been
waiting on that since 2026-08-12.

**CI runs automatically.** Pull requests and main-branch pushes register CI,
including the per-trigger PII scan, and the weekly schedule audits the locks.
Trigger and range behavior is documented in
[`ci-and-locks.md`](../contributing/ci-and-locks.md).

---

## The plan, in order

| When | What | Why |
|---|---|---|
| **0. Before 2026-12-31: small, dated planning fixes** | **AA127** step 2b — four gaps #146's review reproduced: a lot with no `tax_advantaged` flag, a spouse's lots on a separate return, a quantity-less purchase beside a gain lot, and same-day lots matched in file order. **AA84** the day FTB's missing inputs publish. | Q18. A 2026 harvest sale cannot be made after December 31, and each gap over-states the savings a filer acts on. TY2026 filing work cannot be used before that year's documents arrive. AA84 is blocked (blocked table). |
| **1. Filing quality on every return, before TY2026 documents arrive** | **AA140** — employer identity for excess Social Security, a 1099 inside a consolidated statement, and near-duplicate and separate-return payments, in three steps; the two payment gaps share step 3. **AA115** gains one negative case per fix. | AA137, AA138 and AA139 shipped in #147-#155; AA140 is what their review left that can over-state a filed return. W-2s arrive by January 31; corrected 1099s often in March. |
| **2. Filing quality: as FTB and the IRS publish** | **AA83** — the TY2026 Form 540. **AA80** — the TY2026 part-year return. **AA48** — the TY2026 Form 8949 boxes. **Y8** — the 2027 §6621 quarters, the 2026 exchange rates and the TY2026 state tables. | AA83 decides whether any CA resident can file a TY2026 return here. AA48 blocks every TY2026 crypto import. AA80 serves fewer filers. Since AA58, Form 2210 leaves a quarter the cache does not hold uncomputed, so Y8 is the reminder to fetch it. The blocked table dates each check. |
| **3. Filing quality for investors and itemizers** | **AA122** step 1 — special-rate gains block a final package. **AA134** step 1 — a missing Form 8938 value is refused. **AA133** — charitable substantiation. **AA124** — the simplified election, then Form 1116. AA122 step 2 — the Schedule D Tax Worksheet. AA134 step 2 — the asset inventory. **AA121** — the federal Tax Table. | In that order (owner, 2026-11-27): the two small refusals first, since each stops a wrong answer the engine gives today. REIT and international funds, gifts of appreciated stock and foreign accounts are what a brokerage household holds, and the $40,000 SALT cap brings more CA filers to Schedule A. AA121 (Q19) is a few dollars either way and reaches every filer whose ordinary income is under $100,000, investors with larger totals included. |
| **4. Filing quality: mandatory forms with low reach** | **AA120** — the advance-payment question, then a reviewed Form 8962 on its Form 1040 path. | P0 for a filer it reaches, but no current user is known to have Marketplace coverage. Since #132 such a filer is held to a draft; what is missing is the route to a final package. It moves to tier 1 the day such a filer appears. |
| **5. After-tax outcome** | AA127 step 3 — the post-sale window and the effects breakout. AA125 in full. **AA126** — Medicare costs in the single-year ladder. **AA123** — screened refundable credits. | Q16 sets AA123's shape. AA126 reaches filers about 63 and older. |
| **6. Ease of use** | **AA118** — setup and the support table from one coverage source. **AA117** — one document inventory. **AA119** — one finalization command. **AA135** — evidence-based review. | AA117's Windows path defect blocks every Windows tie-out rather than passing a wrong return. |
| **7. Parser maintenance and other states** | **AA116** — the cross-cell false mask, now recoverable by local transcription. **AA110** — residual identifier layouts. **AA111** — parse cost. **AA79** — TY2026 NY and NC payment forecasts. | AA110 and AA111 measure identifier protection, lost figures and false refusals together. No current user files in New York or North Carolina; AA79 is read the day those instructions publish, since the last 2026 estimate is due 2027-01-15. |
| **Owner decision; excluded from the current implementation queue** | **Y1** — enable Discussions on the now-public repository, or demote the distribution goal in writing. | The TY2025 window ended 2026-10-15; the next opens in January 2027. |
| **Closed** | W7, W8, W9 — declined. Y3 — declined with a re-open trigger. | All three trade a working mechanism for tidiness. |

---

## Open

### AA — the 2026-09-20 product-owner review, and what later reviews filed under it


- **AA48 (P1, S): Add the TY2026 Form 8949 boxes before TY2026 crypto imports.**
  `_REPORTING_REGIMES` holds TY2023-2025 only, so every TY2026 import with a
  filing-year disposal blocks. One that supplies `broker_reporting` is refused
  outright, exit 1, with no FIFO or form workpaper. A test pins that refusal on
  purpose: evidence the engine cannot read for its year is refused like
  malformed evidence. When the TY2026 Form 8949 instructions publish, add the
  entry and its row in `crypto-accounts.md`'s table from a year-pinned
  `irs-prior` copy, as the source test requires. Found by AA41's review.
  **Blocked on the IRS, as of a live check on 2026-10-13:** the year-pinned
  [TY2026 Form 8949 instructions](https://www.irs.gov/pub/irs-prior/i8949--2026.pdf)
  returned HTTP 404, and the IRS's
  [current instructions](https://www.irs.gov/pub/irs-pdf/i8949.pdf) still named
  TY2025. Keep the unreviewed-year refusal until the TY2026 instructions
  publish. Do not carry the TY2025 boxes forward, and do not pin the entry to a
  URL that does not resolve yet: the source test checks the prefix and the
  year, not that the file exists. AA55 re-read every TY2026 Form 1099-DA box,
  whether the import reads it or not, and each kept its meaning, so the entry
  needs no per-year input map. A TY2026 import cannot carry `broker_reporting`
  until this lands, so a TY2025 return that moved a sale back from 2026-01-01
  under AA47's reviewed dates (#46) relies on its `YEAR MOVE` record in
  `answers.md` until then.




- **AA79 (P2, M): Review TY2026 for the NY and NC payment forecasts.** AA43
  shipped for the TY2025 form year only. Reviewed live 2026-10-25 and BLOCKED
  (blocked table). New York's [2026 IT-2105 instructions](https://www.tax.ny.gov/pdf/2026/inc/it2105i_2026.pdf)
  print the rate schedule (every rate below 6.85% down a tenth of a point),
  recapture bases, the $150,000 MCTMT threshold and the standard deduction,
  but not the household credit tables, the §615 itemized-deduction limitation
  or the subtraction caps the forecast reads, nor the IT-201 tax table the
  engine uses at or below the entry threshold; they direct the 2025 returns
  and instructions as a guide, and caching 2025 figures for TY2026 is the
  inference AGENTS.md §2 forbids. North Carolina's
  [2026 estimator worksheet](https://www.ncdor.gov/taxes-forms/individual-income-tax/estimated-income-tax/2026-individual-income-tax-estimator-worksheet)
  keeps the 2025 structure at 3.99%, but `nc.py` reads Schedule S lines and the
  IRC conformity date per year from the D-401. When the TY2026 instructions
  publish, add the year to `planning._REVIEWED_YEARS` after reading them; the
  review must also make `compute_ny_itemized` honour each charitable tier's
  `first_year`/`last_year`, which nothing reads today, so a lapsed §615(g) tier
  cannot halve the deduction. The last 2026 voucher is 2027-01-15; NY usually
  publishes the IT-201 instructions in December. P2 while no current user
  files in NY or NC. Found by AA43's review.

- **AA80 (P2, M): Review AA74's part-year return for TY2026, with the renter's
  credit six-month test.** AA74 computes a reviewed CA part-year return for
  TY2025 only. The renter's credit is computed on neither form: it is a manual
  capability rule, and on the 540NR path `agents/lib/capabilities.py` swaps
  only the line, to Form 540NR line 61. Its note warns one way, that omitting
  an eligible credit over-states CA tax, but a part-year filer's likelier error
  is the opposite. The 540NR qualification record asks whether the filer was a
  California resident for at least six full months of the year and paid rent
  for at least half of it on a California principal residence
  ([2025 540NR booklet](https://www.ftb.ca.gov/forms/2025/2025-540nr-booklet.pdf),
  page 25), and one who fails either gets no credit. A banner does not fix a
  filed position nothing asked about: when FTB publishes the 2026 booklet,
  review the part-year return against it and give the rule a 540NR note naming
  that test, as `ca_use_tax` and `ca_noniso_amt` already have, with a test
  pinning the wording. Found by AA74's review.



- **AA83 (P1, M): Review the TY2026 Form 540 before TY2026 CA returns.** The
  CA return path is reviewed for TY2025 only, at four gates:
  `_CA_540_REVIEWED_YEARS` in `agents/lib/tax_computations/state/ca.py` holds
  2025 for the Form 540 line map ("Form 540 line map has not been reviewed for
  this tax year"), for Schedule CA, which the tie-out gate rebuilds, and for
  FTB 5805; `ca_credits.py`'s credit lines accept 2025 alone; and the filing
  package refuses a checked Form 540 outside that set. Until each is reviewed,
  no CA resident can finish a TY2026 return here. This was one clause in Y8's
  plan row. When FTB publishes the 2026 Form 540 booklet
  ([its year-pinned URL](https://www.ftb.ca.gov/forms/2026/2026-540-booklet.pdf)
  returned 404 on 2026-10-24) and the year's Schedule CA and FTB 5805
  instructions, re-read every line AA69's checked builder computes, and every
  line it leaves manual or refuses, against them before adding 2026; carry no
  TY2025 line forward unread. AA80 is the part-year half. Found by the
  2026-10-24 California-first review of the plan.

- **AA84 (P1, S): Review TY2026 for CA tax in Roth and scenario planning.**
  `_REVIEWED_YEARS` in `agents/lib/tax_computations/state/planning.py` holds
  2025 for CA, a gate separate from AA83's, so `/tax-roth-convert` and
  `/tax-scenario` price no CA tax for TY2026 and print "State/local tax not
  modeled". The degrade under-states, as AGENTS.md §4 allows, but a 2026
  conversion has to be made by December 31, so a CA filer deciding one now sees
  no state cost. **Live review 2026-10-25:** FTB's
  [current Tax News page, October 2026](https://www.ftb.ca.gov/about-ftb/newsroom/tax-news/#article1)
  publishes the TY2026 brackets, standard deductions and personal/dependent
  exemption credit amounts, although the dated October URL still returns 404.
  That release says the complete tax rates and exemption amounts arrive in late
  December. `planning._ca` also reads `state.tax_table` (ceiling and published
  rows), `state.amt_screening`, `state.exemption_phaseout` and
  `state.itemized_phaseout`; the release does not supply those planner inputs.
  The TY2026 Form 540 booklet, tax-table PDF and Schedule CA/P instructions
  still return 404. The 2026 Form 540-ES worksheet directs the prior-year table
  and exemption credits, so it cannot fill the gaps. Keep the reviewed-year
  gate closed until the missing covered-year inputs and the planner's CA
  contracts can be reviewed; do not derive them from the announced inflation
  percentage or substitute TY2025 values. Found by the same review.
- **AA110 (P3, M): Layouts AA109's review left.** Found on synthetic tables and PDFs.
  An identifier fragment in a governed column, such as its first four or six digits,
  prints when a whole comb in a named column follows it: a whole value bounds the run,
  and the fragment alone reads as no comb. Main masked it only by swallowing the
  neighbour, routing numbers included. A marked SSN one cell right of an empty identifier
  column, under a named heading such as Date, prints whole, because a named column keeps
  its ownership on a shifted row. AA116 covers the related loss of tax amounts.
  In PDFs, an SSN beginning 1545 printed straight after "OMB No." under the
  W-2's shared SSN heading, with its fifth digit
  unmapped, is exempted as OMB metadata and prints eight digits. Closing that refuses
  W-2 pages whose OMB hyphen is unmapped, unless the exemption checks each form's real
  IRS control number, read from the form rather than assumed.
- **AA111 (P3, M): Parse cost AA109 added.** Found by #124's review; each change needs a
  reproducible benchmark run before and after it. `mask_table` takes about twice main's
  time on an ordinary table with no identifier, a register or a dependents list: for
  every column of every data row the wrapped-header check calls `_is_id_label_cell` on
  the cell above, the cell and the two joined, recomputing what the previous row found,
  and the date and financial caption patterns run on every data row. Reusing the previous
  row's label results, and checking headers only on rows without digits, would avoid it.
  In PDFs, each separator hypothesis that finds a candidate probes the whole page again
  (`extract_words`, `_readable_identifier_glyphs`, `_overlaid_digits`), up to the budget
  plus one passes; a dense page of short rows with mixed unmapped glyphs took about seven
  times main's time. Probing only the candidates' rows, or every marking in one pass,
  would avoid it. `_font_resource_chars` also replays the whole content stream for any
  page with one unmapped glyph, though only the unknown glyphs need the font identity.

- **AA115 (P1, M): Extend the California filing release test as paths land.** The
  TY2025 W-2 and brokerage household, two W-2s with a posted estimate and an
  overlapping-receipts proprietor shipped in #141 with hand-worked federal and
  California figures, each recovery ending in a fresh export. Still to add, each
  when its path lands: refundable credits (AA123), Form 1095-A (AA120), special-rate
  gains (AA122), a Tax Table return (AA121, no scenario reaches it yet), and TY2026
  after AA83. Each new tier-1 fix adds its negative case. The tests stay runnable
  without model credentials (Q2). Found by the 2026-11-18 external review.

- **AA116 (P2, M): Stop the cross-cell false mask without unmasking identifiers.**
  The local recovery path shipped in #134: a masked or unreadable filing field is
  named, held open and transcribed. The false mask itself remains: a three-digit
  figure, a space-grouped amount and a one-digit figure in consecutive cells read as
  one 3/2/4 value (16 of 20,000 identifier-free tables). #134's first head freed the
  amount under `Count | amount | Copies` and printed identifiers cut the same way:
  841 of 20,000 fuzzed tables with an SSN label in the row, and 2,535 of 5,000
  through an amount set that outlived later headers; the narrowest repair tested
  left about 900. A fix must measure leaks under labels, headers and stale header
  state together with lost figures before any rule widens. Found by #124's review.

- **AA117 (P2, M): Share one source inventory across intake and validation.**
  Reproduced 2026-11-18: the scanner reads the top level of `documents/` (.pdf,
  .xlsx, .csv, skipping names containing "Summary"), the ledger builder the top
  level in any format, and tie-out recurses. Tie-out also records native path
  separators, so on Windows each document reads as both missing and extra and every
  tie-out fails. Share document eligibility and canonical workspace-relative paths
  with the receipt, and define the intake disposition for nested files, hidden
  entries, unsupported formats and links. Add a Windows smoke test for scan, ledger
  and tie-out; CI's Windows job runs two modules today. Migrate older ledger paths
  and keep source confinement checks in place. Found by the 2026-11-18 external
  review.

- **AA118 (P2, M): Drive setup and the support table from one coverage source.**
  The README's privacy and support claims and the persona guide's NC and WA scope
  were corrected in #131. The reviewed-year refusal still arrives only at compute or
  package time, after intake. Drive setup and the public support table from the
  same state, form-year, residency and workflow coverage, and test that an
  unreviewed CA year is named before document intake, a supported D-400 shows as
  computed, and manual worksheets and estimate-only paths are visible. Found by the
  2026-11-18 external review.

- **AA119 (P2, L): Publish the validated return and package deterministically.** The
  three gates run as separate processes, each taking the workspace lock for itself,
  and package parts 2-4 are written by the agent from prose. Most of the binding
  exists: the receipt's input digests, `covers_result` on the packaged bytes, and
  the interrupted-run marker. What is left is the PDF re-reading `result.json` with
  nothing binding it to the verified bytes. Reuse the existing engine, receipt and
  PDF renderer in one finalization command that validates a single input generation
  and publishes the return and package together. Test that an interrupted or
  concurrent write cannot publish a mixed generation, a retry with unchanged inputs
  publishes the same values, and drafts stay visibly marked. Found by the 2026-11-18
  external review.

- **AA120 (P0, M): Give Form 8962 its route to a final package (Q15).** Since
  AA136 (#132) the filing-package CLI no longer reports `validated: true` for an
  acknowledged Marketplace case: `capabilities.required_form_items` registers
  `form.marketplace_insurance` (the one rule with `required_form=True`), and
  `material_evidence.open_items` reports every required form open whatever its
  record says, while `record` refuses `form.` ids. What is left is the other
  half of Q15: no filer with Marketplace coverage can reach a final package at
  all, and one with neither advance credit nor a claim never needed Form 8962.
  A return with advance credit paid and no Form 8962 is incomplete: the IRS
  holds the refund and writes for the form. Two steps, each one pull request.
  **Step 1, ask about advance payments.** `tax-setup/references/interview.md`
  asks only "Marketplace health insurance? (Form 1095-A)". Ask whether Form
  1095-A column C shows advance credit and whether the filer will claim the
  credit, as two profile flags in `taxpayer_profile_template.json` (the
  capability flag test requires both places), and key the required form on
  either being true. A filer with Marketplace coverage and neither reaches a
  final package. AGENTS.md §4's sentence on acknowledging a limitation gains
  Q15's rule: an acknowledged mandatory form allows a draft only. Tests in
  `tests/test_capabilities.py` and `tests/test_filing_package.py`: coverage with
  neither (final package), advance credit with an acknowledgment only (draft,
  refused final), and an unanswered question (held, never read as no).
  **Step 2, a reviewed Form 8962.** The engine does not compute Form 8962; it
  records a reviewed one. Add a `form_8962` section to `result_template.json`
  with lines 24 (total credit), 25 (advance payments), 26 (net credit) and 27-29
  (excess, repayment limitation, repayment), each a figure from the worked form;
  check 26 = max(0, 24 - 25), 27 = max(0, 25 - 24) and 29 = min(27, 28) where
  the covered-year rules give a limitation, and line 25 against Form 1095-A's
  column C, which `tax-parse` does not read today: record its annual total as a
  ledger category (`tie_out.CATEGORY_TARGETS`). `return_builder` carries line 26
  into Schedule 3 line 9 (inside line 31, so `_check_the_payments` counts it
  beside lines 10 and 11) and line 29 into Schedule 2 line 1a, the excess
  advance credit repayment (through line 1z into line 3, so line 17 becomes the
  AMT plus line 29 and `_check_the_amt` stops refusing it). The
  `form.marketplace_insurance` item closes when the section is complete and the
  receipt binds it to the result; `filing_package._FEDERAL_SEQUENCE` lists Form
  8962 and the attachment checklist names Form 1095-A. Tests: a net credit, an
  excess repayment, and line 25 disagreeing with the 1095-A (refused). A TY2026
  case with excess advance payments under the covered-year repayment rules
  joins when the TY2026 instructions publish; it does not hold the step. Done
  when each check turns a test red when reverted.
  **Not queued:** `household_employees` (Schedule H), `pfic_taxable_event`
  (Form 8621), `rental_income` (Schedule E) and `foreign_asset_reporting` (Form
  8938) name forms the return needs when their situation applies, Schedule H
  and Form 8938 above a threshold, but carry no `required_form`; marking each
  makes its filers draft-only until it has a route of its own, so each goes
  with its route. `kiddie_tax`'s Form 8615 belongs on the child's return, not
  the one the flag describes. Found by the 2026-11-18
  external review and the code. [IRS Form 8962 instructions,
  TY2025](https://www.irs.gov/pub/irs-prior/i8962--2025.pdf)

- **AA121 (P2, M): Apply the federal Tax Table where Form 1040 requires it (Q19).**
  One pull request. In a TY2025 single-filer case with $30,000 of taxable
  income, `compute_bracket_tax` returns $3,361.50 from brackets; the Tax Table
  row ($30,000-$30,050) prices its midpoint, $30,025, at $3,364.50, which rounds
  to $3,365. Per Q19, filed returns leave the exact-bracket convention
  `return_builder.py`'s docstring records, and the docstring changes with it.
  The IRS recomputes line 16 from the table, so the return as filed is not the
  return as processed, and tax-verify's $1 tolerance flags every such return.
  **Cache.** No federal `tax_table` key exists. Add one to
  `tax_law_fetcher.create_empty_tax_law` with the ceiling ($100,000) and the
  row segments by width, each row "at least" its floor "but less than" its
  ceiling as the table prints it, a `_TABLE_VALIDATORS` entry, the fetch step
  in `tax-setup`'s law-fetch reference, and its row in
  `docs/contributing/tax-law-cache.md`. Pin a few printed rows per status from
  the covered year's table and check the computed rows against them at fetch,
  as `tax-law-fetch.md` does for New York.
  **Arithmetic.** Share only the row arithmetic with New York: the segment
  lookup, the midpoint and half-up rounding to the dollar in
  `state/ny.py::_ny_table_tax`. Its `table_bases` and the recapture in
  `compute_ny_tax` stay New York's.
  **Filing only.** `compute_qdcg_tax` and `compute_bracket_tax` also serve
  `compute_scenario` and `projection.project_federal_tax`, and planning keeps
  exact brackets so marginal answers have no $50 steps: the table is opt-in,
  used by `return_builder._tax` for line 16 below the ceiling and for both
  ordinary-tax lines of the Qualified Dividends and Capital Gain Tax Worksheet
  (lines 22 and 24), each below the ceiling on its own amount, which reaches an
  investor whose ordinary income is under $100,000 while total taxable income
  is above it. `applicable_law._federal_figures` requires the table whenever one
  of those amounts is under the ceiling. Tests: the $30,000 row, both edges of
  a row, $99,999 against $100,000 (the Tax Computation Worksheet), a worksheet
  case with a table component, and a planning call unchanged. TY2026 needs its
  rows from the TY2026 table at season open. Done when each rule turns a test
  red when reverted. Found by the 2026-11-18 external review. [IRS Form 1040
  instructions, TY2025](https://www.irs.gov/pub/irs-prior/i1040gi--2025.pdf) ·
  [IRS Publication 1040, TY2025 Tax and EIC
  Tables](https://www.irs.gov/pub/irs-prior/p1040--2025.pdf)

- **AA122 (P1, M): Price special-rate gains with the Schedule D Tax Worksheet.**
  Known since V1 and written in `tax-gotchas.md` and `return_builder.py`'s
  docstring, but nothing flags it: a REIT fund's 1099-DIV box 2b unrecaptured
  §1250 gain, a box 2d collectibles gain, a 1099-B sale marked collectibles
  (gold and silver ETFs) or a sale of depreciated real property is priced by
  `compute_qdcg_tax` at 0/15/20%, and the tax is under-stated with every gate
  green. Two steps, each one pull request.
  **Step 1, hold the final package.** `tax-parse` reads 1099-DIV boxes 1a, 1b,
  2a, 5, 12 and 14-16 (`references/form-extraction.md`) and the 1099-B
  collectibles box only on the crypto path. Record boxes 2b and 2d, and the
  1099-B collectibles box, as ledger categories in `tie_out.py`
  (`_INFORMATIONAL`; `_ALL_CATEGORIES` follows from it, and
  `test_every_category_documented_in_tax_parse_skill` needs each row in the
  SKILL.md category table). Add a
  profile flag for a sale of depreciated real property, in
  `taxpayer_profile_template.json` and the `tax-setup` interview beside the
  rental and home-sale questions. Then add `input.schedule_d_tax_worksheet` to
  `material_evidence._FILING_INPUT_LABELS` and `_required_filing_inputs`,
  required when any of them is positive. This is an `input.` item, not a
  `required_form` rule: nothing can close a `form.` item until AA120 step 2.
  Closing it as `reviewed` attaches the worked worksheet; its tax is recorded as
  `schedule_d.tax_worksheet_tax`, and `return_builder._tax` takes it for line 16.
  The worksheet's last line is the smaller of its own tax and the regular tax
  on the same taxable income, so refuse a figure above that regular tax; below
  $100,000 the worksheet prices it with the Tax Table, which can exceed exact
  brackets by a few dollars, so that bound waits on AA121 there. There is no
  lower bound at `compute_qdcg_tax`: the worksheet taxes a 25% or 28% gain at
  the ordinary rate where that is lower, so $125 of box 2b gain on $48,350 of
  ordinary income (TY2025 single) is $3.75 below it. A draft keeps the
  0/15/20% figure with the banner. Tests: a REIT distribution with box 2b
  (held, then closed with a worksheet), a box 2d case, a recorded tax above the
  regular tax at $100,000 or more (refused), the $48,350 case (accepted), and
  an ordinary long-term control (no item).
  **Step 2, compute the worksheet.** Carry the 28% and unrecaptured §1250
  amounts as Schedule D lines 18 and 19 in `result_template.json`'s
  `schedule_d`, net them as the covered-year Schedule D instructions direct, and
  compute the Schedule D Tax Worksheet in `tax_computations` with the
  investment-interest election (Form 4952 line 4g, not modeled today) as an
  input; the item then closes as `computed`. Tests: both categories together,
  short-term loss netting that changes the worksheet inputs, and the ordinary
  long-term control. Found by the 2026-11-18 external review. [IRS Schedule D
  instructions, TY2025](https://www.irs.gov/pub/irs-prior/i1040sd--2025.pdf)

- **AA123 (P2, M): Screen the federal and California refundable credits (Q16).** AA1
  discloses the federal EIC, CalEITC, Young Child and Foster Youth credits rather
  than computing them, and deliberately never blocks, because blocking a "maybe"
  teaches filers to answer no (`capabilities.py`). Two holes remain. The profile
  template defaults `may_qualify_for_eic` to false and the tie-out reads that as the
  taxpayer's answer, so a profile nobody asked produces no advisory at all
  (reproduced 2026-11-18). And nothing records that an eligible filer's worksheet
  was worked. Per Q16, the engine screens out filers above the covered year's
  FETCHED limits, California's own for its credits since a federal no does not rule
  them out, and every filer the screen cannot rule out needs an AA136 outcome,
  claimed with its worksheet or worked and ineligible, before final export.
  High-income filers never see the question. Test an eligible and an ineligible
  household, one with different federal and California outcomes, a never-asked
  profile, and an unfetched limit, which leaves the item open rather than screened.
  Found by the 2026-11-18 external review. [IRS Publication 1040, TY2025 Tax and EIC
  Tables](https://www.irs.gov/pub/irs-prior/p1040--2025.pdf) · [FTB 3514
  instructions, 2025](https://www.ftb.ca.gov/forms/2025/2025-3514-booklet.html)

- **AA124 (P1, M): Require Form 1116 evidence or a verified simplified
  election.** `compute_ftc_limitation` in
  `agents/lib/tax_computations/federal/international.py` divides foreign-source
  income by the figure it is given, AGI by its docstring, with no
  qualified-dividend adjustment, so its error runs in either direction, and
  nothing in the engine calls it: the agent
  fills `form_1116_ftc` and Schedule 3 line 1 (`line_20_schedule3_line8`'s
  component) by hand, under keys that do not match the function's output. The
  simplified election lives only in `tax-compute/references/tax-and-credits.md`,
  which compares the credit to `foreign_tax_credit_simplified` and checks passive
  sources only. International index funds put brokerage holders past the
  simplified limit. Two steps, each one pull request.
  **Step 1, the simplified election and the source amount.** Record foreign tax
  paid from 1099-DIV box 7 and 1099-INT box 6 as a ledger category
  (`tax-parse` and `tie_out.CATEGORY_TARGETS`), so the tie-out catches a dropped
  amount. Add an engine check for §904(j): all foreign-source income passive
  and reported on a payee statement, foreign tax paid (not the credit) within
  $300, or $600 on a joint return only (`applicable_law` reads `max_mfj` for a
  surviving spouse today). The election itself then bars carrying any foreign
  tax into or out of that year, so refuse a carryover applied to an election
  year and a carryforward created by one; carryovers between other years are
  unaffected. A credit without Form 1116 is refused unless every condition
  holds. Tests: a qualifying brokerage statement, a case within the dollar limit
  with no payee statement, a surviving spouse at $450, and an election year
  with a prior carryover available (election allowed, carryover not applied).
  **Step 2, Form 1116.** Require the covered year's Form 1116 inputs: line 18
  from its worksheet (for TY2025, Form 1040 line 11b minus line 14 plus
  Schedule 1-A line 37, before the qualified-dividend and capital-gain
  adjustments; leaving out the Schedule 1-A add-back over-states the
  limitation), each
  category separately, and carryovers by category and origin year. One
  carryover path: `rollover._transform_ftc_unused` writes a single total while
  `prior_return._ftc_baskets` imports baskets by category and origin year, and
  nothing in compute reads either. A reviewed external Form 1116 is acceptable
  where the engine cannot compute a line. Tests: a multi-category case with a
  carryover, the credit reaching Schedule 3 line 1. Found by the 2026-11-18
  external review. [IRS Form 1116 instructions,
  TY2025](https://www.irs.gov/pub/irs-prior/i1116--2025.pdf)

- **AA125 (P3, M): Compare eligible elections on the full return.** The summed
  "Total potential savings" line was removed in #130. Federal standard versus
  itemized is already chosen jointly in `compute_schedule_a`, and California's
  election is Q7's. Compare education credits and 529 allocations against one
  household baseline using distinct eligible expenses, showing federal and
  California liability, payments, penalties and settlement. Test that a combined
  benefit comes from recomputing the elections together and that overlapping
  expenses or exclusive elections cannot inflate it. Found by the 2026-11-18
  external review. [FTB Form 540 instructions,
  2025](https://www.ftb.ca.gov/forms/2025/2025-540-instructions.html) · [IRS
  Publication 970, TY2025](https://www.irs.gov/pub/irs-prior/p970--2025.pdf)

- **AA126 (P2, M): Price the single-year Roth ladder with the right Medicare year
  and income.** Reproduced 2026-11-18: adding $60,000 of tax-exempt interest left
  every IRMAA crossing unchanged. The ladder compares AGI with the conversion year's
  tier table, while `roth_plan.py` already uses the premium year two years later and
  Medicare MAGI with its addbacks and enrollee months. Reuse that logic, and show
  federal tax, California tax and Medicare premium changes separately; California is
  priced for reviewed years and gated for 2026 by AA84. Where applicable, include
  the premium tax credit effect from an AA120 reconciliation before ranking amounts.
  An unpublished premium-year table reads "not modeled" (Q8). Test a tier change
  caused by tax-exempt interest, a different premium-year table, and unpublished
  law. Found by the 2026-11-18 external review. [SSA POMS HI 01101.010, Medicare
  MAGI and lookback](https://secure.ssa.gov/POMS.NSF/LNX/0601101010)

- **AA127 (P1, M): Require transaction coverage for harvest recommendations.**
  Step 1 shipped in #128: no lot clears §1091 until a `transaction_coverage`
  statement covers the whole file. Step 2 shipped in #146: a purchase of another
  ticker inside the window needs a reviewed `wash_sale_classifications`
  decision, and a lot whose replacement purchase another lot also matched is
  held when that lot is fully blocked, held back or a different ticker.
  **Step 2b, before 2026-12-31 (tier 0), one pull request:** four gaps #146's
  review reproduced in `agents/lib/loss_harvest.py`, each over-stating the plan.
  Hold or refuse; never guess.
  1. *A lot with no `tax_advantaged` flag is taxable.* `parse_holdings` reads it
     with `_optional_bool(..., default=False)`, so a Roth IRA lot with no flag
     and a $1,000 loss enters the plan and `fed_savings`, and also fills the
     own-purchase budget in `_apply_wash_sale_prorate`, so a same-day IRA buy in
     that account recorded with a null `account_type` stops replacing taxable
     lots (Rev. Rul. 2008-5); with `account_type: "roth_ira"` the conflict
     check already refuses, so write the test with null. Require the
     boolean on every lot, the error naming the lot id and the field. Rewrite
     `test_parse_holdings_defaults_tax_advantaged_to_false`, which pins the
     default, as the refusal, and add the field where a test file or
     `tests/fixtures/harvest/holdings.json` omits it.
  2. *A lot has no owner.* On a separate return a spouse's losses are the
     spouse's, but `HoldingLot` has no owner (only buys carry
     `owner_is_taxpayer`), and SKILL.md tells the filer to list the spouse's
     accounts. An MFS filer listing `{"id": "S1", "account": "Spouse Schwab",
     "cost_basis": 5000, "current_value": 4000, ...}` has the $1,000 counted as
     their own. When `build_harvest`'s filing status is MFS, require `owner`
     (`"taxpayer"` or `"spouse"`) on every lot, refusing a missing or other
     value. A spouse's lot is never in the plan or the savings and earns no
     own-purchase exclusion; the spouse's purchases still count as replacements,
     as Publication 550 says. Report the spouse's losses as their own total, the
     way `tax_advantaged_loss` is reported. Other statuses may omit `owner`.
  3. *A purchase with no quantity is taken to be the losing lots at its key.*
     In `_apply_wash_sale_prorate`, a quantity-less buy whose date, account and
     ticker match a losing lot claims the whole own-purchase budget. Lots X
     (VTI, "Brokerage", 2025-03-01, 50 shares, $3,000 loss), L1 (same account,
     2026-12-01, 10 shares at a loss) and L2 (same account and date, 90 shares at
     a gain) with one quantity-less VTI buy on 2026-12-01: X shows clear with
     $3,000, while the same buy with `quantity: 100` adds 90 replacement shares
     and fully blocks X. When any other lot of that ticker (at a gain, or a
     spouse's) shares the buy's date and account, the quantity-less buy is an
     unknown-quantity replacement, as one matching no lot already is. Keep
     `test_inf_qty_buy_matching_lot_acquisition_assumed_self` (no other lot) green.
  4. *Same-day lots are matched in file order.* `losing_lots` sorts by
     `acquired` alone. VTI lot A (2024-01-01, 10 shares, $10 loss) and lot B
     (same date, another account, 10 shares, $1,000 loss) with one 5-share VTI
     buy: [A, B] makes $1,005 eligible and [B, A] $510. Treas. Reg. §1.1091-1(b)
     applies §1091 in the order of sale, and the plan does not fix that order.
     Among lots acquired the same day, match the one with the larger loss per
     share first, which disallows the most and so is never the over-statement,
     and have the report state the sale order its figures assume. Test both file
     orders giving $510.
  Done when each rule turns its test red when reverted, the four cases above
  are tests, and `tax-harvest` SKILL.md describes `tax_advantaged` as required
  and `owner` on a separate return. `docs/contributing/test-invariants.md` and
  `skill-catalog.md` change where they describe the holdings file.
  **Step 3 (tier 5):** name the date the post-sale window closes, and break out
  current-year, carryforward and replacement-basis effects. The scan assumes
  the sale happens on `as_of`; an omitted `as_of` and a sale later in December
  move a listed buy into the window.
  Found by the 2026-11-18 external review and #146's review.
  [IRS Publication 550, TY2025](https://www.irs.gov/pub/irs-prior/p550--2025.pdf) ·
  [Treas. Reg. §1.1091-1](https://www.ecfr.gov/current/title-26/chapter-I/subchapter-A/part-1/subject-group-ECFR4a3aa9fd5a8fa4c/section-1.1091-1)

- **AA133 (P1, M): Hold the final package until each gift is substantiated
  (Q17).** One pull request. `compute_schedule_a`
  (`agents/lib/tax_computations/__init__.py`) takes cash and noncash totals and
  one boolean, `charitable_over_250_substantiated`, which no skill passes, so
  every itemizer with gifts gets the "SUBSTANTIATION UNKNOWN" note and a
  deduction in full, and "noncash gifts over $500 require Form 8283 (not
  modeled)" is a note only. Per Q17, add `input.charitable_substantiation` to
  `material_evidence._FILING_INPUT_LABELS` and `_required_filing_inputs`,
  required when `schedule_a.use_itemized` is true and the year's gifts are
  positive. `filing_input_issues` then refuses `not_applicable` while a gift is
  deducted and asks for the evidence as sources: the contemporaneous written
  acknowledgment of each gift of $250 or more, and for noncash gifts over $5,000
  the qualified appraisal the covered-year Form 8283 instructions require,
  except for publicly traded securities, which need none at any amount
  (§170(f)(11)(A)(ii)(I)). An
  open item holds the final package and keeps the draft banner, through the
  existing reasons; nothing removes a gift. Form 8283 has no result section, so
  `_FEDERAL_SEQUENCE` cannot list it: add it in
  `filing_package._derived_schedules`, and to the attachment checklist, when
  noncash gifts exceed $500. A carryover alone needs no current-year
  acknowledgment; its vintage keeps the `source_ref` the ledger already
  requires. Tests: an unsupported cash gift (draft only), appreciated stock that
  needs Form 8283 (listed), listed stock over $5,000 (no appraisal asked),
  several similar noncash items over $5,000 together (appraisal asked), a
  carryover with no current gifts (no item), and a standard-deduction filer (no
  item). Done when each rule turns a test red when reverted, and
  `tax-compute/references/income-and-agi.md` replaces its "record the answer in
  answers.md" step with the outcome. Found by the 2026-11-18 external review.
  [IRS Schedule A instructions,
  TY2025](https://www.irs.gov/pub/irs-prior/i1040sca--2025.pdf) · [IRS Form 8283
  instructions, Rev. 12/2025](https://www.irs.gov/pub/irs-prior/i8283--2025.pdf)

- **AA134 (P1, M): Require a reviewed asset inventory for Form 8938.** Two
  steps, each one pull request.
  **Step 1 (XS), the missing value.** `check_form_8938_required` in
  `agents/lib/tax_computations/federal/international.py` reads
  `acct.get("max_value_usd", 0)` and `acct.get("year_end_value_usd", 0)`, so
  `[{"year_end_value_usd": 40000}]` for a single filer returns `required: False`
  however high the account ran during the year, and `_coerce_numeric` lets
  `"nan"` and a negative value through. Refuse an account missing either value,
  naming the field and the account, and a non-finite or negative value
  (AGENTS.md §8). `filing_package` lists Form 8938 when the section is present
  rather than when `form_8938_fatca.required` is true: list it on `required`,
  and refuse a `required: true` block without its accounts. Tests in
  `TestCheckForm8938Required` (`tests/test_tax_computations.py`) and
  `tests/test_filing_package.py`.
  **Step 2, the inventory.** Each specified asset records its owner
  (`taxpayer`, `spouse` or `joint`), year-end and maximum values with the
  valuation method and source, and the covered-year rules apply: on a joint
  return a jointly owned asset counts once; on a separate return the Form 8938
  instructions count half of an asset owned jointly with a spouse toward the
  threshold and report its whole value. Give `form_8938_fatca.accounts` that
  row shape and replace every "realistic simultaneous peak" sentence, in
  `tax-fbar/SKILL.md` (four places), `tax-compute/references/
  international-and-optimization.md` and `docs/contributing/tax-gotchas.md`,
  with the instructions' valuation rule. Tests: joint filing with a joint asset,
  the separately filing spouse example, and a required form reaching the
  package. P1 for filers with foreign accounts. Found by the 2026-11-18 external
  review. [IRS Form 8938 instructions, Rev.
  11/2021](https://www.irs.gov/pub/irs-prior/i8938--2021.pdf) · [IRB 2014-53,
  T.D. 9706 and its joint-ownership example](https://www.irs.gov/irb/2014-53_IRB)

- **AA135 (P3, M): Replace the overall audit-risk rating with evidence-based
  findings.** The tax-review report rates an overall risk level of low, medium or
  high and flags heuristic thresholds as examination signals, though its
  information-return matching, round-number provenance and substantiation checklist
  are already evidence-based. Keep unusual figures as prompts for questions; for
  each material finding record the return line, rule, evidence reviewed, open
  discrepancy and dollar effect where calculable; bind the workpaper to the reviewed
  return version so a change reopens the affected findings. Form 8867 is a paid
  preparer's due-diligence form and stays out of scope for a self-preparer toolkit.
  Test that a substantiated unusual amount clears and a material unsupported
  position stays open. Found by the 2026-11-18 external review. [IRS
  audits](https://www.irs.gov/businesses/small-businesses-self-employed/irs-audits)

- **AA140 (P2, M): Close what the reviews of #147-#155 left that over-states a
  return.** Three steps, each one pull request, in order of reach.
  **Step 1, employer identity for excess Social Security.** `_employer_groups`
  in `agents/lib/tie_out.py` joins W-2s whose `_payer_tokens` are subsets of
  one another, so "Acme Corp" and "Acme Corporation" read as two employers and
  that employer's own over-withholding, which the employer refunds (Topic 608),
  is accepted on Schedule 3 line 11. Record the employer's EIN (W-2 box b) as
  `source_form.employer_ein` on each W-2 record (`tax-parse/references/
  form-extraction.md`, and `validate_form` in
  `agents/lib/tax_computations/federal/source_reconciliation.py`, whose closed
  key set refuses a new field until it is added; an EIN is a public business
  identifier, kept whole, as the profile template's payer comment says), and
  group by it in `_employer_groups`, validating the `NN-NNNNNNN` shape. A
  person's positive credit, claimed or computed, needs an EIN on each of their
  Social Security W-2s; without one, refuse rather than fall back to names. Keep
  the name rule only for the zero-credit path. Tests in `tests/test_tie_out.py`:
  two "Acme" spellings with one EIN over the wage base (no credit), two EINs
  (credit), a missing EIN with a positive line 11 (refused), and a railroad
  employee unchanged. The PII gate reads bare nine-digit runs, so write test
  EINs hyphenated or build them at runtime.
  **Step 2, a 1099 inside a consolidated statement.** In that module's ledger
  review (the `included_in` branch beside
  "is reported within consolidated document"), a skipped 1099-K or 1099-NEC
  linked to a consolidated 1099 is accepted whatever the container records, so
  a container with none of its amounts leaves the business gross off Schedule C
  line 1. Accept the link only when the container's key boxes carry
  `self_employment_gross` at least the included form's amount (its Box 1a or
  NEC box 1, which the skipped record must then carry), and for a 1099-K the
  Box 1a routing #152 requires; refuse otherwise, naming both documents. Test a
  container without the amount (refused), with it (passes and the disclosure
  still prints), and a 1099-INT inside a consolidated 1099 (unchanged).
  **Step 3, payments.** In `tax_computations/federal/payments.py`,
  `reviewed_payments` asks for `distinct_payment_confirmed` only when a
  balance-due row has an estimate's exact date and amount, so one remittance
  typed a day or a cent apart counts twice for Form 2210. Between January 1 and
  April 15 of the following year the two kinds differ only by their label:
  require the confirmation on every balance-due row when any estimate is dated
  in that period. On a return that is not joint, a positive Schedule 3 line 10
  has no allocation check: a joint Form 4868 payment may be split between the
  spouses' separate returns in any agreed amounts, but not claimed whole on
  both. Require `form_1040.schedule_3_line_10_joint_4868` (true or false) when
  line 10 is positive and the status is not MFJ, and with true require
  `schedule_3_line_10_allocation_confirmed`, as `_payment_row` does for a joint
  estimate. `return_builder._check_the_payments` and the result template carry
  the fields. Tests: an estimate on January 15 and a balance-due row on January
  16 with one cent's difference (refused, then confirmed), an MFS return with a
  joint Form 4868 and no allocation (refused), and an MFJ return (unchanged).
  `tax-compute/references/tax-and-credits.md` describes the new fields.
  Not queued: an excess typed into line 31 with line 11 at zero is checked only
  by the reviewed `input.payments` outcome.
  Each step is done when its rule turns a test red when reverted and AA115
  gains its negative case. Found by the reviews of #147, #151 and #152.
  [IRS Topic 608](https://www.irs.gov/taxtopics/tc608) ·
  [Form 4868, 2025](https://www.irs.gov/pub/irs-prior/f4868--2025.pdf)

### Recovered design follow-ups — reviewed against the engine on 2026-10-03

These are extensions, not unfinished portions of the shipped v1 deliveries.
The historical specs supply their origin; the scope stated here governs the
work. The split capital-loss carryforward, Form 1040 line 36 rollover, IRA and
AMT basis rollover, NIIT projection, full-status capital-gains cache and
CA/NY/NC withholding true-ups already exist and are not reopened.

All shipped: AA44, the last, in #69, #70 and #82.


### Y — distribution and product

- **Y1 (P1, XS): Enable Discussions on the public repository.**
  The repository is public, verified 2026-10-15; Discussions are still off.
  Every artifact the publish gate asked for is in place: R1's full-history audit
  (one adjudicated-benign finding, baseline recorded in
  `docs/changelog/epic-R-U.md`), R3's intake surface, R5's QUICKSTART with
  verified output, R2's standing controls, the pre-push PII scan, hashed locks,
  Apache-2.0. There is no remaining engineering precondition. Discussions are
  what R6's pinned thread needs, and that thread is the ONLY signal the design
  permits — the tool is local-first, so filing is structurally unobservable and
  the stated success criterion ("a stranger files a return with it") cannot be
  evaluated without it.

- **Y8 (P2, XS): Fetch the TY2026 figures still unpublished at season open.**
  The season-open runbook's "Verified 2026-10-12 (Y2)" record names three: the
  2027 §6621 quarters, the IRS yearly average exchange rates for 2026, due in
  January, and the state figures that publish with the year's full tables.
  With them goes the step AA14 and AA28 left for TY2026: when the NY State and
  City tables publish, compare each with its rate schedule and fetch
  `state.tax_table.table_bases` and `state.nyc_tax.tax_table.table_bases`, per
  the runbook's AA14 section. The freshness gate reads INCOMPLETE on the
  exchange rates and the state figures, so neither can be missed at compute
  time. It does not gate the quarters: Form 2210 carries the last cached rate
  through April 15, 2027 with only a note, and an extension's interest window
  reaches later quarters still. Fetch each through `set` as it publishes, the
  quarters included; gating them is AA58. Filed by the review that recorded Y2
  as shipped.

---

## Blocked on an owner ACTION or an outside publication, not a ruling

| Item | What it waits on |
|---|---|
| **Y1** | The owner enabling Discussions. Verified 2026-10-15: public, Discussions off, 0 stars. |
| **Y8** | The 2027 §6621 quarters, published one quarter at a time; the IRS's 2026 yearly average exchange rates, in January; and the TY2026 state tables, NY's included. |
| **AA48** | The IRS publishing the TY2026 Form 8949 instructions. The entry dates the last check, and the season-open runbook's Form 8949 step repeats it. |
| **AA83** | FTB publishing the 2026 Form 540 booklet and the year's Schedule CA and FTB 5805 instructions. The booklet's year-pinned URL returned 404 on 2026-10-24. |
| **AA84** | The remaining TY2026 planner inputs and contract review: tax-table ceiling/rows, AMT screening, exemption phaseout and itemized phaseout. The 2026-10-25 live review found brackets, standard deductions and exemption credit amounts on FTB's current Tax News page; the entry records the missing inputs. |
| **AA79** | The TY2026 IT-201 and IT-196 instructions (NY) and D-401 (NC), which print the household credit tables, the §615 limitation, the subtraction caps, the IT-201 tax table and NC's Schedule S lines and conformity date; the 2026 IT-2105 and NC estimator worksheet alone were live on 2026-10-25. |
| **AA80** | FTB publishing the 2026 Form 540NR booklet, whose renter's credit qualification record and line 61 the review reads. Its year-pinned URL returned 404 on 2026-10-24. |

## Declined, with the reasoning kept so it is not re-proposed

- **W7 — ADRs for multi-paragraph "why" comments. DECLINED.** AGENTS.md §9's size
  criterion governs the INSTRUCTION FILES, which are capped because Codex
  truncates them silently. Source comments have no such cap and no such reader —
  what they have is the next person editing the line, and moving the reasoning to
  `docs/adr/` removes it from exactly there. Every defect fixed in the 09-11 and
  09-12 sessions was found by reading one of those paragraphs beside the code it
  guards. If `preflight.py`'s ~890 comment lines are the concern, the question is
  whether they are TRUE and load-bearing, not where they live.

- **W8 — renumber the fractional skill steps. DECLINED.** The fractional numbers
  are what keeps every existing cross-reference valid. `tests/test_portability.py`
  pins every `/tax-safe-harbor step N` an engine module names against the skill's
  real headings; AGENTS.md §4 cites "Step 12.7" and "Step 0.7" by number; the
  changelog and merged PR bodies cite them too. A renumber breaks a reference in
  each for a tidier sequence, and the one thing fractional numbering buys — that
  step 6.5 is visibly a later insertion between 6 and 7 — is information, not
  accretion.

- **W9 — trim `_comment_*` keys in the profile template. DECLINED for
  `foreign_holdings_detail._comment` specifically.** Measured at 2,923 characters
  of 17,663. It is not prose: it is the field-by-field schema an agent must
  follow to fill the block, and several clauses are ALARMS — "the signed amount
  MUST be recorded because the tie-out treats a `disposed` holding with no finite
  value as an unverifiable event and BLOCKS the return"; "every taxable fund needs
  a UNIQUE one"; "carry BOTH `distribution_date` and `disposition_date` when both
  occur". The agent that fills this block reads this template. The smaller keys
  are a different question and could go.

- **Epic Q's v2 parking lot — DECLINED at portability v1 (PR #66), and one of
  the four is now live again.** Recorded here because the reasons are the kind
  that get re-proposed: **plugin/marketplace packaging** (a Codex
  `.codex-plugin/plugin.json` plus a repo marketplace, a `kimi.plugin.json`, and a
  Cursor plugin — the last blocked outright because the public marketplace
  requires a PUBLIC repository, so Y1 gates it); **MCP server packaging** (one
  `.mcp.json` already serves Claude and Kimi natively, and the Codex/Cursor config
  is documented in `docs/contributing/agents-compat.md` instead); **Windows
  symlink automation**, with the superpowers workflow docs staying Claude-only;
  and **a per-agent CI matrix**, declined because there are no vendor CLI licenses
  in CI, with a monthly "agent-surface drift" re-verification checklist as the
  substitute. AA2 restored the Python/Windows CI matrix in #189; that does not
  exercise vendor agent sessions or assign an owner to the drift checklist.
  Note this is a DIFFERENT ground from the Distribution table's
  refusal of a plugin bundle below, which turns on §1 forbidding a real return in
  a hosted sandbox rather than on packaging mechanics.

- **Y3 — package `agents/lib` as a distribution. DECLINED at Q11, with a re-open
  trigger.** At Q11 the repository was not public, so there was no consumer to
  install a distribution — the largest contract change in the backlog against
  zero demand signal, and it defeats the venv-refusal guard by design. It is
  public now, verified 2026-10-15, and still has no consumer outside a clone, so
  the trigger has not fired. **Re-open when a second consumer of the engine
  exists outside a clone.**

## Rulings

Recorded rather than asked mid-flight (owner instruction 2026-09-07: document the
question, skip that task, take the next). **None is re-opened by an implementer
without the owner.** The full question, what each answer would have cost, and the
reasoning are in git history and — for those that shipped — in the changelog entry
for the PR that carried them.

| # | Question | Ruling | Date |
|---|---|---|---|
| Q1 | V3's first golden: whose return shape? | The synthetic CA + federal MFJ return, hand-computed from the worksheets. | 2026-09-07 |
| Q2 | Where do V3's model-driven runs happen, on whose budget? | Locally, on demand, before a release tag. The workflow ships DISABLED; no API key becomes a repository secret. | 2026-09-07 |
| Q3 | Which agents form V3's cross-agent matrix? | One for the first slice, growing when a second is actually in use. Cost accepted: the five-agent portability promise stays unexercised. | 2026-09-07 |
| Q4 | V6: what counts as "isolation established"? | The implementer picks the strongest mechanism the installed build supports and writes down what it does NOT stop. If nothing clears the adversarial bar, ship the sequential fallback SAYING so. | 2026-09-07 |
| Q5 | Y2's uncertified reviewer definitions. | Write the procedure, touch nothing. The digest pin makes the state visible rather than silent. | 2026-09-07 |
| Q6 | Should the toolkit SHIP a font? | Keep the declaration; ship no font. A Cyrillic or CJK name prints as empty boxes and page 1 says so. Committing a binary would widen the PII gate. | 2026-09-07 |
| Q7 | Three sibling forms, three contracts for one itemize-vs-standard election. | Require `standard_deduction` on all three. "Breaks callers" measured at 22 test edits and 3 skill paragraphs, all in this repository. Shipped #156. | 2026-09-13 |
| Q8 | May an indexed figure that changes a return DEGRADE, when §2 says STOP? | Degrade only when BOTH hold — the output is ADVISORY and the fallback UNDER-states; otherwise STOP. Now AGENTS.md §2. Shipped #158. | 2026-09-13 |
| Q9 | Should `state/ny.py` VALUE-validate filing status? | Convert. The only input that starts being refused is input already taking the wrong branch. Shipped #156. | 2026-09-13 |
| Q10 | May a skill state a figure both statutorily NAMED and FETCHED? | Yes for §164(b)(7): state the two anchors, the 101% rule and the 2030 reversion, name the cache key, pin to an engine constant by test. §2's third enumerated exception. Shipped #158. | 2026-09-13 |
| Q11 | Y3: package `agents/lib` as a distribution? | No, with a re-open trigger. See Declined above. | 2026-09-13 |
| Q12 | W3: what runs the hook before the venv exists? | A bash shim of a few lines, and W3 proceeds. Fail-DEFERRED, not fail-open. | 2026-09-13 |
| Q13 | AA3: what does MEASURED COSTS record, when no sitting on this host can meet its idle-machine precondition? | Counts, ratios and relative ordering; no absolute seconds. The hook timeout and the per-test headroom budget remain as compared thresholds, and one cold pass is re-timed only when either changes. | 2026-09-21 |
| Q14 | AA19: may the cached SALT cap override a conflicting statutory amount? | Refuse mismatches. Accept the statutory amount at cents or its nearest whole dollar (half up); a law change requires updating the schedule. AGENTS.md §2 changes with the check; the auditor edit stays gated on Y2. | 2026-10-03 |
| Q15 | AA120: what does acknowledging a MANDATORY form allow? | A draft only. The final package needs the form's result on its Form 1040 path and the form on the attachment list; the same rule covers every mandatory form, and AGENTS.md §4 changes with AA120. | 2026-11-18 |
| Q16 | AA123: should the uncomputed EIC and California family credits block final export? | Screen, then ask. Filers above the covered year's fetched limits are screened out; every other filer needs a recorded outcome before the final package. A never-asked profile is not a no. | 2026-11-18 |
| Q17 | AA133: a gift of $250 or more with no written acknowledgment? | Hold the final package until the acknowledgment is recorded or the gift is removed; drafts keep it with a banner, and nothing removes it automatically. Form 8283 joins the package for noncash gifts over $500. | 2026-11-18 |
| Q18 | How is work ordered within California-first? | Filing quality, then after-tax outcome, then ease of use. A small planning fix whose value ends on a fixed date goes ahead of filing work that cannot be used before that date. | 2026-11-18 |
| Q19 | AA121: should filed returns use the federal Tax Table rather than exact brackets? | Yes, at P2. Line 16 and the capital-gain worksheet's ordinary-tax lines follow the table the form requires; planning keeps exact brackets. | 2026-11-20 |

Q13's "per-test headroom budget" is two constants, the per-test rate and the
absolute minimum under it. Which constants re-time a cold pass when they change
is stated once, in `skill-invariants.sh`'s MEASURED COSTS block; the row above
keeps the owner's words.

---

## Distribution

Carried forward from the 2026-08-12 review, because it is the live plan and not a
finding. **The landing point is fixed by AGENTS.md §1, not chosen:** "never run a
real return on a vendor cloud VM" applies to every hosted channel including a chat
sandbox, so **every hosted channel is advisory-only and PII-free; filing is always
local.** The funnel has one shape and `docs/QUICKSTART.md` is its landing page.

| Option | Verdict |
|---|---|
| **GitHub-native** — publish, add topics, let the aggregators that index public `SKILL.md` repos find it | **Foundation.** It is Y1 anyway. |
| **Skill-upload channel** — the advisory skill in Claude.ai and the plugin marketplaces | **Yes.** Reaches non-developers; the chat→clone jump is the weak link. |
| **Plugin bundle of the whole toolkit** | **No.** §1 blocks real returns in a hosted sandbox, so it only serves local-agent users, who can already clone. |
| **Seasonal posts** timed to Oct 15 / Jan 15 / season open | **Yes.** Highest-intent traffic available, no infrastructure; one-shot, so it needs Y1 first. |

**Competitive context.** `filedcom/opentax` and `habutax` already occupy "an
open-source tax engine exists". The differentiation is not the engine — it is the
integrity chain (parse ledger → tie-out gate → capability gate → source-pinned law
→ limitations manifest) and running natively across five agents. The README must
say that in its first screen.

---

## Shipped

The index. Narrative lives in `docs/changelog/` — one file per epic, listed in
`docs/changelog/README.md`. Pull-request numbers restarted at #1 when the
repository was recreated on 2026-10-10, so one number can name two pull
requests. A row writes the private repository's (`tax-prep-skills-private`)
as `old #N` and this repository's as `#N`; the test checks each against its
own side of the recreation, commit `0a05d0c`, whose history holds every old
merge.

| Item | PRs | Remaining scope / follow-ups |
|---|---|---|
| AA23 | old #203, old #204, old #205, old #206, old #217 | — |
| V0 | old #85 | — |
| V1 | old #109, old #110, old #111 | — |
| V2 | old #108 | — |
| V3 | old #97, old #98, old #107, old #116, old #117, old #141 | — |
| V4 | old #87 | the auditor's `_provenance` check shipped with Y2 in #7 |
| V5 | old #103, old #104 | — |
| V6 | old #99, old #100, old #101, old #106 | — |
| V7 | old #115 | needs `TAX_WORKSPACE` set, or it isolates nothing |
| V8 | old #91 | — |
| V9 | old #92 | — |
| V10 | old #93, old #102, old #139, old #140 | — |
| V11 | old #95 | — |
| W1 | old #90 | — |
| W2 | old #148, old #149, old #150, old #151, old #152 | — |
| W3 | old #163, old #164, old #230 | Single invariant-pass runtime; cheap Bash change boundary and red memory preserved. |
| W4 | old #90, old #118 | the MEASURED COSTS seconds were retired by Q13 rather than re-measured |
| W5 | old #90, old #166 | Floor-reading follow-up shipped as W5a; its remaining comparisons are listed below. |
| W5a | old #191 | the matrix legs, the tool targets, `MIN_PYTHON` and the prose still state the floor, compared rather than read; the lock headers are uv's record and stay |
| W6 | old #105 | — |
| W10 | old #208, old #209, old #210, old #211, old #227 | Registry and wiring; generic year-bound adjustment workpapers and explicit local-tax routing. |
| W11 | old #226 | Shared state true-up specification; public report behavior retained and NY timing-note crash fixed. |
| W12 | old #225 | Test-owned hook input declarations and independent read/import audit. |
| X1 | old #89 | the auditor's keying note was corrected with Y2 in #7 |
| X2 | old #86 | — |
| X3 | old #88 | — |
| X4 | old #110, old #142 | — |
| X5 | old #143, old #144, old #155 | Wrapped and paired limit detection completed under X9. |
| X6 | old #154 | The statutory SALT step schedule remains the fallback; #216 requires cached amounts to agree at cents or the nearest whole dollar (half up), before phasedown. |
| X7 | old #218 | future-sale tax and chained wash-sale repricing remain outside the replacement basis plan |
| X8 | old #159 | Engine entry-point status validation: direct checks or declared delegation probed at both ends |
| X9 | old #228 | Wrapped and paired amounts, Markdown block boundaries, code/CLI handling and shared figure gates. |
| X10 | old #224 | Roth catch-up split, confirmed HSA eligibility and fetched per-account state conformity. |
| Y2 | #7 | The figures still unpublished on 2026-10-12 are Y8, and Y2's review filed AA52-AA54. |
| Y4 | old #112 | — |
| Y5 | old #113 | — |
| Y6 | old #114, old #119, old #165 | — |
| Y7 | old #120 | no Windows coverage — the Windows job runs two modules |
| Z4 | old #219 | TY2025 full-year D-400; other form years and farmer/annualized NC-40 worksheets remain manual |
| Z5 | old #220 | TY2025 excise; separate households and special federal normalization remain out of scope. Harvest pricing shipped as Z6. |
| Z6 | old #223 | WA excise harvest pricing, crypto domicile workpapers and separate WFTC reminder. |
| Z8 | old #221 | TY2025 NY source allocations and NYC IT-360.1; other-state returns/credits, MFS, mixed spouses, IT-230 ratios and IT-203 Yonkers remain manual/refused; NY forecasts are skipped |
| Z1 | old #123, old #124-old #137 | IT-201 remains full-year NY resident only. Reviewed TY2025 IT-203 and NYC IT-360.1 plans shipped as Z8; statutory second residency and other taxable-state returns retain their gates. AA13 supplies IT-201 line 47a, and AA12 supplies whole-year Yonkers line 55 or non-joint nonresident line 56. IT-201 part-year City/Yonkers lines 50/57, special condition Y1, joint Y-203, IT-196 worksheets, other taxes, refundable-credit amounts, use tax and city-level day counts retain manual work or refusals on `meta.limitations`. |
| Z2 | old #130 | City taxable income, the City household credit, the MCTMT and the line-58 caps; the City resident tax, line 47a, came later with AA13, and Yonkers lines 55-56 with AA12 |
| Z3 | old #131 | — |
| Z7 | old #121 | domicile as a GATE is deliberately not modeled |
| AA1 | old #171 | the EIC is DISCLOSED, not computed — out of persona by the 2026-07-12 ruling |
| AA3 | old #172 | Whole-suite count removed by AA8, pass timing added by AA9, and review leftovers addressed by AA10. The timing lint's accepted limits remain listed under AA10. |
| AA7 | old #174 | IT-201 remains resident-only; IT-203 uses the declared plan in #221, while other taxable-state obligations retain their gates |
| AA13 | old #175 | IT-201 part-year City line 50 remains manual/refused; reviewed TY2025 IT-203 NYC IT-360.1 plans shipped as Z8. |
| AA11 | old #175 | — |
| AA12 | old #176 | Part-year Yonkers, special condition Y1 and joint Y-203 remain refused/manual. Line-58 guards and household-method checks shipped as AA15, AA29, AA30 and AA32. |
| AA6 | old #178 | three blocks cannot be sized from public data (statutory residency in a second state, a PFIC event, out-of-state income other than a commute); the figures are tax year 2023 and need a yearly refresh |
| AA16 | old #179 | The compute step forms modified AGI. AA23 re-prices the SALT phasedown from recorded inputs; legacy or unsupported planning inputs retain held-baseline caveats. PTET still holds Schedule A. |
| AA20 | old #180 | AA23 re-prices supported Schedule A inputs, including medical/SALT limits, confirmed charitable caps, elections and §68. Legacy or unsupported inputs stay held with caveats or refuse where required. |
| AA21 | old #181 | Complete Schedule A forecasts re-price under AA23; the legacy declared-total path retains its warning. Non-itemizer §170(p) support shipped as AA22. |
| AA22 | old #182 | AA23 owns the itemize-or-standard election. Gifts remain declared inputs; an unfetched required cap refuses until its block is fetched. The note's acknowledgment-threshold policy remains the owner's call. |
| AA25 | old #185 | Exposure-scan dependence is verified under AA26; its remaining test limits are listed below. |
| AA4 | old #184 | Walk shape remains pinned; AA24 added comparison with the configured timeout. |
| AA5 | old #186 | — |
| AA8 | old #186 | the suite's size is stated nowhere, and the scale row's "about twice" a cold pass is a reader's to keep, not a check's |
| AA17 | old #188 | a part-year return across the border is blocked, not modeled, and so is a non-citizen's dual-status federal year |
| AA14 | old #189 | a cache without `table_bases` gets a note, and the row can be a dollar off either way; the TY2026 check is Y8 |
| AA27 | old #189 | — |
| AA28 | old #190 | the TY2026 `table_bases` check is Y8 |
| AA29 | old #192 | AA30 moved household screening to Step 0.7; AA32 requires computation methods beside flagged household values. A typed method is not a receipt-bound proof. |
| AA18 | old #193 | an acknowledged territory return adds the excluded income back by hand; a territory mailing address on a stateside resident blocks too, the degrade direction |
| AA31 | old #194 | — |
| AA30 | old #195 | AA32 added household computation methods; an acknowledged unsupported household's City or Yonkers tax remains manual. Supported Z8 plans use their variant guards. |
| AA35 | old #238 | Verified federal/CA charitable vintage ledgers through setup, computation and rollover; missing worksheets require explicit resolution. |
| AA36 | old #240 | Local paystub intake reconciles confirmed jobs into projection YTD with source and period evidence, masked extraction and secured publication. |
| AA37 | old #241 | Confirmed regular Social Security worksheets reprice taxable benefits across scenario stacks and Roth rungs; the original income frame and covered-year evidence remain bound. |
| AA38 | old #242 | Reviewed full-year TY2025 CA/NY/NC state/local liability reprices independently in scenarios and Roth sweeps; missing worksheets, allocations and unreviewed form years stay named limitations. |
| AA39 | old #243 | Independent primary/spouse Form 8606 worksheets bind owned IRA balances and source allocations, preserve separate closing basis through rollover, and label individual reports and Roth planning. |
| AA40 | old #244 | Account-specific crypto FIFO moves reviewed original lot slices through owned transfer links; missing inventory/evidence blocks completion and untagged universal imports from 2025 are diagnostic-only. |
| AA41 | #6 | Reviewed years TY2023-2025 only: the TY2026 boxes are AA48, the cases that block for good AA47, a per-row broker-proceeds tie AA49, a narrower event map AA50, the 1099-DA boxes it never names AA59. |
| AA19 | old #216, #7 | — |
| AA51 | #7 | A PDF the auditor's own fetch saves outside the export stays unread by decision, so such a figure goes UNVERIFIED; the export and the engine still answer four questions differently, filed as AA52. |
| AA55 | #10 | Input boxes only: TY2026's Form 8949 boxes are AA48, and the 1099-DA boxes the import never names are AA59. |
| AA59 | #12 | CA and NY 1099 withholding blocks the tie-out until AA60 routes it; the TY2023-2024 Form 1099-B boxes are AA61, specific identification AA62, local tax no return here claims AA63. |
| AA77 | #15 | Schedule P Part II only: Part III's credit limits start from Form 540 line 35, after the exemption credits, and are AA72's. |
| AA75 | #16 | — |
| AA68 | #17 | The CA family credits (lines 75-77), FTB 3506 and the renter's credit carry manual notes rather than computing. |
| AA60 | #18 | Withholding for a state no return here prepares stays an advisory other_state_withholding record with a state-coded breakdown. |
| AA69 | #19 | Reviewed TY2025 form only; a claimable child's Schedule P line 22 exemption limit refuses rather than computing. |
| AA70 | #20 | A payment entered twice under two payment ids is guarded only by the ca_verified reconciliation. |
| AA71 | #21 | The section 17145(d) fund-of-funds exception is a review step, not an input; the taxable state refund is not cross-checked against Schedule 1. |
| AA65 | #22 | Prior-year unemployment repayments over $3,000 need their own worksheet. |
| AA67 | #23 | Boxes 2, 9, 10 and 15 refuse until they have routes; box 1 rents are AA73's. |
| AA66 | #24 | Gambling losses stay a manual worksheet; CA lottery winnings not on a W-2G use the manual Schedule CA path. |
| AA64 | #25 | The continuous-use December 2026 forms need re-reading at each season open for TY2027-2028. |
| AA52 | #26 | — |
| AA54 | #26 | — |
| AA76 | #26 | Regular method only; annualized, farmer and waiver cases need their own worksheet. |
| AA62 | #27 | A taxpayer who specifically identified lots prepares those lots and Form 8949 externally. |
| AA58 | #28 | — |
| AA57 | #29 | The cause of the dispatch-only period was never established. |
| AA72 | #30 | A disregarded single-member LLC owner's PTET credit refuses. |
| AA73 | #31 | Reviewed residential_rental_v1 scope only; vacation, commercial, royalty and disposition cases stay blocked. |
| AA74 | #32 | TY2025 only; the renter's credit six-month residency test is not checked. |
| AA43 | #33 | Reviewed TY2025 form year only: TY2026 mid-year runs refuse until that year is reviewed. |
| AA42 | #34 | The IRMAA dollar figure needs the premium-year table CMS publishes the November before, so a mid-year run normally reports MAGI only. |
| AA53 | #34 | — |
| AA81 | #40 | Rows sharing every field become questions in `answers.md`, not refusals; the review reads only the profile. |
| AA78 | #41 | Regular credit only: an AMT foreign tax credit, or a positive prior-year AMT credit, still needs the full Form 6251 worksheet. |
| AA82 | #42 | — |
| AA61 | #43 | — |
| AA85 | #49 | — |
| AA56 | #53 | — |
| AA46 | #50, #57 | Arbitrary recursive field diffs stay out, and a separate `/tax-diff` skill is demand-gated. |
| AA63 | #62 | Advisory only: no local return is computed, and a flat head tax is recorded nowhere. |
| AA50 | #63 | FIFO matching still prices every lot slice in exact fractions, the import's remaining cost. |
| AA47 | #44, #45, #46, #47, #60, #64, #66 | A first-sale NFT form (box 11c) and a date moved into a later year still refuse, and the collectible 28% rate stays unmodeled. |
| AA49 | #51, #67 | A stale import whose own figures still agree with each other can offset an equity over-merge of the same size; closing it needs each ledgered 1099-DA linked to its broker form's ID. |
| AA45 | #71 | A filing-year plan whose return records the owner's IRA flows or Dec 31 value refuses; future CA years wait on AA84; refundable credits are not repriced. |
| AA86 | #73 | — |
| AA89 | #76, #77, #80 | Unlettered columns retain the heading reach until a first entry establishes a chain; late first entries require a lettered or numbered caption. A nine-digit run just above an SSN label whose own box is blank is masked (a ZIP+4 above a 1099's empty recipient TIN, a FEIN typed without its hyphen above FTB 593's empty individual row), the safe direction. |
| AA88 | #81 | An overlay on a wrapped heading's lower row, or rotated off its row, printed whole until AA91 (#85). Digits interleaved with an identifier label leave with it, a misregistered value on the label included, the safe direction. |
| AA44 | #69, #70, #82 | No software-produced return PDF has been read yet: check the first real import's report table by table. Birth dates, residency, credit eligibility and continuation dependents stay interview questions; a Side 4 figure off its printed cents row is asked. |
| AA90 | #84 | Only IT-213's exact "SSN or ITIN" heading gets the long column reach. Forms 2848 and 8821 printed whole until AA93 (#88). |
| AA91 | #85 | What still prints is AA92. A rotated word touching a label goes whole only as nine digits running on together, and only its glyphs on the label's own line go otherwise, the safe direction. |
| AA93 | #88 | A bare nine-digit EIN in Form 2848's EIN field, beside its SSN fields, masks as an SSN, the safe direction; hyphenated it stays. |
| AA92 | #87 | Still printing, as on main: an SSN tracked past a word's gap, or with a space glyph drawn elsewhere between its digits in the stream; split beside a separator on a level page, which AA95 (#91) masks under a label only. A date joined to a number by position alone, at 90 degrees or more, goes as an SSN would, the safe direction. Form 1042-S's "Recipient's U.S. TIN" was no label until AA94 (#90). |
| AA94 | #90 | Only box 13i's dotted "U.S." before TIN labels; recipient GIIN and FTIN headings, and "Payer's TIN", stay unlabelled. |
| AA95 | #91 | Label-only: an unlabelled split prints whole, as does one page text breaks across lines on a steep page; one only position ties to its label printed whole until AA96 (#94). A split-shaped figure on the line under a label, or in a cell a label governs, masks as an SSN, the safe direction. The delimiter pattern, filename refusal, redactor and pre-commit gate keep the contiguous spellings. |
| AA97 | #93 | A value is a label's own only straight after it. One further along its row, or after other words ("SSN (last 4): XXX-XX-1234"), leaves the label governing the line under it, which masks an identifier-shaped figure there, the safe direction. A split straight after its label never counts as its value (AA95). The SSN under a label whose row prints an identifier-shaped figure far across it with nothing between, which page text joins straight after the label, printed whole until AA98 (#96). |
| AA96 | #94 | Split runs are positional targets only, never a sign that a label's value is there. Still printing: an unlabelled split. On a tilted page the overlay pass drops a split under a label whole, as on main, so its last four is lost. A split-shaped figure a label governs by position masks as an SSN, and one after a heading wrapped onto "number" makes the heading a label, which then masks nine-digit figures down its column, the safe direction. Two governed runs sharing digits mask whole, their neighbour's digits included. |
| AA99 | #97 | A run governed on neither the original page nor the cleaned one still prints, as on main. A figure whose digits match a run governed on the original page masks wherever it prints, the safe direction. |
| AA98 | #96 | A value is a label's own only within `_BESIDE` heights of the label or its dot leader. One further along its row leaves the label governing the line under it, which masks an identifier-shaped figure there, the safe direction. The overlay pass still decides which copies to keep as page text reads labels. |
| AA127 | #128, #146 | OPEN: no lot clears §1091 until `transaction_coverage` covers the window (#128), and a purchase of another ticker in the window needs a reviewed decision (#146); a lot whose purchase another lot also matched is held when that lot is not in the plan, held back or a different ticker. Step 2's residuals and step 3 remain in the Open entry. |
| AA125 | #130 | OPEN: the summed savings line is gone; comparing exclusive elections on one household baseline remains. |
| AA118 | #131 | OPEN: the README's privacy and support claims and the persona guide's NC and WA scope are corrected; one coverage source for setup and the support table remains. |
| AA136 | #132 | `material_evidence.json` binds each material outcome to its evidence and the receipt; deleting it together with the receipt drops items recorded only through the CLI, while required forms, W-2 boxes, sources and filing inputs are re-derived. A Marketplace filer with no advance credit is held to a draft until AA120. |
| AA128 | #133 | W-2 boxes 1 and 2 by word-boundary form match; a declared blank beside other W-2 amounts stays open. |
| AA116 | #134 | OPEN: the recovery path shipped; the cross-cell false mask remains, the first fix having printed identifiers. |
| AA112 | #135 | Per-page digests of a long PDF and the crypto JSON are AA139's. |
| AA130 | #136 | Related forms need distinct series ids; overlap and expected-form evidence are AA139's. |
| AA132 | #137 | Estimate date bounds and double entry are AA138's. |
| AA129 | #138 | Line 13 rounds to the cent by float, one cent from half-up at times; an SE deduction also typed into the remaining adjustments is not detectable; Form 8959 Part II is AA137's. |
| AA114 | #139 | Freshness against new legislation is the cache-level STALE review and the season-open audit, not a window on each figure; any `.gov` host is accepted for a state pin; the wage base is AA137's. |
| AA113 | #140 | Medicare wages above box 1 and excess Social Security against box 4 are AA137's. |
| AA115 | #141 | OPEN: three TY2025 households shipped; the remaining scenarios wait on their filing paths. |
| AA138 | #147 | An estimate after the fourth installment needs `late_credit_confirmed`, a payment matching another needs separate remittances, and line 10 sits inside line 31. A one-day or one-cent near miss and a separate return's line 10 allocation are AA140's; a typed 0 on line 10 is the user's statement; Form 2210 does not date an early Form 4868 payment; the evals score a recorded 0 against a golden null. |
| AA137 | #148, #149, #150, #151 | Form 8959 Part II from Schedule SE with sourced floors under lines 2, 3, 4 and 17; the Medicare screens read box 5, never below line 1a; boxes 4 and 6 are informational; excess Social Security is checked per person and employer, a railroad employee's share bounded. Part I with no Schedule SE earnings is the reviewed other-taxes outcome's, as on a wage-only return; Form 8959 tax also left in `remaining_other_taxes` beside the confirmation is not detectable; the projection still screens Medicare on line 1a; employer identity and an excess typed into line 31 alone are AA140's. |
| AA139 | #152, #153, #154, #155 | Every dollar of Box 1a is routed, with a reviewed personal routing on any business return; an overlap needs a separate bound evidence file; each received form clears one expected entry; a long PDF and each crypto CSV bind to one digest. A rental 1099-K has no route, books are required only for the NEC and K pair across the household, and the intake prompt lists a missing payer once; a 1099 inside a consolidated statement is AA140's; the crypto JSON's digests are self-reported. |
| AA109 | #124 | A header label reaches a value one cell off its column, past a blank row or under a header wrapped over two rows, and a dependents row one cell off its header masks in CSV and Excel. Whole values bound every crossing run; only routing, account and transit columns keep nine digits, and a comb in any other named column masks. A whole date stays whole only in an owned date column or under a flag label, so a count before a date beside any other label masks both, the safe direction. An unknown PDF glyph is a possible digit at any width, and only an IRS OMB number is metadata. What it left is AA110. |
| AA106 | #121 | Partially readable identifier fields refuse before extraction returns: overlaid on a label, crossed by letters or rows, or with some digits mapped. No glyph is decoded. An unknown glyph is a possible digit only within the widths of readable digits in its own font, so an unmapped dash or checkbox beside readable figures does not refuse the page. What it left is in AA109. |
| AA108 | #120 | A header label introduces a comb or dashed value crossing cells on the next row only, and a whole comb past a spacer or another cell on the label's row masks. A filled label stays open for variant spellings below it. A flag beside a whole date keeps both. What it left is AA109. |
| AA107 | #116, #117, #118 | A table row is read as one line for a comb its cells cut apart that a label on the row introduces, a dependent's comb after a whole one included. A label whose own value is a spaced comb still governs variant spellings below it. The gate bounds a candidate where its digits change script. What it left is AA108. |
| AA105 | #114 | The gate reads identifier spellings with one awk tokenizer that keeps byte spans for exact allowlisting, in linear time on macOS; a digit of another script cannot hide nine ASCII or fullwidth digits. The masker reads a comb with a soft hyphen in every gap. What it left is AA106 and AA107. |
| AA104 | #113 | Digest cleanup and both filename rules block when their tool fails, an early `grep -q` match included. The report keeps one row per real path and refusal. |
| AA103 | #110, #111 | The view reads the remaining spellings and adds to what is printed. Which drawn-over digits stay is decided from the printed labels alone. Nine unreadable glyphs of two kinds in a row refuse the read, and a heading in an unmapped font refuses too. The gate reads Unicode spellings, and ASCII lines take a pattern their size. What it left is AA104 and AA105. |
| AA102 | #107, #108 | The view of the text adds to what is printed and never replaces it; positional readers keep the printed text's labels and values. `(cid:N)` needs a label, and a wrapped value must open its line or follow its label. A stack under a label with each group alone on its line masks, even an amount with its cents on their own row. Spellings it left are AA103. |
| AA101 | #101 | Sixteen marks read as a dash; tight they mask with no label, set apart they need one. A real figure in that tight shape masks, as its hyphen spelling does, and a spaced figure-shaped filename refuses with a rename message. Spellings it left are AA102. |
| AA100 | #99 | Runs and text rules resolve together on the source; boundaries that disagree keep no digit. That can take digits beside an SSN: a number sharing eight of its digits, or the cents of an amount drawn against its box. An SSN with a typographic dash printed whole until AA101. |
| AA87 | #74 | Identifiers past its reaches (FTB 3532, Forms 1095-A/B/C, Schedule EIC, Form 8332, IT-201's dependents 6 and 7) are AA89, and an SSN drawn over its own label is AA88; a page that masks its first dependent and prints the next in full still leaks. |
| AA34 | old #207 | AA23 now supplies provisional QBI when re-pricing §68; legacy or unsupported inputs retain the held path and its caveats. |
| AA32 | old #202 | an agent that types a household line can type its method too; a receipt binding would close it |
| AA26 | old #201 | the scan call for a bounded command naming archive outside `archive/YYYY` has no test; the `git` and `rg` tests pin less than their names |
| AA10 | old #200 | outside MEASURED COSTS the timing rule reads whole-line comments only, and catches no duration in words |
| AA33 | old #199 | The permissions-hook absolute-timing rule reads whole-line comments only, as recorded under AA10; this is separate from X9's tax-figure prose lint. |
| AA24 | old #198 | it prices the walk only when the walk ran past a second, and reads its timeout from `settings.json` |
| AA9 | old #197 | it times the pytest pass, not the selector and scan around it |
| AA2 | old #189 | the first green full matrix on `main` ran on #189's merge |
| AA15 | old #188 | AA29 checks flagged household values and the line-58 floor even for a typed total; AA32 adds method markers, which remain unbound to a computation receipt. |
| Q7 | old #156 | — |
| Q9 | old #156 | — |
| Q8 | old #158 | — |
| Q10 | old #158 | — |
| hygiene | old #160, old #161, old #162, old #167 | Changes outside named backlog items; narrated in `docs/changelog/`. |

Epics I through U shipped earlier; see `docs/changelog/README.md` for the
per-epic index and `epic-R-U.md` for R1's audit baseline.
