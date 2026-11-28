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

**Shipped from the owner's 2026-11-27 queue:** AA127 step 2b (#158) and AA140
steps 1-3 (#159-#161), each fixed in review before merge. What review left that
can over-state a return became AA141.

**Shipped from the 2026-11-28 extension:** AA141 steps 1-4 (#164-#167), AA122
step 1 (#168), AA134 step 1 (#169), AA133 (#170) and AA124 steps 1-2 (#171,
#172), each fixed in review before merge. What review left that can over-state
a return is AA142, not yet queued.

**Next for implementation (owner, 2026-11-27; extended 2026-11-28):** AA122
step 2; AA134 step 2; AA121; AA120 steps 1-2; AA127 steps 3a, 3b and 4; AA123 step 1;
AA126; AA125 steps 1-2; AA117 steps 1-3; AA118 steps 1-2; AA119 steps 1-4;
AA135 steps 1-2; AA116 steps 1-2; AA110 steps 1-3; AA111 steps 1-4 — in that
order, each step a pull request of its own. Their entries name the code, the
behavior, the tests and when each is done. AA123 step 2 waits on an owner
action (blocked table); AA110, AA116 and AA127 step 4 follow the defaults of Q20-Q23 until
the owner rules (open questions, below the rulings). Each tier-1 fix adds its
AA115 negative case.

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
| **0. Before 2026-12-31: small, dated planning fixes** | **AA84** the day FTB's missing inputs publish. | Q18. TY2026 filing work cannot be used before that year's documents arrive. AA127 step 2b shipped in #158. AA84 is blocked (blocked table). |
| **1. Filing quality on every return, before TY2026 documents arrive** | **AA142** — two faults in one W-2 pair, Tier 1 withheld below the maximum and a remittance typed as both kinds, with the investor and itemizer gaps it lists. **AA115** gains one negative case per fix. | AA141 shipped in #164-#167; AA142 is what its review and those of #168-#172 left that can over-state a filed return, not yet written to the queue standard. W-2s arrive by January 31; corrected 1099s often in March. |
| **2. Filing quality: as FTB and the IRS publish** | **AA83** — the TY2026 Form 540. **AA80** — the TY2026 part-year return. **AA48** — the TY2026 Form 8949 boxes. **Y8** — the 2027 §6621 quarters, the 2026 exchange rates and the TY2026 state tables. | AA83 decides whether any CA resident can file a TY2026 return here. AA48 blocks every TY2026 crypto import. AA80 serves fewer filers. Since AA58, Form 2210 leaves a quarter the cache does not hold uncomputed, so Y8 is the reminder to fetch it. The blocked table dates each check. |
| **3. Filing quality for investors and itemizers** | **AA122** step 2 — the Schedule D Tax Worksheet. **AA134** step 2 — the asset inventory. **AA121** — the federal Tax Table. | In that order (owner, 2026-11-27). AA122 and AA134 step 1, AA133 and AA124 shipped in #168-#172. REIT funds and foreign accounts are what a brokerage household holds. AA121 (Q19) is a few dollars either way and reaches every filer whose ordinary income is under $100,000, investors with larger totals included. |
| **4. Filing quality: mandatory forms with low reach** | **AA120** — the advance-payment question, then a reviewed Form 8962 on its Form 1040 path. | P0 for a filer it reaches, but no current user is known to have Marketplace coverage. Since #132 such a filer is held to a draft; what is missing is the route to a final package. It moves to tier 1 the day such a filer appears. |
| **5. After-tax outcome** | **AA127** steps 3a, 3b and 4 — the sale date the plan assumes, the effects by year, then matching in the order of sale. **AA123** step 1 — California's screened family credits; step 2, the federal EIC, waits on a replay. **AA126** — Medicare costs in the single-year ladder. **AA125** — 1099-Q intake, then the election comparison. | Q16 sets AA123's shape, and AA125's comparison reads AA123's screen. AA126 reaches filers about 63 and older. |
| **6. Ease of use** | **AA118** — setup and the support table from one coverage source. **AA117** — one document inventory. **AA119** — one finalization command. **AA135** — evidence-based review. | AA117's Windows path defect blocks every Windows tie-out rather than passing a wrong return. |
| **7. Parser maintenance and other states** | **AA116** — a committed masking fuzz harness, then only the rules it shows safe. **AA110** — residual identifier layouts. **AA111** — parse cost. **AA79** — TY2026 NY and NC payment forecasts. | AA110 and AA111 measure identifier protection, lost figures and false refusals together. No current user files in New York or North Carolina; AA79 is read the day those instructions publish, since the last 2026 estimate is due 2027-01-15. |
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
  Re-run on main 2026-11-27 with synthetic tables through `mask_table` and
  synthetic PDFs from `tests/test_identifier_spellings.py::_cid_pdf` on the four
  `ROUTES` of `tests/test_unreadable_identifier_fields.py`, against the tree before #124 (`4819783`). Three steps, each one pull
  request; step 1 needs AA116 step 1's script, step 3 needs step 2's sweep and the
  owner's choice below.
  **The fragment is the masker's nine-digit rule, not a crossing defect.** Under
  `SSN | Spouse`, a six-digit fragment (`n n n - n n - n`) in the SSN column prints
  its six digits with a whole comb beside it, as written, but equally with that
  cell empty (on main and before #124), beside an `SSN` label in the row and under
  one in text: no route on main masks fewer than nine digits. Before #124 the comb
  hid it only by joining both into one run, a routing comb included. An
  identifier whose rest lies in the next cell masks in the dash, slash, comb and
  marked-comb spellings (0 of 640 cuts across Spouse, Note, Wages, Date and
  Routing number headings printed its first five). What does print is a
  whitespace-grouped identifier cut between the identifier column and a named one
  (`SSN | Spouse` over `nnn nn | nnnn`, every cut, all five headings): AA109's rule
  that new cross-cell grouping cannot borrow from a named column, the mirror of
  AA116's false mask, and pinned for financial captions by
  `test_a_grouped_amount_cannot_borrow_digits_across_named_columns`. No step until
  the owner rules.
  **Step 1, a comb beside an identifier column.** Under `SSN | Date` (likewise
  Note, Amount, Wages, Spouse), a marked comb (`n n n - n n - n n n n`) in the named
  column prints all nine digits whatever the SSN cell holds (a count, empty, None,
  "N/A"), and a spaced comb prints all nine when that cell is empty, None, "N/A" or
  "None" and masks when it holds a count. A row one cell longer than its header
  (`Name | SSN | Date` over `Pat | | <comb> | x`) prints the same, as did the tree
  before #124; a dashed value masks everywhere. In `_row_combs` a named column's
  whole value is read as a comb of its own (`reread`, built in `_mask_row_combs`
  from `separate` less `kept`) only when a candidate starting in the identifier
  column reaches it, and an empty cell starts none;
  `test_a_comb_beside_the_identifier_column_is_masked_unless_named_a_nine_digit_field`
  covers a count or a whole comb in the SSN cell beside a spaced comb, no more. Mask a whole `_ROW_COMBS` value
  (`_ID_SPACED`, `_ID_MARKED_COMB`) in a column either side of one the header
  governs, on every row it governs, under any heading `_table_other_field` accepts
  except `_NINE_DIGIT_FIELD`, keeping its last four, whatever the identifier cell
  holds. Bare nine digits and a spaced 3/2/4 there keep the documented rule: they
  widen only on a ragged row and only into unnamed columns, and a Wages column
  keeps a nine-digit figure
  (`test_an_empty_identifier_field_does_not_take_an_explicit_neighboring_column`).
  Measure with AA116's script against main: (L) gains comb and marked-comb
  identifiers beside empty, "N/A" and count cells; (I) gains nine single-digit
  flags in one Note cell beside an SSN column, which this rule masks (report the
  count; it is the safe direction). Zero new leaks, and no other (I) cell changes.
  Tests in `tests/test_identifier_shifted_tables.py`: `[["SSN", "Date"], ["",
  <marked comb>]]` gives `XXX-XX-` and its last four; the spaced comb beside "",
  None and "N/A"; a count beside a marked comb; the longer row; CSV and Excel
  through `tests/test_typographic_identifiers.py::_read`; the routing and account
  tests stay green. Done when each test is red with the rule reverted and
  `parse-boundary.md`'s "Under any other named heading" sentence says a comb beside
  an identifier column masks whether or not a run reaches it.
  **Step 2, the PDF decode sweep, no rule change.** The AA106 and AA109 sweeps are
  not in the repository. Commit `scripts/sweep_pdf_decoding.py`: it reads a
  directory of PDFs downloaded from the URLs it lists, never committed and never
  fetched by the script (the `tests/fixtures/prior_return/build_identifier_layouts.py`
  records, the TY2025 Form 1040, W-2 and Form 1040 instructions, and the three
  Utah State sample exports `parse-boundary.md` cites), on the four routes, under
  each decode failure those sweeps used (none, symbols unmapped, all punctuation
  unmapped, each digit unmapped), made by wrapping the `to_unichr` methods in
  `pdfminer.pdffont` to raise `PDFUnicodeNotDefined` for the chosen characters, so
  pdfminer prints `(cid:N)` as for a missing mapping. With `--baseline DIR` it
  prints, per mode, the pages each side refuses and the pages whose output
  changed, as counts and page numbers, never text. Done when it runs on main and
  the PR records main's refusal counts per mode.
  **Step 3, the W-2's shared OMB heading.** A page printing "Employee's SSN OMB
  No." and a value whose first four digits are 1545, its fifth glyph unmapped
  (`mapped=set(caption + value) - {value[4]}`, the one-item and two-item layouts of
  `test_omb_metadata_cannot_exempt_an_identifier_under_its_shared_ssn_heading`),
  reads on all four routes and prints eight of the nine digits as
  `nnnn<cid>nnnn`; the same page with a 912 area refuses on all four.
  `_printed_omb_glyphs` (`agents/lib/parse_utils.py`) takes
  `1545\s*\(cid:N\)\s*[0-9]{4}` after "OMB No." as metadata and leaves the shared
  heading's phrase out of the ownership check. Every copy of the TY2025 W-2 prints
  "OMB No. 1545-0029" one line below "a Employee's social security number", on the
  line box a's number is typed on. The owner picks one (Q20; the queue proceeds
  on A): (A) no OMB exemption
  under a caption sharing its phrase with an SSN heading, so such a W-2 page
  refuses when its hyphen is unmapped (the refusal AA106's review removed;
  re-export or a local OCR copy recovers it), while other forms keep the
  exemption; or (B) exempt only a control number in a table built from the
  official forms (W-2: 1545-0029), each entry pinned by a test to the blank PDF's
  text, which still prints eight digits of an SSN whose serial equals a listed
  suffix. Tests in `tests/test_unreadable_proportional_digits.py`: the
  reproduction refuses (A), or refuses with any suffix outside the table (B); an
  OMB caption on another form stays readable; and
  `test_omb_metadata_keeps_its_meaning_when_its_caption_period_is_unmapped` there,
  with `test_explicit_omb_metadata_is_not_an_identifier` in
  `tests/test_unreadable_identifier_fields.py`, move to a caption without an SSN
  heading (A) or keep 1545-0029 (B). Done when each rule turns its test red when
  reverted, the PR shows step 2's sweep before and after (refusals added only in
  the punctuation mode, on W-2 pages, under A), and `parse-boundary.md`'s OMB
  paragraph states the rule. Found by #124's review. [IRS Form W-2,
  2025](https://www.irs.gov/pub/irs-prior/fw2--2025.pdf)

- **AA111 (P3, M): Parse cost AA109 added.** Found by #124's review; each change needs a
  reproducible benchmark run before and after it. Reproduced on main 2026-11-27
  against the tree before #124 (`4819783`), one machine, median of five runs:
  `mask_table` on 1,000-row synthetic tables takes 1.9 times as long on an ordinary
  table with no identifier, 2.0 times on a register and 3.0 times on a dependents
  list. `extract_pdf_text` on a dense synthetic page (48 short rows, each with three
  amounts whose currency, thousands and decimal glyphs are unmapped, in 27
  different sets of three kinds) takes 3.1 times as long, and 1.1 times on the
  40-row statement of `test_repeated_amount_rows_with_unmapped_punctuation_stay_readable`.
  The "about seven times" was measured on another page and is not reproduced here.
  Where the time goes: on the ordinary table `mask_table` calls `_is_id_label_cell`
  about 2.8 times per cell, once for `labels` and then for the cell above and the
  cell in the wrapped-header loop, which runs both before its digit check (the
  joined call runs only when neither holds a digit) and repeats what `labels` and
  the previous row found; that is about a third of the time under a profiler.
  `_table_other_field` runs on every heading for every row (about 15% of the
  dependents list), and the date-heading, `_TABLE_FIELD_CAPTION`, flag and
  `variant_columns` patterns read every cell of every row. On the dense page
  `_refuse_unreadable_field` probes the whole page once per marking that finds a
  candidate, 29 times against 2 before #124, each pass running `extract_words`,
  `_readable_identifier_glyphs` and `_overlaid_digits` over every glyph. The
  `MAX_SEPARATOR_HYPOTHESES` budget does not bound those passes, because a kind
  set already enumerated is reused without a charge. `_font_resource_chars`
  replays the content stream for any page with one unmapped glyph (about 15% of
  the statement page). Also found: a 60-row page rotating 3 of 12 unmapped kinds
  per row refuses on main by budget exhaustion, as `parse-boundary.md` documents,
  where the tree before #124 read it; no step here changes a refusal. Four steps,
  each one pull request, each keeping every output byte-identical.
  **Step 1, the benchmark.** Commit `scripts/bench_masking.py` in
  `scripts/bench_crypto_import.py`'s form (`sh agents/lib/run.sh -m
  scripts.bench_masking [N] [REPEAT]`, synthetic only, one untimed run, then the
  median and minimum of REPEAT): the ordinary table (Date, Description, Amount,
  Balance, Category, Note), the register (Document, Count, Wages, Year, with "SSN"
  among the document types), the dependents list (the Form 1040 dependents
  headings over masked identifiers), and the dense and statement pages above,
  built in a temporary directory with `tests/test_identifier_spellings.py::_cid_pdf`.
  For each input it also prints a SHA-256 of the masked output and the counts the
  steps change, which do not vary between runs: `_is_id_label_cell` calls per cell
  and glyphs passed to `_overlaid_digits` per page. No engine change; the PR
  records main's numbers.
  **Step 2, `mask_table`'s repeated label checks.** In the wrapped-header loop,
  test digits before `_is_id_label_cell`, reuse `labels` for the cell and the
  previous row's results for the cell above, and compute `_table_other_field` and
  `_NINE_DIGIT_FIELD` once per header instead of once per row. Skipping the header patterns on rows holding digits is not assumed
  safe: `header_dates`, `header_finance` and `variant_columns` read every row
  today, so change them only where the identity check holds. Test in a new
  `tests/test_masking_cost.py` (not a MEASURED COSTS module): a counter on
  `secure_fs._is_id_label_cell` over the bench's ordinary table at 200 rows: step
  1 pins that table's cell contents and records main's calls per cell (between
  2.5 and 3.5 by layout), and the bound is stated from that record, one call per
  cell plus the joined calls for its digit-free columns.
  **Step 3, one probe of the page.** In `_refuse_unreadable_field`, probe every
  marking in one pass where their glyph groups are disjoint, or restrict a pass to
  the candidates' rows plus every row a label can govern them from; the positional
  readers take labels from rows above and beside, so a pass that drops them
  changes a refusal. Test: on the dense page, `_overlaid_digits` receives at most
  twice the page's glyphs in all (main: 29 whole-page passes), red when reverted.
  **Step 4, the font replay.** Skip `_font_resource_chars` when no two font
  resources on the page, its forms' resources included, share a font name, since
  the kind key then falls back to the same partition. Test: no replay on the
  statement page, and still a replay for both layouts of
  `test_same_named_font_resources_can_give_one_cid_mixed_roles`.
  Identity check for steps 2-4: the bench digests are unchanged; AA116 step 1's
  script with `--baseline` reports no changed table in any corpus; AA110 step 2's
  sweep reports the same refusals and outputs in every mode; and
  `tests/test_identifier_*.py`, `tests/test_typographic_identifiers.py`,
  `tests/test_unreadable_*.py`, `tests/test_overlaid_identifiers.py` and
  `tests/test_parse_utils.py` pass. Done for each step when its counting test is
  red with the change reverted and the PR shows the benchmark before and after.

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
  named, held open and transcribed. The false mask remains, and #124 (AA109) added
  it: the cross-cell whitespace grouping in `secure_fs._row_candidates`
  (`_grouped_inside_cell`) reads a three-digit figure, a space-grouped amount and a
  one-digit figure as one 3/2/4 value. Reproduced on main 2026-11-27:
  `mask_table([["SSN", "850", "12 500", "1"]])` returns `XXX | XX 500 | 1`, and
  "5001" then reads as a false last four. The row masks the same way after `ITIN`
  or `Has SSN`, with the label on the row above, and under `Document type | Count |
  Annual earnings | Copies`; under an `Amount` heading main keeps it, and the tree
  before #124 (`4819783`) returns every one of them unchanged. That layout cannot
  be freed by its shape: an identifier cut 3 | 2+3 | 1 is the same row, and
  `test_a_labelled_identifier_between_count_columns_stays_masked` and
  `test_unowned_three_cell_reading_still_masks_an_identifier`
  (`tests/test_identifier_shifted_tables.py`) pin it masked in the row, on the row
  above and under the header. #134's first head freed it and printed identifiers
  cut the same way: 841 of 20,000 fuzzed tables with an SSN label in the row, and
  2,535 of 5,000 through financial columns (`finance` in `mask_table`) that
  outlived a later header; the narrowest repair tested left about 900. Only what
  context separates can be freed, and only after measuring. Two steps, each one
  pull request; AA110 and AA111 reuse step 1's script.
  **Step 1, the measurement, no rule change.** No fuzz harness is in the
  repository: #124's and #134's lived in session scratchpads and are lost. Commit
  `scripts/fuzz_mask_table.py`, in `scripts/bench_crypto_import.py`'s form (`sh
  agents/lib/run.sh -m scripts.fuzz_mask_table`), seeded and synthetic, printing
  counts and digit shapes (`n` per digit), never a value. Three corpora. (I)
  Identifier-free tables, 3-6 columns under headings drawn from Count, Copies,
  Units, Amount, Wages, Annual earnings, Year, Date, Note, Name, Relationship,
  Document type, SSN, ITIN, Taxpayer ID and Has SSN; cells drawn from counts,
  single digits, amounts grouped by space, NBSP or narrow NBSP, comma amounts,
  years, dates, ZIP codes and "SSN" or "ITIN" as a document type; ragged and blank
  rows included. Any changed cell is a lost figure. (L) The same tables holding one
  synthetic identifier (an invalid area) spelled as spaced 3/2/4, comb or marked
  comb, whole or cut at a random digit across two or three cells: its label in the
  row, as the heading over its first cell, on the row above, as a flag label (`Has
  SSN`) in the row, and with a figure before it inside its first cell. A leak is
  its first five digits printing in order through `identifier_text`, at most four
  non-digits apart, as `tests/test_identifier_residuals.py::_prints` reads. (S)
  Stale header state: a register header (`Document type | Count | <financial
  caption> | Copies`) with data rows, then a header naming an identifier column
  over a different layout, then a cut identifier under it. The script runs a
  positive control first (a table it must count as a leak, one it must count as a
  loss) and refuses to report otherwise. `--baseline DIR` (from `git archive main
  agents`) runs that tree in a subprocess, and both sides print which
  `secure_fs.py` they imported: a differential that imports the candidate twice
  reports clean. It reports per corpus the tables changed or leaking on each side,
  newly leaking, newly losing a figure and recovered, and the grouped-amount class
  (a changed cell holding a thousands-grouped amount) split by what introduced it.
  A prototype on main (seed 116) changed 457 of 20,000 (I) tables: 45 in that
  class (9 under a flag label alone, 20 after an identifier label in the row, 16
  under a heading or a label above; 27 start inside an amount after its
  own leading group, as `12 500 | 34 500 | 1` does), and 412 the documented
  count-before-date mask. Done when the script runs on main with its controls
  passing and the PR records main's numbers under `parse-boundary.md`'s
  measurement notes. The script is not part of the suite.
  **Step 2, free only what the measurement allows.** Two candidate rules, each
  kept only if, against main, it adds no leaking table in (L) or (S), changes no
  (I) table main leaves alone, and shrinks the grouped-amount class. (a) On a row
  whose only identifier label is a flag (`_ID_FLAG_RE`), a new cross-cell
  whitespace grouping (`extended_groups` from `_row_candidates`) is not accepted,
  as AA109 already keeps a whole date beside a flag; combs and marked spellings
  stay introduced (`tests/test_identifier_table_layouts.py::test_a_header_introduces_only_the_next_rows_crossing_comb`
  pins a cut comb under `Has SSN`). (b) A new cross-cell grouping cannot start
  inside a cell after that cell's own leading digit group. Everything else stays
  masked and recoverable through #134's path. Tests in
  `tests/test_identifier_shifted_tables.py` (not a MEASURED COSTS module):
  `["Has SSN", "850", "12 500", "1"]` unchanged; `["SSN", "12 500", "34 500",
  "1"]` unchanged; `["SSN", "850", "12 500", "1"]` still masked; an identifier
  cut 3 | 2+3 | 1 after `SSN` still prints no first five; a comb cut across two
  cells under `Has SSN` still masked. Done when each kept rule turns its test red
  when reverted, the two pinned tests stay green, both sides' numbers are in the
  PR, and `parse-boundary.md`'s whitespace-grouping paragraph states the narrowed
  rule. Found by #124's review.

- **AA117 (P2, M): Share one source inventory across intake and validation.**
  Four readers decide what a source document is, and they disagree. The scanner
  (`parse_utils.scan_workspace`) reads the top level of `documents/` for .pdf,
  .xlsx and .csv, skipping links, names carrying an identifier and any name
  containing `Tax_Return_` or `Summary`, but it lists dot-files. The ledger
  builder (`parse_records._documents`) reads the top level in any format,
  follows links and skips dot-files. The tie-out (`tie_out.list_document_files`,
  which `receipt._documents` reuses) recurses, keeps .pdf, .xlsx, .xls and .csv
  (`_DOCUMENT_EXTENSIONS`), follows links and skips dot-files. Reproduced on main
  (327af4e) with nine entries in `documents/`: all three readers agree on 2 of
  the 9. A `Fidelity_Tax_Summary.csv` or a `.xls` is never scanned but both later
  readers require a record. A `notes.txt` or `photo_W2.jpg` has no record that
  passes: the builder refuses the ledger without one, and with a skip record the
  tie-out fails it as a phantom. A `documents/sub/1099.csv` is the reverse: the
  tie-out requires a record and the builder refuses that record as a phantom.
  On Windows, `list_document_files` returns `str(p.relative_to(workspace))`, so
  `documents\W2_Acme.pdf` against the ledger's `documents/W2_Acme.pdf` comes out
  as `missing_records` and `phantom_files` together and `failed: True` (simulated
  with `PureWindowsPath` through `tie_out.evaluate`). Every Windows tie-out with
  a document fails, while `_source_outcomes`, which keys on
  `str(Path(record.file))`, matches. `load_ledger` compares `file` as a raw
  string, so `documents/W2.pdf` and `documents\W2.pdf` load as two records and
  the wages sum to $2,000 from one $1,000 W-2. It also accepts an absolute path,
  `documents/../result.json` and `summaries/x.md`; coverage catches each one
  later as a phantom. `amend.ledger_changes` reports one document under two
  spellings as REMOVED and ADDED. Every case fails closed today, so the cost is
  a blocked return, not a wrong one. Three steps, each one pull request.
  **Step 1, one inventory.** Add `source_inventory(workspace)` to
  `agents/lib/workspace.py`, which every reader already imports and which needs
  no new preflight roster row. It walks `documents/` recursively without
  following links. Each entry gets one disposition:
  `document`, with `canonical_source_path` giving the workspace-relative POSIX
  name; `ignored`, when the name or a parent directory below `documents/` starts
  with "."; or `refused`, with a reason. The reasons are a link or junction
  (`secure_fs.is_reparse_point`), a name that carries an identifier
  (`secure_fs.names_an_identifier`), or a suffix outside `.pdf`, `.xlsx`, `.xls`
  and `.csv` ("convert it to PDF or move it out of documents/"). Drop the
  `Tax_Return_`/`Summary` name skip: the renderers' default outputs land at the
  workspace root, not in `documents/`. `scan_workspace` lists the documents under its
  directory and adds a `file` field holding the canonical name, which the record
  copies; it takes only the scanned directory, so the name is relative to that
  directory's parent (the CLI passes `documents/`). It warns for each refused entry and gives a count of ignored ones.
  `parse_records._documents`, `tie_out.list_document_files` and
  `receipt._documents` return the same documents. `build_ledger` refuses while
  any entry is refused, naming the reason. `tie_out.evaluate` fails on a refused
  entry, reported in a new `refused_documents` field and rendered in
  `_tie_out.md`. `_DOCUMENT_EXTENSIONS` and `parse_utils.DIGEST_EXTENSIONS`,
  which a test already holds equal, both name the inventory's set.
  `_source_outcomes` keys on the canonical name; on POSIX that is the same
  string, so existing receipts and `source.` items keep their keys.
  Keep `_validate_subpath` in `receipt.gate_inputs` and
  `parse_utils._confined_dir`. Tests in a new `tests/test_source_inventory.py`:
  a fixture of `Bank_1099.csv`, `Broker.xlsx`, `Fidelity_Tax_Summary.csv`,
  `Legacy.xls`, `sub/Nested_1099.csv`, `notes.txt`, `photo_W2.jpg`,
  `.hidden.csv` and a link gives the same first five as documents from all four
  readers, the next two and the link as refused, and `.hidden.csv` as ignored.
  A nested file with its record builds and ties out with no missing or phantom
  entry. A `notes.txt`
  fails both `build_ledger` and the tie-out with the unsupported-format reason,
  with or without a skip record. A file under `documents/.cache/` is ignored by
  all four readers. Done when each new rule turns its test red when reverted, and
  `tax-parse/SKILL.md` steps 1 and 6.5 and the README's "Supported formats" line
  state the dispositions.
  **Step 2, a Windows smoke test.** Mark step 1's link case POSIX-only, since a
  symlink needs a privilege on Windows and the job runs the whole module. Add cases to
  `tests/test_source_inventory.py` that run on every platform with no symlink
  and no mode assertion. One workspace holds `documents/W2_Acme.csv` and
  `documents/sub/1099.csv`, with records written by
  `parse_records.write_record` using the scanner's `file`. `build_ledger`
  passes, and `tie_out.evaluate(load_ledger(ledger), {},
  list_document_files(ws))` has `missing_records == phantom_files == ()`.
  `receipt.gate_inputs(ws, "tie_out")` holds
  `receipt._entry_key("documents/sub/1099.csv")`. `_source_outcomes` opens no
  item when `source_sha256` equals the file's digest. Every text read or write
  names its encoding. Add the module to the `test-windows` command in
  `.github/workflows/ci.yml`, to `WINDOWS_MODULES` in `tests/test_ci_workflow.py`
  and to the parametrize list of
  `test_the_modules_the_windows_job_runs_are_clean_too`. Add it to "Why the
  Windows job is scoped the way it is" in `docs/contributing/ci-and-locks.md`.
  None of these are MEASURED COSTS modules. Done when the Windows job runs the
  module green, and the step-1 change to `list_document_files` reverted turns it
  red there.
  **Step 3, older ledger paths.** `load_ledger` passes each `file` through
  `canonical_source_path`. It accepts `documents\W2.pdf` and `./documents/W2.pdf`
  as `documents/W2.pdf`. It refuses an absolute path, a drive or UNC anchor, a
  `..` part and any path outside `documents/`, naming the record. Then it runs
  its duplicate check on the canonical name. `amend.ledger_changes` keys both
  ledgers the same way; `rollover._archived_ledger` already reads through
  `load_ledger` and degrades to `([], True)` on a refusal. The material-evidence
  sources that `source_reconciliation` compares with `record.file` (the
  `personal_sources` lookup and `reported_files`) then match on Windows too.
  Tests in `tests/test_tie_out.py`: `documents\W2.pdf` loads as
  `documents/W2.pdf`; that spelling beside `documents/W2.pdf` is refused as a
  duplicate file (today two records and $2,000 of wages); `/abs/documents/W2.pdf`,
  `C:\ws\documents\W2.pdf`, `documents/../result.json` and `summaries/x.md` are
  each refused. In `tests/test_amend.py`: an as-filed ledger with
  `documents\W2.pdf` and a current one with `documents/W2.pdf` and equal boxes
  report no change. Done when each new rule turns its test red when reverted.
  Found by the 2026-11-18 external review.

- **AA118 (P2, M): Drive setup and the support table from one coverage source.**
  #131 corrected the README's privacy and support claims and the persona guide's
  NC and WA scope. Nothing before intake names an unreviewed form year.
  Reproduced on main (327af4e): `capabilities.evaluate` on the profile template
  with every flag false and `filing_status: single` returns no blocking finding
  for CA in TY2025, TY2026 and TY2027, or for NC, NY and WA in TY2026.
  `tax-setup` runs no coverage check. The refusal comes only from the form
  builders (`compute_ca_540`: "Form 540 line map has not been reviewed for this
  tax year"; `compute_ca_schedule_ca`, `compute_ca_5805`) and from
  `filing_package`, after the documents are parsed. The years sit at twelve
  sites in ten modules: `ca._CA_540_REVIEWED_YEARS`, `nc._NC_D401_YEARS_READ`,
  `planning._REVIEWED_YEARS`, and `!= 2025` literals in
  `ca_credits.compute_ca_credit_order`, `ca_nonresident._year`,
  `ny_nonresident` (`ny_it203_plan`, `compute_nyc_part_year`,
  `compute_ny_it203`), `wa.compute_wa_capital_gains`,
  `filing_package._refuse_uncomputed_states`, `pdf_render.render` and
  `rollover._ca_source_path`. `capabilities._state_note` writes "TY2025" into
  the NC, WA and IT-203 notes whatever year the profile holds. The README's "Filing
  coverage" is prose bullets, not a table. NY IT-201 has no form-year guard at
  all (`ny.py` takes every figure as an argument), while
  `planning._REVIEWED_YEARS` lists NY as reviewed for 2025 only. The one
  coverage source is the existing state registry
  (`agents/lib/tax_computations/state/registry.py`, W10). Every module already
  derives its section tables from it, and it imports nothing but `agents.lib`.
  Two steps, each one pull request.
  **Step 1, the years in the registry.** Give `StateSection` three fields.
  `reviewed_years` is a frozenset of form years, or None for a statement-only
  row (FL). `residency` is `full_year` or `part_year_nonresident`. `depth` is
  `computed`, `computed_with_worksheets` or `statement`. Fill them from the
  sets and literals above as they stand when this step is implemented; AA83
  may have added 2026 to CA 540 by then. Each module listed reads its row:
  `_CA_540_REVIEWED_YEARS = BY_SECTION["california_540"].reviewed_years`, the
  540NR, IT-203, NC and WA guards from their own rows, and
  `planning._REVIEWED_YEARS` from the CA, NY and NC primary rows. Error
  messages and behavior stay as they are. Tests in
  `tests/test_state_registry.py`. Each module set is the row's own object
  (`is`, not `==`). A copy finder in the style of `_literal_copies` refuses a
  `{2025}`, `frozenset({2025})` or `!= 2025` form-year literal at the twelve
  sites; `filing_package._derived_schedules`'s `rental["computed_tax_year"] !=
  2025` is a federal Schedule E year this step does not move, and is exempt. `compute_wa_capital_gains(tax_year=2026, ...)` still refuses.
  `compute_ca_540` with `tax_year=2026` still raises its line-map message
  unless the row lists 2026. Done when each new rule turns its test red when
  reverted, and the registry row in the table in
  `docs/contributing/state-module-playbook.md` names the three fields.
  **Step 2, name it at setup and in the table.** Add `capabilities.coverage(profile)`.
  It selects the row from `_filing_state`, `ca_540nr_plan` and
  `ny_it203_plan`. It returns the form title (from `package_forms`), the
  profile's `tax_year`, whether that year is in `reviewed_years`, the residency
  and the depth. An income-tax state with no row gets `estimate_only`, and a
  `_NO_INCOME_TAX_STATES` code gets `no_income_tax`. A new `--coverage` flag on the
  capabilities CLI prints it and exits 2 for an unreviewed year. It returns
  before `receipt.gate_inputs`, so it writes no `validation_receipt.json` and no
  `material_evidence.json`. `evaluate` adds the same condition as an inline
  finding, `state_form_year_unreviewed` (UNSUPPORTED, `acknowledged=False`).
  That moves today's builder refusal to Step 0.7 without making anything
  computable that is not, with one new refusal: NY IT-201 for TY2026 computes
  today and now blocks at Step 0.7, its row taking `{2025}` from `planning`. As
  `unanswered_pfic_event` and `_NY_HOUSEHOLD_RULE_ID` are, it is an inline
  UNSUPPORTED finding with no `RULES` row and no flag. FL, which has a row with
  `reviewed_years` None and is in `_NO_INCOME_TAX_STATES`, gets no finding, and
  a null `tax_year` keeps today's refusal of the profile. Existing assertions in
  `tests/test_capabilities.py` (a MEASURED COSTS module; edits move no count)
  change: `test_an_absent_residency_block_does_not_read_as_a_negative_answer`
  and its `half` loop (CA TY2026), `test_the_pre_2022_abode_period_is_the_longer_one`
  (CA TY2021, expects `()`) and `test_a_new_york_filer_is_not_blocked_by_their_own_state`
  (NY TY2026). So it stays out of the `**Blocking**` paragraph of
  `docs/contributing/skill-catalog.md`, which
  `test_the_skill_catalog_names_every_blocking_rule` holds to `RULES`. Describe
  it in a sentence of its own there. `_state_note` takes the year it prints from
  the row. `tax-setup/SKILL.md` gets a step after "3. Save Taxpayer Profile"
  that runs `--coverage` and shows the row to the user. On exit 2 it STOPs
  before `/tax-parse`, and step 7's summary repeats the row. The README's
  "Filing coverage" gains a table rendered by `registry.coverage_markdown()`
  (state, form, form years, residency, depth); the prose bullets and their
  links stay under it. Tests in a new `tests/test_coverage.py` (not
  `test_capabilities.py`, a MEASURED COSTS module). A CA TY2026 profile gets
  `--coverage` exit 2 naming Form 540 and its reviewed years, and the workspace
  then holds no `validation_receipt.json`. An NC TY2025 profile shows Form
  D-400 as `computed`, exit 0. A CA TY2025 profile shows Form 540 as
  `computed_with_worksheets`. A 540NR plan selects the part-year row. An Oregon
  profile shows `estimate_only`, exit 0. `evaluate` on the CA TY2026 profile
  blocks with `state_form_year_unreviewed`. The README holds
  `coverage_markdown()` verbatim. Done when each new rule turns its test red when
  reverted, and `tax-setup/references/interview.md` names the check.
  Found by the 2026-11-18 external review.

- **AA119 (P2, L): Publish the validated return and package deterministically.** The
  three gates are `capabilities`, `return_builder --check` and `tie_out` (Steps
  0.7, 12.6 and 12.7). Each runs as its own process, and each takes and releases
  the workspace lock (`workspace.hold_workspace`) for itself. The binding already
  works inside one renderer. `pdf_render._main` and `filing_package._main` each
  read `result.json` once and pass those bytes to `receipt.verify(...,
  result_bytes=raw)` and to `receipt.covers_result(..., contents=raw)`. An
  interrupted run leaves `.tax-workspace.abandoned`, which `verify` reports
  first. Reproduced on main (327af4e) on the validated workspace from
  `tests/test_pdf_render.py::TestTheCli`. Two `pdf_render` runs one second apart
  wrote different bytes from the same inputs. The PDF carries no digest of the
  result it was rendered from. `filing_package --json` reported `validated: true`
  and wrote no PDF. No engine module writes `Tax_Return_{year}_Filing_Package.pdf`:
  `tax-generate` Step 4.5 has the agent write parts 2-4 from prose after its own
  separate read of `result.json`. After an edit to `result.json` the summary
  PDF stayed on disk, unmarked, while `verify` turned false. The gap, then, is
  the package PDF and the pairing of the two PDFs. Four steps, each one pull
  request.
  **Step 1, a reproducible summary that names its return.** In
  `pdf_render.render`, pass `invariant=1` to `SimpleDocTemplate`; reportlab 5.1.0
  then writes identical bytes for identical input (probed: identical with it,
  different without). Add a `result_sha256` keyword and write
  `keywords=f"result.json sha256 {digest}; validated"`, or `draft` when
  `draft_reasons` is given. `_main` passes the digest of `raw`. Tests in
  `tests/test_pdf_render.py::TestTheCli`. Two runs on one validated workspace
  give byte-identical PDFs. `pdfplumber`'s `metadata["Keywords"]` holds the
  SHA-256 of the `result.json` bytes and `validated`. `--draft` writes `draft`
  and keeps NOT VALIDATED on page 1. A one-cent change to a figure changes both
  the bytes and the digest. Done when each new rule turns its test red when
  reverted.
  **Step 2, the engine writes the package, parts 1 and 2.** Add `--pdf` and
  `--out` to `filing_package._main`. The default is
  `Tax_Return_{year}_Filing_Package.pdf` through `resolve_under_workspace`. It
  renders from the same `raw` that `verify` and `covers_result` checked, using
  step 1's build (`atomic_path`, `PRODUCER`, `invariant`, keywords) and
  refusing a non-finite figure (`first_non_finite`). Page 1 carries the
  not-an-e-file banner, `meta.limitations` and `_draft_banner(checked)`. Part 1
  is `assembly_order`, `attachment_checklist` and the disclosures, the content
  `render_markdown` prints today. Part 2 is the line-numbered pages that
  tax-generate Step 4.5 item 2 describes, moved into code. `line_<n>_*` prints
  as "Line n" in numeric order, other fields go under "Supporting detail", and
  the derived schedule rows come from `_derived_schedules`. Its field-name
  caveats become one table keyed by (section, field). Tests in
  `tests/test_filing_package.py`. In a validated workspace, Form 1040's page
  prints "Line 9" before "Line 11". `schedule_d.line_1c_1099b_box_c` prints as
  "internal name — place per the Schedule D instructions", never "Line 1c".
  `california_540` prints with no line labels. `line_13b_schedule_1a_deductions`
  of 0 prints no 13b row. A stale receipt exits 2 and writes no PDF, while
  `--draft` writes one marked NOT VALIDATED. The keywords digest equals the
  digest `covers_result` checked. Done when each new rule turns its test red when
  reverted, and Step 4.5 items 1-2 name the command instead of the prose.
  **Step 3, parts 3 and 4.** Part 3 is the transfer mapping: one table in
  `filing_package` from each packaged section to a generic "typical software
  section", with no vendor's menu path. A section with no row prints "enter per
  the form's instructions". Part 4 is the sign-and-file page, built from
  `attachment_checklist`'s lines: signatures, assembly order, confirming the
  IRS mailing address on irs.gov, the separate FTB envelope for a CA return,
  and the FBAR line exactly as the checklist words it. Tests: every section in
  `assembly_order` gets one Part 3 row. No row names TurboTax, FreeTaxUSA, H&R
  Block or TaxAct. A CA return's Part 4 holds the separate-envelope line. The
  FBAR sentence matches the checklist's. Done when each new rule turns its test
  red when reverted, and Step 4.5 items 3-4 are replaced.
  **Step 4, one finalization command.** Add `agents/lib/finalize.py`, run as
  `sh agents/lib/run.sh -m agents.lib.finalize [--draft]`. List it in
  `workspace.LOCKED_CLIS`; `tests/test_workspace.py` pins that list both ways.
  Add it to `preflight.SKILL_MODULES`, whose roster test pins the directory;
  a new engine file adds a case to `tests/test_secure_fs.py`'s `engine_files()`
  parametrization, so refresh that MEASURED COSTS row and both sums in
  `.claude/hooks/skill-invariants.sh`. It
  holds the lock from start to publish and reads `result.json` once. It runs the
  gates in-process, in order, from a module-level `GATES` tuple: `capabilities._main`,
  `return_builder._main(["--check"])` and `tie_out._main([])`, each with
  `--workspace`. Their `hold_workspace` re-enters the held lock, which
  `workspace_lock` already allows for its own process; correct
  `hold_workspace`'s docstring, which says a second call refuses. It stops at the first
  non-zero exit. Then it runs `verify(for_filing=True, result_bytes=raw)` and
  `covers_result(contents=raw)`. It renders the summary and the package from
  `raw` into `secure_fs.atomic_paths(summary, package)`, and runs `verify`
  again just before the block publishes both. Exit 0 means both were published
  final. Exit 2 means refused, with no PDF written and earlier PDFs untouched
  (the gates still write the receipt, `_tie_out.md` and `material_evidence.json`).
  Exit 1 means it could not run. `--draft` publishes both marked NOT VALIDATED.
  Tests in a new `tests/test_finalize.py`, with stand-ins for `GATES` that
  record PASS as `TestTheCli._workspace` does. A retry with unchanged inputs
  writes byte-identical PDFs. A stand-in gate that rewrites `result.json`
  before recording PASS gives exit 2 and no PDF. A rename failure inside
  `atomic_paths` leaves neither new PDF and the old pair intact. A lock held by
  a live process gives exit 1 and writes nothing. A stale lock is reclaimed
  into `.tax-workspace.abandoned`, and the run publishes only after the gates
  pass on the inputs as they are now. An open material item refuses the final
  publish, while `--draft` marks page 1 of both PDFs and writes `draft` in both
  keywords. `GATES` is exactly those three, in that order. Done when each new rule
  turns its test red when reverted, and `tax-generate` Steps 2 and 4.5 run this
  command. The lock comment above `LOCKED_CLIS` must say why `finalize` holds the
  lock that `pdf_render` and `filing_package` alone do not.
  Found by the 2026-11-18 external review.

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
  docstring: a REIT fund's 1099-DIV box 2b unrecaptured §1250 gain, a box 2d
  collectibles gain, a 1099-B sale marked collectibles (gold and silver ETFs) or
  a sale of depreciated real property is priced by `compute_qdcg_tax` at
  0/15/20%, which under-states the tax; until #168 every gate stayed green. Two
  steps, each one pull request.
  **Step 1 shipped in #168:** boxes 2b and 2d and the 1099-B collectibles mark
  are ledger categories, `sold_depreciated_real_property` is a required gate
  question, and `input.schedule_d_tax_worksheet` holds the final package until
  a reviewed figure is recorded in `schedule_d.tax_worksheet_tax`, which line 16
  takes. The figure is bounded below by the tax on line 15 less qualified
  dividends and net capital gain, and above by the regular tax from the Tax
  Table ceiling up; a draft keeps the 0/15/20% figure with the banner.
  **Step 2, compute the worksheet.** Carry the 28% and unrecaptured §1250
  amounts as Schedule D lines 18 and 19 in `result_template.json`'s
  `schedule_d`, net them as the covered-year Schedule D instructions direct, and
  compute the Schedule D Tax Worksheet in `tax_computations` with the
  investment-interest election (Form 4952 line 4g, not modeled today) as an
  input; the item then closes as `computed`. Tests: both categories together,
  short-term loss netting that changes the worksheet inputs, and the ordinary
  long-term control. Found by the 2026-11-18 external review. [IRS Schedule D
  instructions, TY2025](https://www.irs.gov/pub/irs-prior/i1040sd--2025.pdf)

- **AA123 (P2, M): Screen the federal and California refundable credits (Q16).**
  The capability rule `earned_income_credit` (`agents/lib/capabilities.py`)
  discloses the federal EIC and FTB 3514's CalEITC, Young Child and Foster Youth
  credits (Form 540 lines 75-77, 540NR lines 85-87) without computing them, and
  is MANUAL on purpose: a hard stop on a maybe teaches filers to answer no. Three
  gaps, reproduced on main (327af4e). The template ships
  `flags.may_qualify_for_eic: false` and `tie_out._main` reads a present false as
  the taxpayer's no: the tie-out CLI over an $18,000-wage return with line 27 at
  0 prints no EIC advisory with the template's value, and the UNANSWERED one with
  the key removed. Nothing records that a worksheet was worked:
  `material_evidence._required_filing_inputs` for that return with a
  `california_540` section asks only `input.deduction_election`. And there is no
  limit to screen with: neither `create_empty_tax_law` nor
  `create_state_template("CA")` has an EIC or CalEITC key. Per Q16 the engine
  screens out a return at or above the covered year's FETCHED limits and holds
  every other final package until an outcome is recorded; an open item keeps the
  draft banner through the existing reasons. Two steps, each one pull request,
  California first because it needs no acceptance replay.
  **The screen, shared by both steps.** It reads only what rules a credit out
  whatever the facts the engine cannot see. AGI (`form_1040.line_11_agi`) against
  the limit for three or more qualifying children, the largest column: the
  profile's `dependents` do not bound EIC children, since a custodial parent who
  releases a dependency on Form 8332 keeps the child for the EIC. And a lower
  bound on investment income, lines 2a + 2b + 3b (`line_2a_tax_exempt_interest`,
  `line_2b_taxable_interest`, `line_3b_ordinary_dividends`), which both
  worksheets add in full while each other line they add is zero or more. A
  missing, non-numeric or non-finite limit or AGI screens nobody out. Do not
  reuse `material_evidence._above`: it answers True on a missing threshold, right
  for "does this tax apply" and backwards here (`_above(200000, None)` is True
  on main). Neither the screen nor the hold reads `may_qualify_for_eic`: a
  profile's no is a screening answer, not a worked worksheet. Each item is
  positive when its lines hold a credit, so the existing rule refuses
  `not_applicable` there; a positive line on a screened-out return is refused as
  a credit the covered-year limits rule out (the over-statement direction); and
  `computed` is refused on both items, since nothing computes them. A limit
  present in the cache joins `applicable_law._state_figures` or
  `_federal_figures`, as the optional `federal.amt.phaseout_rate` does, so a
  carried-over or unpinned limit refuses the package instead of screening with an
  older year's lower figure.
  **Step 1, California.** Add `caleitc: {"income_limit": None,
  "investment_income_limit": None}` to `create_state_template("CA")` in
  `agents/lib/tax_law_fetcher.py`. The TY2025 FTB 3514 instructions require
  earned income and federal AGI "less than $32,901" and stop at investment income
  "more than $4,814", so a return is screened out at AGI of `income_limit` or
  more, or a lower bound above `investment_income_limit`. YCTC and FYTC both
  require being allowed CalEITC, YCTC's zero-earned-income route included (it
  also stops at federal AGI over $32,900), so one screen covers all three. Add
  `input.ca_family_credits` ("FTB 3514 CalEITC, Young Child and Foster Youth
  credit worksheets") to `material_evidence._FILING_INPUT_LABELS` and
  `_required_filing_inputs`, required on a California return (a `california_540`
  or `california_540nr` section, the test `applicable_law` already makes) the
  screen does not rule out, positive when `california_540.line_75`-`line_77` or
  `california_540nr.line_85`-`line_87` is. Ship `may_qualify_for_eic` as `null`
  in `taxpayer_profile_template.json` (the `has_digital_asset_option_exercise`
  precedent) and update its `_comment_eic`. Add a CA bullet to
  `tax-setup/references/tax-law-fetch.md` and a row to
  `docs/ops/season-open-runbook.md`'s block table; `check_freshness` gets no skip
  entry, so the nulls read INCOMPLETE, since every CA return uses them. Give
  `evals/ca_filing_2025.py::seed_law` both figures, or its $138,000-wage household
  in `tests/test_ca_filing_release.py` is held. Tests in
  `tests/test_filing_package.py` beside the AA113 `_inputs` tests, at the TY2025
  figures: single at AGI 32,900 (required, open) and 32,901 (no item); AGI 30,000
  with 2a 1,000, 2b 2,000 and 3b 1,814 (required) and 3b 1,815 (no item); an
  unfetched `income_limit` at AGI 200,000 (required), and a present one without
  provenance named by `applicable_law.filing_issues`; line 75 at 300 on AGI
  40,000 (refused) and a `computed` outcome (refused). In `tests/test_tie_out.py`,
  the template's own flags through the CLI give the UNANSWERED advisory. No test
  goes in `test_capabilities.py`. Done when each new rule turns its test red when
  reverted, `tax-compute/SKILL.md` Step 12.65 lists the item, the advisory's
  shared closing sentence about answers.md in `tie_out.py` names the outcome for
  the California lines (the New York lines keep it), step 12 of
  `tax-compute/references/state-return.md` names the outcome, and `docs/contributing/tax-law-cache.md`
  and the EIC bullet of `tax-gotchas.md` name the keys. The disclosure texts keep
  no dollar figure but $0
  (`test_capabilities.py::test_the_eic_text_states_no_year_specific_figure`).
  **Step 2, federal.** Add `earned_income_credit: {"completed_phaseout": {"mfj":
  None, "other": None}, "investment_income_limit": None}` to
  `create_empty_tax_law`: Rev. Proc. 2024-40's completed phaseout amount for
  three or more children, "at or above which no credit is allowed" ($68,675
  joint, $61,555 for all other statuses, TY2025), and its excessive investment
  income ($11,950, not allowed if it "exceeds"). The keying is joint against
  everyone else, so a surviving spouse reads `other`, unlike `qbi_threshold` and
  `child_tax_credit` where QSS maps to `mfj`: add the row to
  `tax-law-cache.md`'s keying table. A separate return reads `other` as well;
  the separated-spouse rule is a worksheet fact, not a limit. Add
  `input.earned_income_credit` ("Form 1040 line 27 earned income credit
  worksheet"), required unless the screen rules the return out, positive when
  `line_27_eic` is. The seed moves the freshness auditor's Replay B input: with
  it, `tests/test_claude_agents.py` fails because the verdict pins input
  354044de500b and the replay builds a new digest, which depends on where the
  block sits (probe on main). Only a fresh-session replay renews
  that pin (`docs/ops/review-subagent-acceptance.md`, "When to re-run"), which
  an implementer cannot run: stop at that test and hand it to the owner, or land
  this step in the season-open sitting, whose step 5 is that replay. Also: a
  federal bullet in `tax-law-fetch.md`, the runbook row, the complete fixture and
  an absence case (item open) in `tests/test_ty2026_readiness.py`, the figures
  in `evals/ca_filing_2025.py::seed_law`, and the closing `== []` assertion of
  `test_filing_package.py::test_outcome_checks_that_fixtures_cannot_reach`, whose
  form has no AGI and so now needs the item. Tests at the TY2025 figures: MFJ at
  AGI 68,674 (required) and 68,675 (no item); QSS at 61,555 (no item, the `other`
  column) and 61,554 (required); single at AGI 40,000 with 2a + 2b + 3b of
  11,951 (no item) and 11,950 (required); a California single at AGI 45,000
  (California screened out, federal required), then federal `not_applicable`
  with a worksheet attached and no California item; line 27 at 600 on MFJ AGI
  70,000 (refused). Done when each new rule turns its test red when reverted, and
  the tie-out advisory, the capability note, the template's `_note_line_27` and
  `tax-compute/references/tax-and-credits.md` line 27 name the outcome rather
  than answers.md. New York's IT-215, IT-209 and City EIC lines keep today's
  advisory. Found by the 2026-11-18 external review. [IRS Publication 596,
  TY2025](https://www.irs.gov/pub/irs-prior/p596--2025.pdf) · [Rev. Proc.
  2024-40](https://www.irs.gov/pub/irs-drop/rp-24-40.pdf) · [FTB 3514
  instructions, 2025](https://www.ftb.ca.gov/forms/2025/2025-3514-booklet.html)

- **AA125 (P3, L): Compare eligible elections on the full return.** The summed
  "Total potential savings" line left the tax-compute optimization report in
  #130; federal standard versus itemized is chosen jointly in
  `compute_schedule_a`, and California's election is Q7's. What remains is the
  education allocation, and nothing in the engine can price it. Reproduced on
  main (327af4e): `compute_scenario` takes no credit but the CTC,
  `scenario.parse_deltas(["aotc_expenses=4000"])` refuses the key, no file in
  `agents/lib` names a 1099-Q or a qualified tuition program, and
  `tie_out._ALL_CATEGORIES` has no category for one. The Pub 970 coordination
  is prose in `tax-compute/references/tax-and-credits.md` step 1 ("surface the
  trade-off and let the user choose"), so a return's taxable 529 earnings and
  its credit are two hand figures nothing checks against each other. Priced one
  at a time, the pieces over-state: two LLC-only students at $6,000 each give
  $2,400 summed and $2,000 together (the $10,000 per-return cap), and an AOTC on
  $4,000 at joint MAGI $165,000 is $1,875 at the baseline MAGI but $1,625 once
  the $2,000 of 529 earnings that allocation makes taxable is in MAGI.
  California has no education credit: the one education entry in the 2025
  Form 540 Credit Chart, College Access Tax (code 235), is for contributions to
  a fund. Its effect is through income, since taxable 529 earnings and any
  scholarship included to free expenses reach CA AGI through federal AGI, and
  through its non-conformity for some 529 uses. Two steps, each one pull
  request.
  **Step 1 (M), one worksheet for the expenses.** Add
  `compute_qtp_coordination` beside `compute_education_credits` in
  `agents/lib/tax_computations/__init__.py`. Each student records, from the
  Pub 970 worksheets: QTP qualified expenses, credit qualified expenses,
  tax-free assistance, the credit (`AOTC`, `LLC` or none) and the expenses
  allocated to it, and each 1099-Q's box 1 gross, box 2 earnings and box 3
  basis with its recipient (the beneficiary when paid to them or the school,
  otherwise the account owner, Pub 970 chapter 7). It returns the netted
  `qualified_expenses` for `compute_education_credits` and the taxable
  earnings, earnings × (1 − AQEE ÷ distributions) floored at zero (none taxable
  when the AQEE reach the distributions), the AQEE reduced by the expenses the
  credit used, to the cent. Refuse expenses allocated to a credit
  above that credit's adjusted expenses, credit expenses above the QTP
  expenses, box 1 other than box 2 plus box 3, a non-finite amount, a negative amount
  other than box 2 (a final-year loss the Form 1099-Q instructions allow),
  and any use but postsecondary expenses: for K-12 expenses, §529(f)
  credentials and a Roth IRA rollover California does not conform (Schedule CA
  line 8z column C, and FTB 3805P's 2½%). Record the rows in
  `form_8863.students` (`result_template.json`) with
  `qtp_gross_distributions_total` and `qtp_earnings_total`. Add ledger
  categories `qtp_gross_distribution` and `qtp_earnings` (1099-Q boxes 1 and 2)
  to `tie_out._INFORMATIONAL`, rows in the `tax-parse` SKILL.md category table
  and a 1099-Q section in `references/form-extraction.md`, and reconcile them
  in a check appended to `missing_targets` beside `_schedule1_other_checks`,
  since the totals are per student in `form_8863.students`. Taxable
  earnings whose recipient is on this return join
  `derive_schedule1_other_workpaper` as a component, confirmed out of
  `remaining_income` as `rentals_excluded_from_remaining` is. A distribution
  above the student's gross QTP expenses carries the Form 5329 Part II
  additional tax, which nothing computes: `_required_filing_inputs` then
  requires `input.other_taxes` with `not_applicable` refused, and on a CA
  return the tie-out check fails unless `ca_has_other_taxes` is true, so the
  `ca_other_taxes` rule (FTB 3805P) blocks. Tests (`TestEducationCredits` in
  `tests/test_tax_computations.py`, `tests/test_tie_out.py`,
  `tests/test_filing_package.py`): Pub 970 chapter 7 Example 1 ($8,300 of
  expenses, a $3,100 tax-free scholarship, a $5,300 distribution with $950 of
  earnings) gives $17.92 taxable, the publication's $18 rounding each line;
  Example 2 ($4,000 to the AOTC) gives $734.91 and passes $4,000 to
  `compute_education_credits`; $6,000 allocated against $5,200 of adjusted
  expenses is refused; a 1099-Q ledger record with no `form_8863` row fails the
  tie-out; and a $9,000 distribution against $8,300 of expenses requires
  `input.other_taxes`. Done when each rule turns its test red when reverted,
  and `tax-and-credits.md` step 1 calls the function instead of describing the
  netting.
  **Step 2 (M), the household comparison.** A new
  `agents/lib/education_plan.py`, listed in `preflight.SKILL_MODULES`; its file
  adds a case to `tests/test_secure_fs.py`'s `engine_files()` parametrization,
  so refresh that MEASURED COSTS row in `.claude/hooks/skill-invariants.sh` and run from `tax-and-credits.md`'s education step. It reads Step 1's
  `form_8863.students` and prices a closed candidate set per student: no
  credit; the AOTC on up to $4,000, or the LLC from the $10,000 per-return
  pool, with the 529 taking the rest; and, where a scholarship may by its terms
  pay nonqualified expenses, including just enough of it to bring the AOTC
  expenses to $4,000 (Pub 970 chapter 2 Example 3). Each household candidate,
  the product across students, is one change to the baseline: its income
  change (taxable earnings and included scholarship, less the recorded
  allocation's) is priced by `scenario.build_report` as an `other_income`
  delta, which re-prices Schedule A, taxable Social Security and the state
  through `price_state_points`, and its credits by `compute_education_credits`
  at the candidate's MAGI (`form_8863.magi` plus the change) and Credit Limit
  Worksheet amount, layered outside the keystone as `roth_ladder` layers NIIT.
  Per candidate it shows federal tax, the refundable AOTC (line 29), CA tax and
  the settlement, ranked by federal plus state settlement, with no total across
  candidates; the recorded allocation must reproduce `form_8863`'s credits to
  the cent or it refuses. It writes `scenarios/<name>.json` through
  `scenario.save_scenario` and never edits `result.json`; the chosen candidate
  is recorded through Step 1 and the return recomputed. It prints candidates
  unranked, naming the reason, when a cost it cannot price differs between
  them: a 1099-Q or scholarship whose recipient files another return, since
  that return's tax is left out (Pub 970 chapter 7 Example 2); a state effect
  `price_state_points` does not model, unless the cache declares `state.type`
  `none`; an EIC or California EITC the return claims, or where
  `may_qualify_for_eic` is not false (null included) (chapter 2 Example 5 moves the EIC; AA123 replaces the flag); a child
  tax credit limited by tax at any candidate, as Schedule 8812 takes the
  education credits first; a positive line 38; modified AGI above the fetched
  `federal.niit.threshold`; Step 1's over-distribution; and any year but TY2025
  (TY2026 requires a work-valid SSN, Pub 970 What's New, which
  `compute_education_credits` does not check). Tests
  (`tests/test_education_plan.py`; TY2025 brackets and the 2025 CA Tax Table
  rows named): Bill, chapter 2 Examples 2-3 (AGI $39,250, a $5,600 scholarship
  usable for any expense), where including $4,000 improves the federal
  settlement by $2,020.00 (the publication's $2,585 less $565), CA tax rises
  $160 ($704 to $864), and the household gains $1,860, ranked first; a single
  student who is the 1099-Q recipient, wages $39,250 and Example 1's figures,
  where the AOTC candidate has $734.91 of taxable earnings against $17.92,
  federal tax $2,669.69 against $2,583.65 and a $2,500 AOTC ($1,000
  refundable), so federal is $2,413.96 better and CA $28 worse ($708 to $736);
  a joint return at MAGI $165,000 with a $20,000 distribution of which $10,000
  is earnings, where $4,000 to the AOTC makes $2,000 taxable and the credit is
  $1,625 (ratio 0.65), federal tax rises $440 at 22% and CA $186.00 at 9.3%,
  household $999.00; two LLC students at $6,000 price $2,000, and a candidate
  giving one student both credits, or more than its adjusted expenses across
  the credit and the 529, is refused; and Example 2's family, the student the
  recipient on another return, is not ranked. Done when each refusal and the
  joint MAGI turn a test red when reverted, `capabilities.py`'s
  `education_credits` note drops "the scholarship-election optimization is not
  modeled", and `docs/contributing/skill-catalog.md` lists the module. Found by
  the 2026-11-18 external review. [IRS Publication 970,
  TY2025](https://www.irs.gov/pub/irs-prior/p970--2025.pdf) · [FTB 2025
  Personal Income Tax Booklet, Credit
  Chart](https://www.ftb.ca.gov/forms/2025/2025-540-booklet.pdf) · [FTB 2025
  Schedule CA (540)
  instructions](https://www.ftb.ca.gov/forms/2025/2025-540-ca-instructions.html)
  · [FTB 3805P instructions,
  2025](https://www.ftb.ca.gov/forms/2025/2025-3805p-instructions.html) · [FTB
  2025 Tax Table](https://www.ftb.ca.gov/forms/2025/2025-540-taxtable.pdf)

- **AA126 (P2, M): Price the single-year Roth ladder with the right Medicare year
  and income.** One pull request. `build_ladder` in `agents/lib/roth_ladder.py`
  reads IRMAA tiers from the conversion year's own `tax_law_<year>.json`
  (`_irmaa_tiers(tax_law, ...)`), whose `federal.irmaa` holds that year's
  premiums (`tax-setup/references/tax-law-fetch.md`: "the named PREMIUM year's
  figures"), passes `premium_year=year`, and tests the rung's AGI
  (`scen["agi"]`). Medicare sets the premium from income two years earlier, and
  its MAGI is AGI plus tax-exempt interest. Reproduced on main with the
  `tests/fixtures/roth` MFJ return (AGI $200,000) swept $0-$100,000 by $10,000:
  the IRMAA crossings are at $20,000 (MAGI $220,000 > $212,000) and $70,000
  (> $266,000), and setting `form_1040.line_2a_tax_exempt_interest` to $60,000
  leaves both unchanged. With Medicare MAGI ($260,000 at $0) they are at $10,000
  and $80,000. `roth_plan.build_plan` already does this for the multi-year plan:
  `premium_year = year + 2`, tiers from that year's law
  (`read_irmaa_tiers(premium_law, ...)`), `magi = agi + irmaa_magi_addbacks`
  (refused below line 2a in `_anchor_checks`), and the surcharge times
  `sum(medicare_months_two_years_later) / 12`. `projection.exposure.project_irmaa`
  reads the same premium-year cache, `tax_law_<year+2>.json`, and checks its
  `tax_year` and `lookback_years == 2`.
  **The change.** `build_ladder` takes a `premium_law` argument (dict or None);
  `_main` reads `tax_law_<year+2>.json` from the workspace when it exists.
  Validate it as `project_irmaa` does (move that check into
  `tax_computations/federal/irmaa.py` and call it from both), and never read
  tiers from the conversion year's cache. A missing file, an empty table or no
  table for the status sets `irmaa_modeled` false with the banner "IRMAA not
  modeled for premium year {year+2}": the output is advisory and the omission
  under-states the cost (Q8). Each rung gains `medicare_magi` (rung AGI plus
  line 2a); crossings, the top-tier `≥` label and `irmaa_tiers_reached` use it
  and `premium_year=year+2`. Add CLI flags `--medicare-enrollees {0,1,2}`,
  `--medicare-months` (one 1-12 count per enrollee, comma-separated) and
  `--medicare-parts-b-and-d-confirmed`, validated as `roth_plan.validate_plan`
  does (two enrollees only on MFJ, one count per enrollee, Parts B and D
  confirmed for a positive count). With them each rung gains
  `medicare_premium_cost`: (surcharge at the rung's MAGI minus the surcharge at
  $0) times the summed months over 12, null without the flags or the table. Add
  a "Medicare premium cost (premium year {year+2})" column beside the federal
  and state columns and keep it out of `combined_cost`. The state columns stay
  as they are: `price_state_points` already prices CA separately for reviewed
  years (`_REVIEWED_YEARS`, CA 2025), and TY2026 is AA84's. A filer with
  `lives_abroad` or `bona_fide_territory_resident` true gets no Medicare figure
  and a named reason, since excluded income adds back to Medicare MAGI and the
  return does not record it. A TY2026 ladder prices premium year 2028, which is
  not published until late 2027, so it reads "not modeled"; the multi-year
  plan's explicitly named hypothetical `premium_law` is the route for an
  estimate, and the ladder accepts fetched law only.
  **Premium tax credit.** No engine path computes Form 8962 (AA120 step 2
  records a reviewed one), so no rung can be repriced for it. When
  `has_marketplace_insurance` is true, print that a conversion raises household
  income (AGI plus tax-exempt interest, untaxed Social Security and excluded
  foreign earned income, Form 8962 instructions) and the credit is not repriced, so the cost can be higher.
  Repricing waits on a Form 8962 computation after AA120.
  Tests in `tests/test_roth_ladder.py`, with a synthetic `tax_law_2028` premium
  law beside the fixture: (1) line 2a $60,000 and the fixture's MFJ tiers as
  2028's: crossings at $10,000 (MAGI $270,000) and $80,000 ($340,000); with two
  enrollees at 12 months, costs of $3,600 ((2,800 - 1,000) x 2) and $5,000; (2)
  no line 2a, 2028 thresholds $12,000 higher than the fixture's: crossings at
  $30,000 and $80,000, the same whatever the 2026 cache's `federal.irmaa` holds;
  (3) no premium law, the fixture's populated 2026 tiers present: no IRMAA
  crossing, every `medicare_premium_cost` null, the banner names 2028, and a
  premium law whose `tax_year` is not 2028 is refused; (4) on test (1)'s file
  (line 2a $60,000), enrollment [12, 6]: $2,700 at $10,000, and two enrollees on a single return, a missing months
  count or unconfirmed Parts B and D refused; (5) a Marketplace profile prints
  the credit warning. `test_filing_year_reuses_existing_ladder_exactly` in
  `tests/test_roth_plan.py` stays green. Done when each new rule turns its test
  red when reverted, and `tax-roth-convert/SKILL.md` (the IRMAA prerequisite,
  step 1, "How to read it" and the caveat "MAGI approximated as AGI"),
  `roth_ladder.render_markdown`'s caveats, the fetch reference's
  "income-year-plus-two cache" sentence and `docs/contributing/skill-catalog.md`
  ("MAGI≈AGI", the IRMAA degrade) say which year and income are used. Found by
  the 2026-11-18 external review. [SSA POMS HI 01101.010, Medicare MAGI and
  lookback](https://secure.ssa.gov/POMS.NSF/LNX/0601101010) · [SSA POMS HI
  01101.020, IRMAA tables](https://secure.ssa.gov/POMS.nsf/lnx/0601101020) ·
  [CMS, 2027 Part B premiums](https://www.cms.gov/newsroom/fact-sheets/2027-medicare-parts-b-premiums-deductibles)
  · [IRS Form 8962 instructions, 2025](https://www.irs.gov/instructions/i8962)

- **AA127 (P1, M): Require transaction coverage for harvest recommendations.**
  Step 1 shipped in #128: no lot clears §1091 until a `transaction_coverage`
  statement covers the whole file. Step 2 shipped in #146: a purchase of another
  ticker inside the window needs a reviewed `wash_sale_classifications`
  decision, and a lot whose replacement purchase another lot also matched is
  held when that lot is fully blocked, held back or a different ticker. Step 2b
  shipped in #158: `tax_advantaged` is required on every lot and `owner` on a
  separate return; a quantity-less purchase is a lot's own only alone at its
  key; same-day lots match by loss per share; a lot the plan does not sell
  leaves its own purchase to replace the others, which are held; the history
  lists every share bought in the window; and MFS in a community-property state
  is refused (Form 8958). Steps 3a and 3b change the report only and can land
  in either order; step 4 changes the matcher and follows 3a. Each is one pull
  request in `agents/lib/loss_harvest.py` and `tests/test_loss_harvest.py`;
  step 3b also edits `agents/lib/tax_computations/__init__.py` and
  `tests/test_tax_computations.py`.
  **Step 3a (tier 5), the sale the plan assumes.** Reproduced on main with the
  harvest fixture: with `as_of` omitted, `--today` 2026-12-01, coverage from
  2026-11-01 through 2026-12-01 and a 100-share VTSAX buy listed for
  2027-01-05 in Schwab Brokerage, lot L1 reads clear and
  the plan saves $1,886.25; the same file with `as_of` 2026-12-20 blocks L1 and
  saves $1,464.75, and nothing says the figures hold only for a sale on
  `as_of`. The fixture with an `as_of` of 2027-01-04 is priced on the TY2026 return ($2,097),
  though the loss belongs to the year of the trade date. The report never
  dates the post-sale window; its caveat says "for 31 days". In
  `build_harvest`, refuse an `as_of` outside the tax year, naming both (the WA
  pricer's own `as_of.year != year` degrade then cannot be reached; remove
  it). Add to `HarvestReport` and `_report_to_dict`: `as_of_source`
  (`"holdings"` or `"today"`); `window_closes`, `as_of` plus 30 days, the last
  day a purchase replaces the sale; `no_buy_tickers`, each eligible lot's
  ticker and every ticker `wash_sale_classifications` pairs with it as
  `identical`, sorted; and `sale_valid_through`, the earliest of December 31 of
  the tax year, 31 days before the first listed purchase dated after
  `window_closes` whose ticker is not reviewed `not_identical` to an eligible
  lot's, and each short-term eligible lot's last short-term day (`acquired`
  plus 365 days, as `derive_holding_period` counts). Give `HoldingLot` a
  `sell_with` list: in `_apply_wash_sale_prorate`, a same-ticker purchase whose
  `own_lots` the lot is not in set those shares aside on the assumption that
  those lots are sold, so the lot is clear only if they are. `render_markdown`
  states the sale date and its source in "What this models", says the figures
  hold for a sale from `as_of` through `sale_valid_through` and that a later or
  earlier sale needs a re-run on its trade date, replaces the "for 31 days"
  caveat with "do not buy {no_buy_tickers}, in any account the file pools,
  through {window_closes}", and adds a "Sell with" column to the harvest plan.
  Tests: (1) the fixture gives `window_closes` 2027-01-14,
  `sale_valid_through` 2026-12-31 and `no_buy_tickers` AAPL, TSLA, VTSAX, and
  the markdown names 2027-01-14; adding a 100-share VTSAX buy on 2027-01-20 in
  Schwab Brokerage moves `sale_valid_through` to 2026-12-20 and leaves
  `total_savings` at $1,886.25, and a META buy that day (reviewed
  `not_identical` to all three) leaves it at 2026-12-31. (2) A short-term lot
  acquired 2025-12-20 gives 2026-12-20: 365 days, still short; 2026-12-21 is
  366. (3) The omitted-`as_of` file above gives `as_of` 2026-12-01,
  `as_of_source` "today" and `sale_valid_through` 2026-12-05. (4) On TY2026,
  `as_of` 2027-01-04 on the fixture and 2025-12-31 on a one-lot file acquired
  before it are refused, each `pytest.raises` matching the message, which
  names `as_of` and the year. (5) VTI lots OLD (acquired
  2025-01-10, 100 shares, $2,000 loss) and NEW (2026-12-01, 100 shares, $1,000
  loss, its buy listed) give OLD `sell_with` ["NEW"] and NEW none, figures
  unchanged; the control with NEW at a gain blocks OLD fully, as on main. Done
  when each rule turns its test red when reverted, and
  `.agents/skills/tax-harvest/SKILL.md` says `as_of` must fall in the tax
  year and the plan holds through `sale_valid_through`, and action plan step 5
  points at the dates the report names, with no new dollar figure
  (`test_portability.py` allows `$0` and `$3K` in this skill).
  **Step 3b (tier 5), effects by year.** Reproduced on main: with a baseline
  Schedule D line 7 of -$10,000, a $7,000 carryforward already, the fixture's
  $6,250 harvest saves $0 this year, and the report's one carryforward row
  reads $13,250, the baseline's included, with no character. The fixture's
  $750 TSLA disallowance appears only as "Unresolved" in the basis table, and
  `_report_to_dict` totals no deferred or permanent amount. Add
  `capital_loss_carryover(net_short_term, net_long_term, loss_limit,
  taxable_income)` beside `net_capital_gains` in
  `agents/lib/tax_computations/__init__.py`: the Capital Loss Carryover
  Worksheet, lines 1-13, returning the short- and long-term carryovers, with
  taxable income allowed below zero as the worksheet's line 1 is. Add
  `effects` to the report: `current_year` (federal, state and total savings for
  the tax year, as today); `carryforward`, baseline and after by character from
  the keystone's taxable income and `_capital_loss_limit`, and `added`, after
  less baseline, with `lower_bound` true when the keystone's taxable income is
  0, since it floors a line 1 the worksheet takes below zero; and
  `replacement_basis` over eligible lots' adjustments only: `deferred` (sum of
  `basis_increase`), `permanent` (sum of `permanent_loss`) and `unresolved`
  (the `disallowed_loss` of adjustments whose `basis_increase` is None), which
  sum to those lots' disallowed loss. `capital_loss_carryforward` becomes the
  worksheet's total. `render_markdown` labels the total "this year", adds rows
  for the carryforward added by character and the three basis amounts, none
  added into it, and marks a basis-plan row for a lot outside the plan "not
  sold by this plan". Tests: (1) in `tests/test_tax_computations.py`, line 7
  -12,500 and line 15 -3,750 give 9,500 and 3,750; -1,000 and -5,000 give 0 and
  3,000; 2,000 and -7,000 give 0 and 2,000; -8,000 and 0 at taxable income
  -2,000 give 7,000 short-term, where the loss less the cap is 5,000; a 1,500
  limit with -5,000 and 0 gives 3,500. (2) The -$10,000 baseline gives a
  current year of $0, added short-term $2,500 and long-term $3,750, after
  $9,500 and $3,750, and the two existing cap tests
  (`test_build_harvest_loss_exceeds_gains_triggers_3k_cap_and_carryforward`,
  `test_build_harvest_mfs_uses_1500_loss_cap`) keep $7,000 and $8,500 long-term.
  (3) The holdings of
  `test_wash_sale_basis_plan_separates_taxable_and_ira_replacements` give
  deferred $150, permanent $100 and unresolved $0; the fixture gives
  unresolved $750; an exact-match fully blocked lot gives all three $0 while
  `wash_sale_blocked_loss` is positive. Done when each rule turns its test red
  when reverted, `sh agents/lib/run.sh -m agents.lib.tax_computations` passes,
  and the SKILL.md "Bottom-line table" bullet, `skill-catalog.md`'s "basis-shift
  ... is documented but NOT modeled in v1" (the basis plan shipped) and the
  harvest block of `test-invariants.md` describe the breakout.
  **Step 4 (tier 5), match the sales the plan makes.** A matcher change, so a
  pull request of its own: the two limits #158's review left, and one found
  while writing this step. (a) The plan assumes every eligible lot is sold: in
  3a's OLD and NEW, keeping NEW makes its purchase replace OLD, $2,000 deferred into NEW's basis,
  and nothing prices a subset. (b) VTI lot A1 (100 shares bought 2026-12-01,
  $2,000 loss) and VTSAX lot B1 (10 shares bought 2026-12-03, $100 loss),
  reviewed `identical`, each bought by its own listed purchase, hold each
  other: `unverified_loss` $2,100, harvest $0, though B1's 10 shares can
  replace at most 10 of A1's. (c) The own-purchase exclusion assumes a loss
  passed to a partner the plan sells is recognized on that sale. Add to OLD
  and NEW a 50-share VTI buy in another account on 2026-12-05: main allows
  $2,000, $1,000 on each; matched in the order bought (Pub. 550), OLD's 100
  shares meet NEW's purchase first, its $2,000 moves into NEW's basis, and the
  50 shares then disallow half of NEW's $3,000, so $1,500 is allowed. Bought
  2026-11-25 instead, both give $2,000. The render's "Cross-lot wash sales
  ... under-counted" caveat names (c); it over-states, AGENTS.md §4's
  hard-error direction. Behavior: `holdings.json` takes optional
  `planned_sales`, lot ids, defaulting to the eligible lots recomputed until
  none drops out. Replace the own-acquisition budget and
  `_hold_for_unsold_acquisitions` with matching under Treas. Reg. §1.1091-1:
  losses in the order of sale, same-day sales in acquisition order (paragraph
  (b)); each against the window's purchases in acquisition order (paragraphs (c)
  and (d)), every share counted but the lot's own, each share used once
  (paragraph (e)); a loss matched to shares of a
  lot sold later in that order joins that lot's loss and is tested with it;
  one matched to a kept lot, a purchase with no lot or an IRA is deferred or
  lost as the basis plan records today. Until Q23 is ruled, a loss matched to
  shares sold earlier in that order counts as deferred, the under-stating side. Tests: `planned_sales` ["OLD"] gives OLD fully blocked
  and $0; A1 and B1 give A1 $1,800 allowed with $200 into B1's basis, and B1
  fully blocked; (c) gives $1,500, and $2,000 for the 2026-11-25 purchase; and
  a fuzz over small random files (a seed and file count in the PR) that no
  figure depends on the order of lots or purchases in the file, that allowed,
  deferred, permanent and unresolved amounts sum to the planned lots' losses,
  and that adding a purchase never raises the allowed loss. Done when each
  rule turns its test red when reverted, and the cross-lot caveat, SKILL.md's
  "A lot the plan does not sell keeps the shares its own purchase bought"
  sentence and `test-invariants.md`'s `_hold_for_unsold_acquisitions` line
  describe the new matching, and SKILL.md's holdings schema lists `planned_sales`.
  Found by the 2026-11-18 external review and the reviews of #146 and #158.
  [IRS Publication 550, TY2025](https://www.irs.gov/pub/irs-prior/p550--2025.pdf) ·
  [IRS Schedule D instructions,
  TY2025](https://www.irs.gov/pub/irs-prior/i1040sd--2025.pdf) ·
  [Treas. Reg. §1.1091-1](https://www.ecfr.gov/current/title-26/chapter-I/subchapter-A/part-1/subject-group-ECFR4a3aa9fd5a8fa4c/section-1.1091-1)

- **AA134 (P1, M): Require a reviewed asset inventory for Form 8938.** Two
  steps, each one pull request.
  **Step 1 shipped in #169:** an account missing either value, or with a
  non-finite or negative one, is refused, and the package lists Form 8938 on a
  boolean `form_8938_fatca.required`, refusing false above the living-abroad
  threshold.
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
  findings.** The rating is skill prose; no engine code produces it.
  `tax-review/references/report-templates.md` opens the IRS Officer section
  with "**Overall Risk Level:** LOW / MEDIUM / HIGH" and gives each DIF flag a
  "Risk: {low/medium/high}". `tax-review/SKILL.md` Step 5 prints "Overall
  audit risk level". Step 2 feeds the rating from threshold heuristics
  (charitable over 3% of AGI, Schedule C expenses over 50% of gross, a 100%
  business-use vehicle) and from a web search for "IRS audit rate {year} by
  income level". That is a population statistic, not evidence about this
  return. Its information-return matching, round-number provenance check and
  substantiation gaps are already evidence-based. The workpaper,
  `reviews/{year}-review.md` (Step 4.5), is markdown the agent writes; nothing
  binds it to a return version. A changed `result.json` reopens nothing until
  someone re-runs the whole review. Step 2 also calls its EIC questions "Form
  8867-style checks"; Form 8867 is a paid preparer's form and stays out of
  scope. The binding needed already exists in `material_evidence` (outcomes
  bound to input digests, and `_source_path`/`_dependency` for confined,
  canonical evidence paths). But every open `material_evidence` item holds
  final export, and a review finding is a question for the taxpayer, so it gets
  a store of its own. Two steps, each one pull request.
  **Step 1, findings bound to the line they question.** Add
  `agents/lib/review_findings.py` with a row in `preflight.SKILL_MODULES`;
  a new engine file adds a case to `tests/test_secure_fs.py`'s `engine_files()`
  parametrization, so refresh that MEASURED COSTS row and both sums in
  `.claude/hooks/skill-invariants.sh`. It
  stays out of `workspace.LOCKED_CLIS`: like `yoy_diff --save`, it writes only
  its own file in `reviews/`, which nothing in the pipeline reads back, and
  `tests/test_workspace.py` pins that list both ways. `record` takes `--id`,
  `--line` (a dotted `result.json` path that must exist), `--rule` (an https
  primary source accepted by `tax_law_fetcher._source_host_authoritative`),
  `--evidence` (repeatable, checked by `material_evidence._dependency`),
  `--outcome open|cleared`, `--discrepancy` and an optional `--dollar-effect`.
  It writes `reviews/{year}-findings.json`, with the year from `meta.tax_year`,
  through `secure_fs.write_secure`, after `secure_path` on `reviews/`. Each
  finding stores the line's value when recorded (the IRM lead sheet's "per
  return" figure), the evidence digests, the whole `result.json` digest, and
  the dollar effect, or null for "not calculated". `status [--json]` reports
  each finding as cleared, open, or reopened. A finding is reopened when its
  line's value changed, an evidence file changed or went missing, or the line
  is gone. A change elsewhere in the return leaves it as it was. Refuse
  `cleared` without evidence, `open` without a discrepancy, a non-finite dollar
  effect (`math.isfinite`), and labels or discrepancies that `redact` would
  change. Print digests without `redact`, as `material_evidence` does for
  digest-derived ids. Tests in a new `tests/test_review_findings.py`. A
  substantiated unusual amount clears: `schedule_a.charitable_deductible` of
  9,000 on `form_1040.line_11_agi` of 100,000 is over the skill's 3% prompt,
  and recorded `cleared` with an acknowledgment in `reviews/` it reads cleared.
  A material unsupported position stays open: `schedule_c.line_27a_other_expenses`
  recorded `open` with "no mileage log" reads open. Changing the charitable
  figure to 9,500 reopens the first finding, while changing
  `form_1040.line_1a_wages` leaves it cleared. Editing the evidence file
  reopens it. `cleared` with no evidence, a NaN dollar effect,
  `--rule http://example.com` and `--evidence ../x` each exit 1. A finding with
  no dollar effect renders "not calculated", never $0. Done when each new rule
  turns its test red when reverted, and AGENTS.md §6's `reviews/` line names the
  file.
  **Step 2, the review reports findings.** In `report-templates.md`, replace
  "Overall Risk Level" and the per-flag risk with a Findings table from
  `review_findings status`. Its columns are return line, rule, evidence reviewed,
  open discrepancy, dollar effect and status, and it carries the reviewed
  `result.json` digest at the top. In `SKILL.md` Step 2, rename "DIF Score Red
  Flags" to unusual-figure prompts. Each prompt is a question, and an answered
  one becomes a finding only when the amount is material or unsupported. Delete
  "Audit Trigger Assessment" and its audit-rate search. The skill keeps its
  other "search the web" lines, which
  `test_web_dependent_skills_carry_a_stale_data_degrade` needs. Rename the
  "Form 8867-style checks" to EIC eligibility checks, saying the form is for
  paid preparers. For an income-shaped difference, the dollar
  effect comes from `sh agents/lib/run.sh -m agents.lib.scenario --result
  result.json --year {year} --no-save other_income=<difference>`; otherwise it
  is left empty. Step 5 prints open and reopened counts instead of a risk
  level, and the frontmatter `description` stops promising an audit-risk
  rating. Tests in `tests/test_review_findings.py` read the skill text (not
  `test_portability.py`, a MEASURED COSTS module). No "Overall Risk Level" and
  no "Risk: {low" remain. The template names all six columns. "Form 8867" does
  not appear in a check the skill tells the agent to run. Done when each
  new rule turns its test red when reverted. Found by the 2026-11-18 external
  review. [IRS
  audits](https://www.irs.gov/businesses/small-businesses-self-employed/irs-audits)
  · [IRM 4.10.9, examination
  workpapers](https://www.irs.gov/irm/part4/irm_04-010-009) · [Form 8867
  instructions, Rev. 11/2025](https://www.irs.gov/instructions/i8867)

- **AA142 (P3, M): Close what the reviews of #164-#172 left that can over-state
  a return.** Not yet written to the queue standard. Each gap is done when its
  rule turns a test red when reverted.
  **Excess Social Security.** `_review_related` in `agents/lib/tie_out.py`
  misses two faults in one pair: a misread Box b beside a spelling its rules do
  not relate ("USA" and "United States", an OCR error). `_rrta_bound` takes
  Tier 1 as withheld, the ledger carrying no RRTA compensation, so a railroad
  employer's Tier 1 over-withholding below the maximum is left to the payments
  review.
  **Payments.** A remittance typed once as `balance_due` and again as a
  next-year estimate passes once the balance-due row carries
  `distinct_payment_confirmed`; the IRS payment-history count and total are
  the only control.
  **Special-rate gains.** `input.schedule_d_tax_worksheet` is not required for
  collectibles sold without a 1099-B collectibles mark (physical metal, coins)
  or for a §1202 exclusion. `compute_form_6251` in
  `agents/lib/tax_computations/federal/amt.py` reads no Schedule D line 18 or
  19, so Form 6251 Part III taxes those gains at 0/15/20% and under-states the
  tentative minimum tax of a filer who has them.
  **Charitable gifts.** A household item over $500 not in good used condition
  needs a qualified appraisal (§170(f)(16)(C)) that the substantiation item does
  not ask for, and a vehicle the charity used, deducted at fair market value,
  gets no Form 1098-C attach line.
  **Form 1116.** The line 18 adjustment bound is loose, and line 17 has no bound
  of its own; both pass only through the reviewed workpaper.
  **Form 8938.** The package's ceiling reads `FORM_8938_THRESHOLDS`, from which
  the cache is seeded, rather than the cache.
  Found by the reviews of #164-#172.

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
| **AA123** step 2 | The owner's fresh-session replay of the freshness auditor: the step's seed moves the replay's pinned input (`docs/ops/review-subagent-acceptance.md`, "When to re-run"), or the season-open sitting, whose step 5 is that replay. |

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

**Open, awaiting the owner.** Each is recorded rather than asked mid-flight, as
above; the queue proceeds on the default shown until a ruling replaces it.

| # | Question | Default the queue uses | Item |
|---|---|---|---|
| Q20 | AA110: on a W-2 page whose OMB control number shares the SSN heading's line, an SSN beginning 1545 with its fifth digit unmapped prints eight digits. Drop the exemption there (A), which also refuses a page whose OMB hyphen is unmapped, or keep it only for control numbers listed from the official forms (B)? | A: refuse that page. A refusal is recovered by re-export or a local transcription; a printed identifier is not (AGENTS.md §1). | AA110 step 3 |
| Q21 | AA110: may a whitespace-grouped reading cross into a non-financial named column (Spouse, Note) beside an identifier column? It changes AA109's documented rule. | No step until ruled, and then measured with AA116 step 1's harness first. | AA110 |
| Q22 | AA116: does the layout `SSN \| 850 \| 12 500 \| 1`, which a real cut identifier also produces, stay masked for good, recovered through #134's transcription path? | Yes: step 2 frees only what the harness shows adds no leak and no lost figure. | AA116 step 2 |
| Q23 | AA127 step 4: matched in the order of sale, a loss can meet shares the same plan sold earlier. Does it count as deferred into that lot's basis, or move with that lot's loss? | Deferred, the side that under-states the harvest. | AA127 step 4 |

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
| AA127 | #128, #146, #158 | OPEN: no lot clears §1091 until `transaction_coverage` covers the window (#128); a purchase of another ticker needs a reviewed decision and a shared purchase holds the later lot (#146); every lot states `tax_advantaged`, a separate return each owner, and an unsold lot's own purchase holds the lots it may replace (#158). Step 3 remains in the Open entry. |
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
| AA140 | #159, #160, #161 | Excess Social Security is grouped by W-2 Box b EIN, a positive credit needing one on each of that employee's W-2s; an included 1099-NEC or 1099-K carries its amount, which the consolidated statement must cover, and a statement's own Box 1a is routed and counts as a 1099-K; a balance-due row beside a late installment and a following-year estimate beside line 10 need separate remittances, and line 10 off a joint return needs the Form 4868 ownership. A railroad bound by employer, a mistyped EIN, an included 1099-MISC and two payment row shapes are AA141's. |
| AA141 | #164, #165, #166, #167 | Two or more balance-due rows each need separate remittances confirmed; related employer names with different EINs need a reviewed outcome when merging them lowers the credit; a railroad employee's share is bounded by employer, Social Security counted on that employer's own wages; an included 1099-MISC carries its amounts, which the statement must cover. Two faults in one W-2 pair, Tier 1 withheld below the maximum and a remittance typed as both kinds are AA142's; one included form linked into two statements rests on the series id, and a joint Form 4868 payment typed as a row on both separate returns on the `federal_review` confirmation. |
| AA122 | #168 | OPEN: special-rate gains hold the final package until a reviewed Schedule D Tax Worksheet figure, bounded both ways, is recorded; step 2 computes the worksheet. Collectibles without a 1099-B mark and a §1202 exclusion are AA142's. |
| AA134 | #169 | OPEN: a missing, non-finite or negative account value is refused, and the package lists Form 8938 on a boolean verdict; step 2, the asset inventory, remains. The package's ceiling reading the built-in thresholds is AA142's. |
| AA133 | #170 | Each itemized gift needs its acknowledgment, and over $5,000 per item or group of similar items its appraisal, securities excepted; Form 8283 is listed over $500 of noncash gifts and in a carryover year; a vehicle the charity sold needs its Form 1098-C; a California-only itemizer is held. Intellectual property and inventory are asked for an appraisal they do not need, the safe direction, and the securities and vehicle labels are the reviewer's; a household item not in good used condition and a vehicle the charity used are AA142's. |
| AA124 | #171, #172 | Foreign tax paid is a ledger category; the simplified credit needs every §904(j) condition, deducting or not claiming has a path, and Form 1116 needs each category's lines, Schedule B by origin year and a reviewed outcome, carryovers following one path from compute to rollover. The election's limit compares tax paid rather than creditable tax, and a high-tax kickout can refuse a correct external line 14, both the safe direction; the loose line 18 bound and line 17 are AA142's. |
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
