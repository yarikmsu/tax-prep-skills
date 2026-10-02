# Epic AA — the 2026-09-20 product-owner review

Backlog: `docs/roadmap/BACKLOG.md`. Epic AA began as the seven items a
product-owner review raised on 2026-09-20, the day the roadmap was collapsed to
one file. They are not a feature theme: AA2 and AA3 are process, AA4 and AA5 are
test hygiene, AA6 and AA7 are copy a first public user would hit, and AA1 is the
one tax finding. AA8 to AA10 were filed later from AA3's review rounds, AA11
to AA13 from AA7's scoping and its two reviews, AA14 and AA15 from the reviews
of AA13 and AA12, AA16 from AA6's measurements, and AA17 from AA6's review.

---

## AA1 — the Earned Income Credit is disclosed instead of defaulting to zero

### What was wrong

`form_1040.line_27_eic` had a field, no computation and **no capability rule at
all**. That last part is the whole of what was distinctive about it, and a first
draft of this file claimed something stronger and false — that it was the only
manual-worksheet item the engine computes nothing of. Half the registry is
uncomputed: Forms 5695, 8936, 8839, 2555, 4137 and 8938 are all hand-worked.
Every one of them already had a rule. The EIC did not, so its gap reached no
manifest and no taxpayer.

It defaulted to 0 and flowed into line 33 (`return_builder._LINE_33_TERMS`), so
an eligible filer's refund was short by up to the whole credit with every gate
green.

**Nothing downstream could catch it, and that is what made it a P1 rather than a
documentation gap.** The tie-out gate is the mechanism built to see a dropped
figure, and checks A-D work by comparing the parse ledger to the return. A credit
issues no source document, so there is nothing to ledger and nothing to compare —
the one check designed for exactly this failure is structurally blind to it.

The direction is an UNDER-statement, which AGENTS.md §4 says degrades with a loud
banner. What it had instead was **five** prose sites saying the credit is
uncomputed — `result_template.json`'s `_note_line_27`, the tax-and-credits compute
reference, `docs/contributing/tax-gotchas.md`, `return_builder`'s module docstring
and `projection`'s. A first draft of this file counted two, which undercounted the
prior state in the direction that flattered the change. What none of the five was,
is a manifest line: every one is read by an AGENT and none reaches a taxpayer.
`meta.limitations`, which `/tax-generate` prints on page 1, said nothing.

New York needs stating precisely, because the blunt version is also false.
`state-return-ny.md` already told the agent to put each refundable credit's
amount, IT-215 included, on `meta.limitations` verbatim. That is an INSTRUCTION
an agent may skip; a rule is a line the gate emits whether anyone remembers or
not. And there are **three** New York lines, not the two a first draft carried:
IT-201 line 65 (Form IT-215), line 66 (Form IT-209, the noncustodial parent
credit) and line 70 (the NYC credit). `tax_computations/state/ny.py` names the
pair that gets missed in its own comment — "the noncustodial parent EIC and the
New York City EIC" — and the draft had picked up one of the two while asserting
the set was complete.

### What the ruling already said

**The EIC being uncomputed is a RULING, and this did not re-open it.** The
2026-07-12 review sharpened the persona to a CA resident with W-2 plus equity
comp, investments and foreign accounts, and dropped the EITC to P3 as out of
persona in the same breath that promoted AMT to P1. That decision stands, and the
deliverable here was the disclosure, NOT the computation.

What the ruling did not license is the silence. The guiding principle stated in
the paragraph immediately after it is that this toolkit's differentiator is that
it never silently understates, and that where a gap cannot be built the
requirement is to refuse or loudly degrade. A field defaulting to zero with no
gate, no question and no manifest line is neither.

### What shipped

**One flag.** `flags.may_qualify_for_eic` in `taxpayer_profile_template.json`,
asked by the `/tax-setup` interview in Group 8 beside the other credit probes.
The question screens on SHAPE and states no figure: the credit amounts and the
disqualified-income cap index annually, and the childless age band does not but
has moved by statute, so either kind written into an instruction file reads as
current while belonging to whatever year it was typed in. The interview says to
set it TRUE on a MAYBE — a false positive costs one manifest line, a false
negative costs the credit — and says outright that its own screening list is a
prompt rather than the §32 test, since every omission from it biases toward YES.

**One capability rule.** `earned_income_credit`, MANUAL, covering all four lines.
MANUAL and not UNSUPPORTED is the severity decision: an under-statement degrades
loudly rather than blocking, and a worksheet that legitimately comes out at $0 —
investment income over the cap, say — is a CORRECT return that no gate may
refuse. The rule binds each line to its OWN form and says none of them is a copy
of another.

**One tie-out advisory**, and it is TRI-STATE. `True` is the taxpayer's yes.
`False` is their no and ends it, because firing on every return would bleed the
advisory block dry for the PFIC and second-read warnings that share it. `None` —
a profile predating the question — gets a DIFFERENT advisory saying the question
is unanswered, because silence there is not deference to an assertion: there is
no assertion. `unanswered_pfic_event` is the same shape at blocking level; this
stays advisory because the direction is an under-statement.

The advisory lives in the tie-out rather than in the gate that writes the
manifest for a structural reason. `capabilities.py` cannot see the result: it
runs before `result.json` exists, and its receipt input set is
`taxpayer_profile.json` alone (`receipt._INPUTS`), pinned by
`test_a_result_edit_leaves_the_capability_gate_alone`. A gate that read
`result.json` would go stale on every compute write, which is the cry-wolf shape
this repository has a recorded lesson about.

### What review caught

Three reviewers read the first draft. Every finding below was reproduced against
the primary source before being fixed.

- **An OVER-statement path in the advisory's own remedy.** It said "work the
  covered year's worksheet for each line named above (Pub 596 / the 1040
  instructions)" over a list that includes the New York lines. The New York and
  City credits are not the federal credit, so following that literally puts a
  federal figure on three New York lines — and `compute_ny_settlement` sums them
  straight into the refund. §4 calls an over-statement a hard error. The remedy
  now builds its authority list from the lines actually named, and the comment
  above it claimed that scoping before the code did it.
- **The NYC line was named to every New York filer.**
  `refundable_credits_and_payments` carries no `_include_if`; only the sibling
  `nyc` block is gated on `flags.nyc_resident`. Line 70 now has a gate section.
- **A present section with an absent field went silent.** `_result_total` one
  screen up in the same file settles the same question the other way: "a present
  section always yields a number". Form 1040 always has a line 27 — only the JSON
  key is missing — and `return_builder.build` reads it as `working.get(field, 0)`,
  contributing a real $0 to line 33 either way.
- **A negative figure silenced the advisory** (`value == 0`). An EIC is never
  negative. The federal line is refused upstream by `_check_components`; the three
  New York lines are not, and this gate also runs standalone.
- **`_EIC_LINES` was not pinned to `result_template.json`**, and every fixture is
  hand-built, so the constant and the fixtures could have drifted together. The
  convention existed twice in the same test file.
- **Nothing linked the flag name across the two modules.** Renaming it in the
  template and the rule would have left the suite green while the tie-out read a
  key that no longer existed.
- **`projection/__init__.py` still said `result.json` carries no EIC or ACTC
  field** — false since V1's second slice added both. The sibling the prose sweep
  missed.
- **`/tax-compute` Step 12.7's advisory enumeration** was not updated, though
  `/tax-review`'s was.
- The `docs/contributing/tax-gotchas.md` bullet called the EIC "the largest single
  number on the return". At the plateau the credit is a fraction of earned income
  by construction, so line 1z is always larger. Carried-forward wording, re-asserted
  and now corrected.

### What a second review caught

One local Codex pass over the corrected branch, two findings, both reproduced:

- **Deleting the New York credit block silenced the advisory; emptying it did
  not.** The scope check read the IMMEDIATE parent, so a `new_york_it201` with
  no `refundable_credits_and_payments` inside it reported nothing. It now scopes
  by the return's top-level section.
- **A federal NO suppressed the noncustodial parent credit.** Form IT-209 has its
  own eligibility test, not the federal EIC's, so a flag worded as a federal
  screen let a filer who fails the federal test leave line 66 silently at 0. The
  interview now asks for a YES from a New York noncustodial parent paying
  court-ordered support, and the rule's situation names that credit.

A third pass found one more, in the interview's list of what the prompt leaves
out: it stated the more-than-half-year US home requirement as universal. That is
the test WITHOUT a qualifying child; with one, it is the child's residence with
the filer, and a birth or death in the year still counts. Read as written, it
let an agent screen an eligible filer who arrived mid-year with a newborn to NO,
and a NO silences both disclosures. The list now scopes the rule and says none of
it is a reason to answer NO.

### Two things deliberately not done

**Not registered in `answers.REQUIRED_ANSWERS`.** A registered answer BLOCKS
(`answers._main` returns 2 while one is owed) and the interview asks for a TRUE on
a MAYBE. Coupling the two makes the liberal answer the expensive one and teaches
people to answer no, which destroys the screen the rule depends on.

**The tie-out does not reject a non-bool flag the way `capabilities._flag_value`
does.** A reviewer proposed aligning them. Reproduced instead: `capabilities`
exits 1 with `flags.may_qualify_for_eic must be true/false, got str: 'true'` at
Step 0.7, long before the tie-out runs, so the stated failure — both disclosures
vanishing silently — does not occur. Changing `is True` for this one flag while
`has_pfic_taxable_event` beside it keeps it would create the inconsistency the
finding is about. A present non-bool reads as UNANSWERED here, which is the loud
direction.

### What it is not

Not a computation, and not a step towards one. No EIC table, threshold, cap or age
band is recorded anywhere in this change; a test enforces that over the rule, the
advisory and both JSON notes, exempting only `$0`. `/tax-review`'s H3
due-diligence block, which already carried the screening criteria, gains a pointer
to the two runtime signals and the note that their ABSENCE is itself a review
finding: if the H3 checks suggest eligibility and `may_qualify_for_eic` is false,
the screening question was answered wrong.

---

## AA3 — MEASURED COSTS records ratios, not seconds

### What was wrong

The MEASURED COSTS block in `.claude/hooks/skill-invariants.sh` required every
absolute timing to be re-measured in ONE sitting on an otherwise-idle machine.
Three of the sittings on record failed it: one because edits landed mid-sweep,
one to host memory, and the most recent because an endpoint scanner holds more
than a core whenever a pytest pass opens thousands of files. That is a feedback
loop, and no time of day fixes it. This host could not reliably meet the
precondition, so the contract was the blocker and not the machine. Meanwhile the block had grown to about fourteen hundred
lines, two thirds of them a ledger of sittings and discarded readings.

Two facts decided it. From one sitting to the next, `pytest tests/` on this host
read more than a third slower on a suite that had gained six tests, with no code
change to explain it. And nothing that READ the block needed an absolute: every
check and decision rests on a ratio or an ordering ("dominates every total",
"one of the cheap rows"), and the two test comments that did restate a reading
were stale copies, swept here. The one quantity that matters as a number,
headroom against the hook's timeout, was never read off the seconds.
`test_the_skill_hook_has_headroom_over_its_measured_runtime` derives its floor
from the COLLECTED count times a per-test budget, and collection is
deterministic.

This section first gave the evidence as the suite's slowest reading ever
against its fastest, nearly two and a half times apart. The suite had doubled
between those two readings, so per test the spread was about a fifth and the
comparison mostly measured growth. A review caught it. The consecutive-sitting
jump above is what the record supports, and the ruling rests on that and on
the three failed sittings on record.

### The ruling (Q13, 2026-09-21)

Option (a): counts, ratios and relative ordering; no absolute seconds. The hook
timeout and the per-test budget remain as compared thresholds, the exemption
CLAUDE.md already grants a budget. One cold pass is re-timed only when either
threshold changes, and that reading goes in the description of the change that
moves the threshold, not in the block. The per-test budget is two constants, a
per-test rate and an absolute minimum under it, and a change to either counts:
CLAUDE.md's exemption already covers any threshold the code compares against.

Option (b), a protocol that records the load each reading was taken under, was
declined. Annotating weather does not make a reading taken under one load
comparable with a reading taken under another. It also kept everything that made
the block expensive: the single sitting, the hand refresh, and the killed runs.

### What shipped

- The block is rewritten as counts, ratios and ordering. The ratios come from the
  last complete sitting, and each is stated in words ("about a seventh", "under a
  fiftieth"), because one lifted to three significant figures would be wrong by
  the next reading. No order is claimed among the four cheapest modules: they
  regrouped between sittings with nothing touched in between.
- The sitting ledger, the "two trees" annotation and the counts-ahead paragraph
  are deleted. Git history holds every sitting and its readings.
- `test_the_measured_costs_counts_match_live_collection` keeps every count
  check it carried: both tables, the reconciliation sentences, the EXACT sums,
  the skip counts and the stray-`N tests` sweep. The seconds cross-checks are
  gone with the seconds, and so are the counts-ahead word-pair parser and its
  helpers. It now also compares the RUN half of the portability and hooks
  reconciliation sentences with their rows, which neither it nor the version on
  main did. Mutation-checked: a count off by one, a drifted agent-definition
  row and a stale `runs N` each turn it red.
- `test_the_hook_states_no_absolute_timing` holds the ruling. It began as a list
  of timing spellings, and four review rounds each found one the list missed
  (`150-second`, `thirteen seconds`, `half a second`, `2m30s`). So the block may
  no longer use the words of duration at all, the ordinal `second` included,
  and the list is gone. A fifth round found the gap the word rule leaves: a
  unit glued to its number (`150sec`, `2min`) has no word boundary in front of
  it, so the figure rule runs inside the block as well, and it now takes any
  unit word and a leading-dot decimal. Outside the block, where those words
  are ordinary prose, it rejects a number in front of a unit in any comment of
  `skill-invariants.sh`. That found a retry cost stated in absolute figures
  further down the file, now stated against the fast path. Two more copies, in
  this suite's own comments and outside what the test reads, were swept by
  hand, along with the comments that still quoted text this change deleted.
  Five comments further down the script stated the pass's duration in words
  ("seconds to a minute", "tens of seconds"), understating a cold pass several
  times over; the rule cannot see a duration in words outside the block, so
  they were reworded by hand to describe the pass rather than time it.
  Mutation-checked: `2m30s`, `150sec`, `.5s`, `a dozen seconds`,
  `150 wall-clock seconds`, `150 µs` and the ordinal inside the block, and a
  figure above the block and the old retry figure below it, each turn it red;
  `a second pass` in a comment and a duration in a line of code stay green.
- A sixth review round found three gaps in the checks and three stale
  sentences. The stray-`N tests` sweep read the raw block, so a numeral and
  its `tests` either side of a line break went unchecked; this change had
  deleted the paragraph recording exactly that, and the sweep now reads the
  joined text. The span required its header once but not its end marker, so
  a quotation of the marker inside the block would have handed everything
  after it to the looser outside rule; both must now appear once. And the
  count readers kept the first copy of a restated sentence, so a second,
  stale copy was never compared; every copy must now agree. The timing rule
  learned `millis`, a glued `us` and, inside the block, clock notation, and
  stopped reading a tax form's plural (`W-2s`, `$1M`) as a timing; a bare
  `1099s` still reads as one and is written `1099 forms`. By hand: two more
  comments that timed a pass as "seconds", the suite's absolute runtime in
  `agents-compat.md`, and the doc copy of the fast path's "milliseconds" in
  `claude-automation.md`. The block also said the counts test is not
  selected by this hook, which is false for every edit that runs the hooks
  module, and five places said it goes red on a test added to one of six or
  seven modules, where the suite check makes it any test at all. The block
  now also says what each row times, which the deleted ledger had carried.
  Mutation-checked: a count split across a line break, a quoted end marker,
  a second stale `collects N and runs M` sentence, a second stale suite
  pair, `150us`, `150 millis` and `2:35` each turn a test red; `W-2s`,
  `K-1s` and `$1M` stay green, and the range `1.0-1.8s` is still caught.
- A seventh round found `millisec`, `microsec` and `nanosec` passing both
  rules, the prefixes having attached only to `seconds`. The unit list now
  takes the short form after any prefix, and inside the block the chase over
  spellings ends: every numeral there must be one of the block's counts (an
  `N tests` row or a `collects N and runs M` sentence, which the counts test
  holds to a live collection), so a timing is refused for what its number is
  not, whatever unit follows it. The clock-notation pattern from round six is
  subsumed and gone. The same round found the block's reason for the ruling
  overstated (see What was wrong), its re-timing rule stated three ways with
  three triggers (it now names the three constants, says an added test never
  triggers one, and `claude-automation.md` points at it rather than restating
  it), its opening forbidding the ratio quotes CLAUDE.md allows, and
  `test-invariants.md` describing a ratio check that no longer exists.
  Mutation-checked: `158000 millisecs` and an unknown unit inside the block,
  `2:35` in a table row, and `150 millisec` and `150millisec` above it each
  turn it red; a count the block already states and a PR number stay green.
- An eighth round found a hole in the seventh's allowlist: it trusted the
  numbers of any `collects N and runs M` sentence, while the counts test reads
  only the portability, hooks and whole-suite ones, so a sentence nothing checks
  could carry a timing. The allowlist now trusts only the sentences the counts
  test reads, through patterns the two tests share. The round also found the
  block naming the wrong sitting for its ratios (W3 slice 2, #164, not the
  V5-era tree), its trigger-with-no-row paragraph true for only the six armed
  test modules, and the ruling's evidence overstated a second time: the record
  holds three failed sittings, one of them to host memory, not three to memory
  alone. The ruling names two re-timing triggers against the block's three; the
  budget is two constants, and the backlog item and the ruling paragraph now
  say so, the Q13 row keeping its words (when AA3 shipped, #173 kept the
  item's point as a note under the backlog's Rulings table). Four
  docstrings in this suite still timed a pass ("10 seconds", "tens of seconds")
  and one sentence in `test-invariants.md` still said the block's seconds carry
  run counts. Mutation-checked: an unread `collects N and runs M` sentence
  carrying a timing turns the timing test red, where the seventh round's test
  passed it.

What the rounds left is filed rather than chased: AA8 stops pinning the whole
suite's count in the hook, AA9 has the hook time itself against its timeout,
and AA10 collects the small leftovers and the timing test's accepted limits.

---

## AA7 — New York City's scope is stated where readers look

### What was wrong

The owner's definition of done for NYC (2026-09-05) was parity with CA, and
Z1-Z3 met most of it, for a full-year resident of New York alone. Three
situations fall outside that and BLOCK at the capability gate: a move between
states during the year (`part_year_residency`), statutory residency in a second
state (`statutory_residency_second_state`), and income sourced to a state
outside the no-income-tax list (`nonresident_source_income`), a City resident
working in New Jersey and a New Jersey resident working in the City alike. Both
wait on Z8. Blocking is the right bias; the defect was that nothing said so.

The two places a reader would look disagreed in opposite directions. The
shipped index read as NYC finished. The README, the first page a public reader
opens, still said NYC was not modeled and did not mention New York at all,
after the module had shipped. The AA7 entry itself named "the
Westchester commuter" among the blocked cases, which is wrong: a Westchester
resident working in Manhattan is a full-year New York resident with all New
York income, and the City has not taxed commuters since 1999.

### What shipped

- The README names both depth states, their shared scope (a full-year resident
  of that state alone) and what stops a return outside it. Florida and the bracket-estimate states follow.
- The Z1 shipped row, the Z8 entry and the state-module playbook state the
  same scope, the playbook with the form each block waits on (IT-203, and the
  IT-112-R resident credit).
- Three places still said NYC is blocked or gated, from before the City tax
  computed: the blocking list in `skill-catalog.md`, the capability module's
  own docstring (which also still called ISO/AMT blocking, when it is a manual
  limitation), and the state-module skill and playbook's "NYC precedent". Each
  now says the City is computed.

### What the first review caught

One local Codex pass found seven things, all confirmed against the code.

- The first draft's scope sentence, "a full-year resident whose income is all
  sourced to that state", was wrong both ways. Income sourced to a state with
  no income tax does not block, and statutory residency in a second state
  blocks with no move and no outside income. The scope is now a full-year
  resident of that state ALONE, with the three blocks named.
- The README and the capability note claimed Yonkers. No engine function
  computes IT-201 line 55 or 56: `compute_city_and_yonkers_total` sums two
  arguments that default to zero, and the compute step gives no formula, so a
  Yonkers resident's return silently omits the surcharge. Both now say the
  Yonkers lines are not computed, and the gap is AA12.
- Line 57 is the part-year Yonkers line, not a City line.
- "Florida is modeled as a no-income-tax state" left out AK, NV, SD, TN, TX and
  WY, which the engine treats the same way. AGENTS.md had the same omission.
- The state-module skill and the playbook said NYC was modeled "in full", with
  part-year City residence uncomputed.
- `residency-guardrails.md` and the tax-review residency reference still
  described `nyc_resident` as a rule and Yonkers as having no flag.
- The joint-return guard AA11 cited is narrower than it said (below).

### What the second review caught

A second local Codex pass found the largest gap of the three: **nothing computes
the New York City resident tax**. `state.nyc_tax` fetches the City's three rate
schedules and the engine never reads them; `compute_city_and_yonkers_total`
takes line 47a as an argument defaulting to zero, and the compute step never
said how to produce it. Z2 shipped City taxable income, the City household
credit, the MCTMT and the line-58 caps, was recorded as "lines 47-58", and the
`nyc_resident` block was deleted on the strength of it. The first draft of this
change repeated the claim. The README, the capability note, the compute step,
the interview, the playbook and the Z1 and Z2 rows now say the City resident tax
is not computed. The compute step tells the agent to leave it out and name the
gap on `meta.limitations` rather than improvise it, which makes the omission
section 4's degrade, an under-statement with a banner. The computation is AA13,
and the Z2 slice in `epic-Z.md` carries a dated correction rather than a
rewritten history. A test had pinned the old claim: the note was required to say
"47-58" and "Yonkers". It now requires the note, and the compute step, to name
line 47a and the Yonkers lines as NOT computed, and restoring the old note turns
it red.

The same pass found the note and three documents calling IT-201 line 44 the
IT-230 lump-sum input, when the engine computes line 44 as tax less credits and
the lump-sum tax belongs to line 45's IT-201-ATT; "the other no-tax states"
standing for a list the engine does not have (NH and WA are deliberately left
out of it), now named as the seven it does have; AA12 claiming no banner when
this change had just added one; two leftover "all sourced to that state"
phrasings; and a residency paragraph that still gave the Yonkers answer to the
City's flag. A stale "NYC (still gated)" in a dated design spec was left as the
record of its day.

### Found, not fixed

A City resident for part of the year who stayed in New York is not blocked: the
part-year City line 50 is not computed, and the return names the gap. A single
filer is asked only whether they are a City resident, so one who lived in the
City for half the year can answer yes and have City taxable income taken for the
whole year, which over-states the City tax the moment it is computed. A joint
return is asked how many spouses were residents for the whole year, but only a
count of one is refused, so a couple who both lived there part of the year is
treated the same way. Both are code changes, filed as AA11 (P1), which has to
land with or before AA13. The City resident tax is AA13 (P1), and the Yonkers
lines AA12 (P1). Whether the `nyc_resident` block should return until AA13 ships
is an owner question.

---

## AA13 and AA11 — the New York City resident tax, for whole-year residents

### What was wrong

Nothing computed IT-201 line 47a. `state.nyc_tax` fetched the City's three rate
schedules and the engine never read them; `compute_city_and_yonkers_total` took
the City tax as an argument defaulting to zero, and the compute step never said
where it came from. So a New York City resident's return carried a City tax of
zero, while Z2 was recorded as shipping lines 47-58 and the `nyc_resident` block
had been deleted on the strength of it. AA7's second review found it.

The part-year guard had the same hole from the other side. A single filer was
asked only whether they were a City resident, and a joint return was refused
only when exactly one spouse was a whole-year resident, so a City resident for
part of the year had the whole year's income taken as City taxable income. With
the City tax at zero that over-statement was latent; computing line 47a makes it
real, which is why AA11 ships with AA13 and not after it.

### What shipped

- `compute_nyc_resident_tax` computes line 47a. Below the fetched ceiling it
  reads the City TAX TABLE, which is the State's method, the schedule at each
  row's midpoint rounded half up, on the City's OWN rows; at or above the
  ceiling, the rate schedule. The City's bottom rows are $0-18 and $18-25 where
  the State's are $0-13 and $13-25, so reusing the State's rows puts a $15 City
  return in a row that owes a dollar. There is no New York AGI condition on the
  City table and no City recapture. An unfetched schedule or table refuses
  rather than returning zero.
- `state.nyc_tax.tax_table` joins the fetched schema with its own
  `max_taxable_income` and `row_segments`, and the fetch step asks for the City
  table's rows from the City table and for the published base on every band.
- The compute step wires line 47a into line 52; `line_47a_nyc_resident_tax`
  joins the result template and the `/tax-verify` map.
- AA11: `nyc_resident_full_year` is asked of every non-joint City resident and
  `compute_nyc_taxable_income` refuses a false or absent answer; a joint count
  of zero whole-year spouses refuses too. Two tests encoded the old behaviour,
  one accepting a joint zero and one computing a single filer with no answer,
  and both were rewritten with the reason they were wrong.
- The README, the capability note, the playbook, the skill catalog and the
  state-module skill say the City tax is computed, for a whole-year resident,
  and the Yonkers lines (AA12) stay named as not computed. The note test now
  requires exactly one "NOT computed", so the City tax cannot drift back onto
  that list unnoticed.

### Verified against the source

The schedule is IT-201-I (2025) p.40, read from the PDF itself: 3.078%, 3.762%,
3.819% and 3.876%, with published bases of 665, 1,545 and 3,264 (joint), 369,
858 and 1,813 (single and separate) and 443, 1,030 and 2,176 (head of
household). The table rows are the published 2025 NYC table: its own worked
example (joint, $38,275, $1,292), both $36,000 and $45,000 rows in all three
columns, and the bottom rows. A summarising fetch reported a $64,950 row of
$1,939 that the published page does not carry and the schedule contradicts; it
was not used.

### Mutation checks

Each turned a test red and was restored by hash: always the schedule, always the
table, the row floor for the midpoint, the State's key named for the City's
table, a joint zero accepted, a non-joint absent answer assumed whole-year, a
non-joint part-year answer accepted, an unfetched schedule returning zero, and
the note listing the City tax as not computed again.

### What the local review caught

A local Codex pass found the schedule rounded twice. `_ny_schedule_tax`
returned its tax rounded to cents and every caller rounded that to the dollar,
so a single filer with line 47 of $71,504 (1,813 + 3.876% x 21,504 =
2,646.49504) got $2,647 where the form gives $2,646. The review named the
schedule; the table had the same defect, because it evaluates the schedule at
each row's midpoint, and its $3,500-3,550 row came out 109 against a published
108. The City's rates run to five decimals, so the cents land in that window
often, and the error is always upward. The schedule now returns the unrounded
tax and each path rounds once, to the dollar. The recapture worksheets enter
the schedule tax as a line in cents, and round it there as before.

Every cell of the published 2025 City table now matches the engine: 3,882 of
them, 24 of which were a dollar high. The State table's results did not move.
The same comparison found the State's joint column a dollar LOW on 22 rows, on
`main` before this change; that is AA14. Mutation-checked: restoring the cents
rounding turns seven new tests red.

### Not done

The Yonkers lines are AA12. The City school tax credit's rate-reduction amount
(line 69a) stays a refundable-credit input like the others, from the fetched
table. A part-year City return (IT-360.1) is refused, not modeled.

---

## AA12 — the Yonkers taxes, for a whole-year resident or nonresident

### What was wrong

Nothing computed IT-201 line 55 or 56. `state.yonkers_tax` fetched the resident
surcharge rate, the nonresident earnings rate and its exclusion, and nothing
read them; `compute_city_and_yonkers_total` took both Yonkers amounts as
arguments defaulting to zero. A Yonkers resident's return carried no
surcharge, and one who worked in Yonkers while living elsewhere in New York
carried no earnings tax. AA7 named the gap on the capability note, which made
it an under-statement with a banner rather than a silent one; the amounts were
still wrong.

### What shipped

- `compute_yonkers_resident_surcharge` computes line 55 with the IT-201-I
  Yonkers worksheet: line 46, less the refundable credits the worksheet lists,
  net of the STAR reconciliation amount, times the fetched rate. The credits
  are named by the form line the worksheet reads them from, not by IT-201's
  credit lines, because line 64 carries the City's child care credit beside
  the State's and the worksheet takes the State's alone. A return with no
  State tax owes no surcharge, and credits above the tax give zero rather
  than a negative tax. The credits and the STAR amount are both required.
- `compute_yonkers_nonresident_earnings_tax` computes line 56 with Form Y-203,
  in whole dollars as the form prints it: Yonkers wages and self-employment
  earnings (a loss enters as zero), less START-UP NY wages, less the exclusion
  from the fetched twelve-month row of Y-203's table, times the fetched rate.
  Line 6 carries that tax limited to the surcharge the return would owe as a
  Yonkers resident, which is why the function takes the worksheet's inputs
  too. A joint return refuses; see below.
- The AA11 guard, for Yonkers: `yonkers_resident_spouses` on a joint return
  and `yonkers_resident_full_year` on every other one, both asked by the
  interview. One resident spouse (special condition Y1, computed as if the
  couple had filed separately), zero whole-year spouses, a part-year resident
  and an unanswered question all refuse, and so does line 56 for a household
  with a Yonkers resident in it, which is one of those two cases.
- The fetch step records the three figures and the table's row shape; the
  compute step wires both lines into line 58; the README, the capability note
  and the playbook say the Yonkers taxes are computed.
- AA7, AA13 and AA11 move to the shipped index here, with the PRs that
  shipped them, rather than one bookkeeping PR each.

### Verified against the source

The worksheet is IT-201-I (2025) p.17, read from the PDF: lines a through o,
the rate printed on line n (16.75%), and the special condition Y1 instruction
beside it. Form Y-203 (2025) and its instructions: lines 1 through 6, the
0.5% rate on line 6, the exclusion table on the form's front (3,000, 2,000 and
1,000 for twelve months, none over $30,000), "if a loss, enter 0 on line 2",
one form per spouse, and the limitation to the resident surcharge. Form IT-215
line 17 is the married-filing-separately line the worksheet's line e names.
Worksheet line g is the tuition credit "if you elected to claim" it. Y-203-I's
limitation ends "Enter the Yonkers resident income tax surcharge on line 6 of
Form Y-203", and the form prints ".00" on every line.

### A decision the instructions leave open

Y-203's limitation says a nonresident need not pay more than they would as a
resident, figured with "the instructions for Form IT-201", and each spouse
files their own form. On a joint return that can be read three ways: the two
forms share the joint surcharge as one cap, each carries it, or each spouse's
cap is the surcharge on their own tax as if they had filed separately, which
is how IT-201 figures a couple with one Yonkers resident (special condition
Y1). The first build capped each form at the joint surcharge and refused only
where the first two readings differed. The review found the third, under which
a spouse earning $10,000 in Yonkers beside a higher earner is charged about
$35 where the separate computation gives about $13, and that computation is
not modeled. So Form Y-203 on a joint return refuses (owner's ruling). Single,
head-of-household and married-filing-separately returns file one form each,
and no reading differs for them.

### What the local review caught

- An omitted `worksheet_credits` read as no credits: $5,000 with a $1,500
  earned income credit gave $838 where the worksheet gives $586. Both it and
  the STAR amount now refuse when absent, the AA11 pattern.
- A capped Form Y-203 still showed the uncapped tax on line 6, where the
  instructions put the surcharge.
- A STAR amount above the credits it comes out of pushed line m past line 46.
  It refuses, and with it the separate line-46 condition could no longer
  change a result.
- The fetched exclusion rows went unchecked: an open row below the top gave
  $50,000 of wages the $3,000 exclusion, and a string bound raised a TypeError.
- The band was read on cents: $10,000.40 took the $2,000 row, where the form's
  whole-dollar 10,000 takes the $3,000 one.
- The STAR amount was never asked for; the interview now asks, and the
  worksheet refuses without it.
- `child_and_dependent_care` named IT-216 line 14 here and IT-201 line 64 in
  the settlement; the worksheet's key is now `state_child_and_dependent_care`.
- Line g took the tuition credit on a return that took the deduction.
- The tax-law schema said the base was net of the STAR amount, the reverse of
  line k, and the README's scope sentence did not fit line 56.

### Not done

Part-year Yonkers residents (IT-360.1, line 57), special condition Y1 and
Form Y-203 on a joint return are refused, not modeled. The wage allocation (Y-203 Schedules A and C) is an input,
as the MCTMT's zone allocation is.

## AA6 — the UNSUPPORTED list, sized against the named personas

### What was wrong

The capability gate's blocking rules decide who can use the toolkit at all, and
nobody had measured how many filers they stop. The owner named the next users on
2026-09-05: California first, Florida and New York City next, North Carolina and
Seattle after. Rental property blocks, and a Californian or New Yorker earning
well with a rental condo is not an unusual filer. Leaving the list unsized meant
the first public issue would be the first measurement.

### What shipped

- `docs/WHO-IT-FITS.md`, linked from the README's "refuses to guess" bullet. For
  each persona it gives the share of returns hitting each block, at $200,000+
  of AGI and for all returns. It then gives a paragraph per persona and the
  gaps that do not block but matter most for these personas, pointing to the
  skill catalog for the rest. It ends with the method, the sources and what
  each measure misses.
- **The answer:** 16-27% of returns hit a block, depending on the persona, and
  21-29% of $200,000+ returns. Above $200,000 rental property is most of it
  everywhere (11-17% by the IRS's own count, which misses owners whose losses
  are suspended). Moves come second (3.7% of California households a year, up
  to 8.8% in New York City). Florida is the outlier for all returns (27%),
  because 17% of Florida returns took an advance premium credit.
- `test_who_it_fits_sizes_exactly_the_blocking_rules` holds the page to the
  gate's list both ways. A blocking rule the page does not name fails, and so
  does a rule the page names that no longer blocks or no longer exists.
- `scripts/who_it_fits.py` recomputes every figure from the downloaded files
  and saves them in `scripts/who_it_fits_figures.json`. The national
  Publication 1304 counts are entered by hand, with their tables and columns,
  because the lock has no reader for `.xls`. It renders the page's table and
  the phrases its prose quotes from the saved figures, and stops on a download
  whose layout has changed rather than reading a missing row as zero.
- `test_who_it_fits_prints_what_the_script_measured` holds every table cell and
  every quoted figure to that rendering, and
  `test_who_it_fits_comparisons_still_hold` checks each comparison the prose
  makes. Four tests on made-up rows pin the file readers: the income brackets,
  the moves arithmetic, which commutes block, and the stop on a changed layout.
- The page, the script and its figures file each have a selection arm in the
  skill hook, so an edit to any of them runs these tests.

### How it was measured

IRS Statistics of Income data for tax year 2023 gives rental and the advance
premium credit (fields `N25870` and `N85775`), by state and by county for the
five New York City boroughs and King County, and nationally the household
employment tax and Form 8615 counts. IRS migration data for 2022 to 2023 gives
the moves across a state line, leaving out moves abroad, which the gate does
not stop. The Census Bureau's 2016-2020 county-to-county commuting flows give
work in another state, counting every workplace state the gate blocks,
Washington and New Hampshire included. Every figure was
computed from the published files, not read off a summary. The page states each
field and what it misses. The rental field counts rental and royalty net income
or loss. It misses a suspended loss that nets to zero, so nationally it reads
15% of $200,000+ returns where returns reporting any rent are 19%. The page
gives both ends.

### What it found that is not a block

- **AA16.** The main return ignores the SALT cap's MAGI phasedown.
  `compute_schedule_a` deducts the full cap at any income, and its only warning
  is a note printed on every Schedule A. Reproduced: at $700,000 of AGI it
  deducts $40,000 where the statute allows $10,000. `ptet._salt_cap` already
  applies the phasedown. Filed at P1, because 54-64% of $500,000+ returns in
  these states itemize.
- **AA17.** A mid-year move abroad passes the gate: the interview asks only
  about moving states. The first draft of the page said the gate stopped it;
  the review found it does not. Filed at P2, because 0.13-0.32% of households
  a year move abroad or arrive from abroad in these places.
- **The Washington capital-gains excise is not named on the return.** The state
  note says only that Washington is an estimate, and the page says so. Nothing
  new is filed: Z5 already covers the excise, and its affirmative statement
  replaces that note.

### What the review changed

A review of the first draft found the page wrong in ways that all read well.
Besides the move abroad above:

- Several at-least-one cells did not follow from the rows above them, and the
  method behind the range was not stated. The row is now computed from the
  cells as printed, by the script and by a test.
- The kiddie-tax figure was called a ceiling. It counts Form 8615 only, and
  parents who use Form 8814 are blocked by the same flag and counted nowhere.
- The SALT phasedown was given as "above $500,000 (2025 onward)". The
  threshold rises 1% a year by statute, halves for married filing separately,
  and the phasedown ends after 2029, so the page now names the section, not a
  figure.
- The New York City paragraph missed the joint return with one City-resident
  spouse, which the City tax refuses at compute, and called the MCTMT a City
  tax. The commuting row called its states ones "that tax wages", which
  Washington and New Hampshire do not, and its figure is per worker, not per
  return. Two persona paragraphs contradicted the table.
- AA16's entry called the over-deduction a degrade a note could cover. On a
  return, which is not advisory, AGENTS.md §2 allows no degrade; the entry now
  asks for a stop where modified AGI cannot be formed.

A second review found the checks narrower than they read. The page test
passed a blocking rule deleted from the gate outright. The commuting test
compared state letters while the script counted by FIPS code. A measured cell
or a figure in the prose could be edited by hand with every test green, and a
renamed row in a newer year's download would have printed a plausible 0.00%.
The headline said rental was most of the blocks everywhere, which holds only
above $200,000. The script now derives the FIPS codes from the IRS file,
renders the page from saved figures, checks the prose's comparisons, reads the
`.xlsx` through defusedxml, and stops on a changed layout.

A third review found three more. The income readers still accepted a download
missing a bracket or a county: with California's top bracket gone, its
$200,000+ rental share fell from 16.6% to 15.4% and nothing said so. They now
require every bracket and county they read. The page said a block always means
the toolkit cannot produce the return, but an accepted PFIC event is computed
under §1291, unless the fund has a QEF or mark-to-market election or became a
PFIC after it was bought. And it listed two New York refusals at compute and
left out the third, Form Y-203 on a joint return.

### Not done

The page cannot measure three blocks: statutory residency in a second state, a
PFIC distribution or sale, and out-of-state income other than a commute (a
multistate K-1, a sale of out-of-state property, remote work under a
convenience-of-the-employer rule). It names them rather than guessing, and says
the commuting figure is only a floor. The figures describe 2023 returns and need
refreshing when a newer year is published.

## AA16 — the SALT phasedown, on the return itself

### What was wrong

§164(b)(7)(B) cuts the SALT cap by 30% of modified AGI over a threshold, never
below $10,000 (half each on a separate return), for TY2025-2029. `/tax-ptet`
applied it since #157. The return's own Schedule A did not: `compute_schedule_a`
capped at whatever cap it was handed, the compute step handed it the fetched
`federal.salt_cap` amount, and a note saying the phasedown was not modeled
printed on every Schedule A whatever the income. A single filer with $700,000
of AGI and $72,000 of state and local tax deducted $40,000 where the statute
allows $10,000, about $11,000 of federal tax under-stated. AA6 found it; 54-64%
of $500,000+ returns in the five persona states itemize.

### What shipped

- **One rule, in the tax library.** The §164(b)(7) constants and the cap
  function moved from `ptet.py` to `agents/lib/tax_computations/federal/salt.py`.
  `ptet` imports it, and its `_salt_cap` keeps reading the cache and raising
  `PtetError`. Its answers were recorded across 6,244 cases of year, status,
  cache and MAGI before the move and matched once after it, errors included.
  That comparison ran against code no longer in the tree and cannot be re-run;
  what stands behind the claim now is `test_ptet.py`, unchanged and green, and
  the review-subagent acceptance digests, which hash the engine's answer.
- **The return applies it.** `compute_schedule_a` takes `modified_agi`, the same
  figure Schedule 1-A takes (AGI plus Form 2555 lines 45 and 50, Form 4563 line
  15 and excluded Puerto Rico income), and applies the phasedown to the fetched
  cap. For TY2025-2029 it refuses without one
  wherever MAGI could move the deduction: a return is not advisory output, so
  AGENTS.md §2 allows no degrade. The
  every-return note is gone; a note now appears only when the cap was reduced,
  and the result carries `salt_cap_applied` and `modified_agi`, which the
  template records.
- **The instructions say so.** The compute step passes `modified_agi`, and the
  claims that the phasedown was "not modeled" are gone from the compute step's
  optimization reference, `/tax-ptet`'s limitations, the fetcher's comment and
  `compute_schedule_a` itself. `/tax-ptet` had contradicted itself: one step said
  the phasedown was modeled, and the limitations list said it was not. The pin
  in AGENTS.md §2 and its test now name the new module.
- `docs/WHO-IT-FITS.md` no longer lists the phasedown as a gap.

### Tests

Hand-computed from the section: the AA6 case ($40,000 to $10,000), a joint
return whose threshold is not doubled, TY2026's stepped threshold, a separate
return's halved amount, threshold, rate and floor, a dollar under the
threshold and a dollar over it, a loss year, MAGI above AGI, TY2027-2029's
stepped thresholds, and the refusal without MAGI in 2025 and 2029 but not in
2024 or 2030, or where no MAGI can move the deduction. The §70111 haircut test used $1,000,000 of AGI and asserted a
$40,400 SALT deduction, which was the defect written into a test; it now
floors at $10,000.

### Review

A local review found 13 issues. All are fixed:
- **MAGI below AGI was accepted.** $100,000 against $700,000 of AGI kept the
  whole cap, AA16's bug reached by input. It refuses now, and MAGI is
  validated in every year, not only TY2025-2029, so NaN no longer passes in
  2030.
- **One name meant two things.** The result's `salt_cap` was the cap after
  the phasedown and the input of the same name the amount before it, so a
  re-call fed the result would have reduced the cap twice. The result key is
  `salt_cap_applied`, and the compute step says to re-pass the cache figure.
- **The review disagreed with the return.** `/tax-review`'s PTET step ran
  without `--magi`, so its block said the phasedown was not applied beside a
  return that applied it. The result records `modified_agi`, and the step
  passes it, and `--standard-deduction` when the return did not itemize.
- **The note could crash or mislead.** A cap given past the cent was trimmed
  by the rule's rounding and read as a reduction, which formatted a missing
  MAGI in TY2024; a dollar over the threshold printed "$40,000 to $40,000".
  The cap is taken at cents first, and the note prints cents.
- **Two tests guarded nothing.** `test_input_validation`'s four bare checks
  passed on the new "modified_agi is required" error with the check each named
  deleted, and a threshold frozen at TY2026's left every test green. Each
  check now matches its own message, and TY2027-2029 have cases.
- **AGENTS.md §2's exception named only the amounts.** The phasedown's
  threshold, rate and floor now decide the return and have no cache field; §2
  names them and says the cache wins for the amount only, and the pin test
  holds `/tax-ptet`'s statement of all three to the constants.
- **Territory residents.** Three instructions equated MAGI with AGI when
  `lives_abroad` was false, but §931/§933 exclusions belong to residents of
  Puerto Rico and the other territories, who are not abroad. They key on the
  exclusion now, the education credits' MAGI included.
- **Smaller:** the fetch step still called the amount "indexed" and never said
  it is the figure before the phasedown; `/tax-review`, the skill catalog, the
  season-open runbook and the fetcher's comment were stale; the MAGI check
  duplicated `_sched_a_amount`; `/tax-ptet`'s label kept its own year window
  and its `--magi` help quoted a flat 30%; and the 6,244-case claim above read
  as evidence the tree could reproduce.

### Second review

A second local review found 13 more. Nine are fixed here, three are filed as
two backlog items, and one is skipped:
- **The planning tools held the cap fixed without saying so.** `compute_scenario`
  holds deductions at the baseline, so once the return's cap moved with MAGI,
  `/tax-scenario`, `/tax-roth-convert` and `/tax-harvest` priced an itemizer
  above the threshold as if it did not: a conversion there can cost more than
  shown, and a harvest can save more. Each says so while the phasedown runs,
  from one helper, `scenario.salt_phasedown_caveat`. `/tax-ptet` prices the cap
  at the MAGI without the election, which electing lowers, so its phasedown
  note now says the benefit is a floor.
- **The review asked the taxpayer what the engine should price.** `/tax-review`'s
  PTET step asked whether the SALT deduction was "at the cap" and passed
  `--salt-room`, which overrides the cap priced from `--magi`. It now asks for
  the other state and local taxes `/tax-ptet` asks for and passes
  `--other-salt`. The review proposed the return's `salt_before_cap` instead;
  that holds the state tax on the pass-through income itself when the entity
  has not elected, which shrinks the headroom and over-states the benefit, so
  the step says not to use it.
- **The refusal fired where MAGI cannot matter.** Keyed on the year alone, it
  stopped a return with a few thousand of property tax. It now refuses only
  when the tax and the amount are both above the phasedown's floor.
- **Smaller:** the template's `modified_agi` placeholder was 0, which the
  review step would pass as a MAGI, and is null; the below-AGI check compared
  raw floats and compares at cents; `/tax-ptet` validated `--magi` only inside
  TY2025-2029 and now does in every year, as the return does; `/tax-ptet`'s
  skill said a missing `--magi` refuses where the code degrades, and quoted a
  flat "$40K", which also leaves the figure registry; its report intro and
  module docstring still called the cap $10,000; and an engine comment called
  the amount indexed.
- **Filed:** AA18, a territory resident passes the capability gate, so the
  §931/§933 exclusions the compute step names are never collected; and AA19,
  nothing checks the cached amount against the statute, while the freshness
  auditor still calls it indexed (that edit rides Y2's replay).
- **Skipped:** the who-it-fits script still measures the itemizing share for all
  five persona states while the page quotes California's. Its comment says so,
  and a narrower script would save nothing.

### Third review

A third local review found 13. Twelve are fixed here and one is skipped:
- **MAGI left out the foreign housing deduction.** Every IRS worksheet that
  defines it — Schedule A's line 5e, Schedule 1-A Part I, Pub 970's
  education-credit worksheets — adds back Form 2555 lines 45 and 50, Form 4563
  line 15 and excluded Puerto Rico income. Line 50 is a deduction, not an
  exclusion, and "AGI plus the §911/§931/§933 exclusions" left it out: MAGI
  reads low, the cap high, and a filed return deducts too much. The compute
  steps, the engine's docstrings and its refusal now name the four amounts.
  The same passages gave Guam and the Northern Marianas a §931 exclusion, which
  the worksheets give only to American Samoa; a bona fide resident of either
  files with the territory (§935).
- **`/tax-ptet` reads the return.** The MAGI and the itemize choice lived in
  skill prose: `/tax-review` passed them, while `/tax-ptet`'s own procedure and
  `/tax-project`'s call did not, so one election was priced against two caps
  depending on which skill ran it. The analyzer now takes both from
  `schedule_a` where the flags leave them out. A missing section is not read as
  the standard deduction, which would drop the SALT offset and over-state the
  benefit.
- **The "floor" note claimed too much.** It called the benefit a floor on every
  path, including the default one that assumes the cap is used up, which can
  over-state. It says so only where the headroom was derived from
  `--other-salt`.
- **The return recorded a cap it did not know.** Where MAGI may be left out —
  tax at or under the floor — `salt_cap_applied` held the unreduced amount, and
  the summary printed it for a filer whose statutory cap was $10,000. It is
  null there, with a note.
- **Smaller:** the refusal compared a raw float sum with the floor, so cents
  totalling exactly $10,000.00 could refuse, about 3% of random splits;
  `/tax-ptet`'s phasedown note printed whole dollars ("cut the cap by $0"); a
  `--salt-room` larger than the whole cap was priced and is refused; the
  planning caveat, gated on the year alone, reached standard-deduction filers
  far below the threshold, and now shows only where the cap can move or that
  is not known (`scenario.salt_cap_may_move`); the template's
  `salt_cap_applied` placeholder was 0 and is null; and `salt.py`'s header said
  the cache wins for every figure, where it wins for the amount only.
- **Skipped:** taking the excluded amount in place of MAGI. An agent can pass 0
  by reflex as easily as it passes AGI, and the input would part from
  Schedule 1-A's.

### Fourth review

A fourth local review found 11, none of them a figure on the filed return.
Nine are fixed here, one is filed and one is left:
- **The recorded election could be stale.** The compute step redoes the
  itemize-or-standard choice by hand when §170(p) tips it, after
  `schedule_a.use_itemized` is recorded, so `/tax-ptet` could price the SALT
  offset for a filer who took the standard deduction and under-state the
  benefit with no banner. The step now writes the final choice back, and
  `/tax-ptet` and the planning caveat read a positive line 13c as the standard
  deduction, since only a standard-deduction filer has one.
- **The caveat's gate asked the wrong question.** It asked whether any MAGI in
  play crossed the threshold, so it fired where the deduction could not move,
  with a cap at the floor or above the tax throughout. And it measured from the
  immediate base, so a stacked chain that had moved MAGI far from the return
  could drop it. It now compares the deduction at each MAGI in play with the
  return's own, on the cache's amount as the return used.
- **A note said something false.** `/tax-ptet` said MAGI was "at or under" the
  threshold whenever the cut came to nothing, including at $600,000 of MAGI
  against a cap already at its floor. It now says the phasedown leaves the cap
  unchanged.
- **Smaller:** `modified_agi` came back unrounded and is recorded at cents; the
  saved planning JSON now carries the caveat flag; a `--salt-room` is checked
  against the cap only for an itemizer, the only filer it prices; the SALT-cap
  block moved out of `compute_schedule_a` into `_schedule_a_salt_cap`; and this
  section's "What shipped" gave the old MAGI definition.
- **Filed:** AA20, the planning keystone prices every return on the standard
  deduction, which is why the SALT phasedown is a caveat there and not a price.
- **Left:** a standard filer's harvest still carries the caveat. With no
  Schedule A there is no MAGI on record, and a foreign or territory filer's can
  sit far above AGI.

### Not done

Modified AGI is an input. The return forms it the way Schedule 1-A does, and a
foreign earned income exclusion is itself a manual worksheet (the `feie` note).
`/tax-ptet` does not check `--magi` against the return's AGI: a figure too low
leaves its cap too high, which under-states the advisory benefit, the
direction AGENTS.md §2 lets it degrade in.

## AA20 — the planning keystone, on the return's own deduction

### What was wrong

`compute_scenario`, which `/tax-scenario`, `/tax-roth-convert`, `/tax-harvest`
and `/tax-ptet` all price through, subtracted the cache's standard deduction for
every filer, and nothing passed it the return's line 12. The anchor to line 24
corrects the level of a figure, not the position a delta is priced from, so an
itemizer was priced from a taxable-income position too high by the difference.
On the fixtures, $18,600 of PTET for an itemizer with a $200,000 deduction
saved $4,770, and the tool said $6,430; a harvest that saves $1,255 read $1,305.
That is the over-statement AGENTS.md §4 makes a hard error. AA16's fourth
review found it, and AA20 was filed as P1.

### What shipped

- **The deduction is the return's.** `scenario.return_deduction` reads
  `form_1040.line_12_standard_deduction`, and `_rate_kwargs` passes it to the
  keystone, whose parameter is now `deduction`. A missing or corrupt line 12
  refuses with `/tax-compute` named: falling back to the cache is the
  over-statement this replaces. The age-65/blind add-on rides along, since line
  12 carries it, which closes that part of X7.
- **The medical floor is said.** Holding line 12 holds the medical deduction,
  whose floor is a share of AGI in every year, and nothing had said so. The
  three report tools carry a caveat wherever that floor can move the deduction
  (`scenario.medical_floor_may_move`, measured from the return as the SALT gate
  is), and `/tax-ptet` adds a note where the election's lower AGI moves it.
  `compute_schedule_a` now records the gross expenses, `medical_expenses`,
  beside what cleared the floor, because a deducted amount of zero cannot say
  whether a lower AGI would clear it.
- The four planning skills name line 12 as a prerequisite, and their caveats
  describe the deduction they hold.

### Tests

Each tool is pinned at two line-12 values, hand-computed on its fixture: a
scenario delta of $90,000 ($19,200 against $18,000), the first Roth rung
($2,200 against $2,700, the lower position pushing $10,000 of preferential
income from 0% into 15%, so the error runs both ways), the harvest ($1,305
against $1,255) and the PTET election ($6,430 against $4,770).
`return_deduction`'s refusals and the medical gate's cases, each exact where
the gross is known, are pinned too.

### Review

A local review found 13. Twelve are fixed here and one is filed:
- **A stacked scenario mixed two deductions.** A saved base carries its anchor,
  and the stacked run re-priced its inputs on the current line 12, so a baseline
  saved at $27,000 read $15,800 once line 12 moved. The anchor now records the
  deduction, and stacking refuses a base priced on another one, which is every
  file saved before AA20.
- **A capped charitable deduction over-stated every tool.** A gift over its
  share-of-AGI cap moves with AGI, and holding it makes a saving or a cost read
  high, the direction no caveat may cover. The tools now refuse wherever that
  cap binds (`schedule_a.charitable_carryforward` above zero) and AGI moves;
  pricing it needs the cash and appreciated split the return does not record.
- **Zero passed as a deduction.** The template's placeholder is 0, and a line
  12 nobody filled priced every tool on nothing. Zero now refuses unless the
  return was forced to itemize with nothing to itemize.
- **The checks compared keystone levels with line 11.** The keystone models
  half the SE tax as its only adjustment, so its AGI sits off line 11 by every
  other line-10 adjustment. Both held-deduction checks now share one shape and
  measure each priced AGI as line 11 plus the keystone's move from its own
  recompute of the return.
- **Smaller:** `/tax-ptet`'s medical note read as a statement of fact on a
  standard return, and went silent without line 11; it now uses the shared
  sentence and the keystone's AGI. A negative gross silenced a check and reads
  as unknown. The keystone validates `deduction`. `/tax-scenario`'s procedure
  names line 12. The medical-floor rule is one helper, `_medical_deductible`,
  where the federal and CA Schedule A and the check each held a copy.
- **Filed:** AA21, `/tax-project` prices every projection on the standard
  deduction; a current-year projection has no line 12 to read.

### Not done

The planning tools still hold the deduction; they do not re-price it. The SALT
phasedown and the medical floor are caveated where they can move, and a capped
charitable deduction refuses. The charitable floor from TY2026, the §70111
haircut and a switch between itemizing and the standard deduction are held
under the reports' general line that the deduction is the return's. Re-pricing
them needs Schedule A's inputs in `result.json`, which holds its outputs.

## AA21 — the projection, on the deductions the taxpayer declares

### What was wrong

`projection.project_federal_tax` subtracted the cache's standard deduction for
every filer, and `project_state_tax` the state's. `/tax-project` projects the
current year, so there is no return to read a line 12 from, and nothing asked.
For an itemizer that put projected taxable income too high by the difference:
on the test cache, $45,000 of itemized deductions against a $30,000 standard
deduction projected $12,000 of federal tax where the year comes to $9,000, and
$30,000 of California itemized deductions against its $11,000 standard
deduction projected $3,650 where it comes to $2,700. Every W-4 and estimated
payment the report recommends is priced from those figures. AA20's review found
it.

### What shipped

- **Declared, both.** `forecast_overrides.itemized_deductions` and
  `state_itemized_deductions` carry the itemized deductions the taxpayer expects
  for the year, or 0 for the standard deduction. Each projection prices from the
  larger of the declared figure and its standard deduction, and the report names
  which it used. An unanswered one refuses (the state's only where a state is
  projected), because the standard deduction in its place is the over-statement
  being fixed. The template ships both as null, not 0, so an untouched file reads
  as unanswered rather than as a declared standard deduction.
- **The skill asks.** `/tax-project`'s interview asks for both, pre-filled from
  the archived return where it itemized (federal line 12, California's Schedule
  CA total) and to be revised for what changed.

### Tests

Both projections are pinned at declared values, hand-computed: federal $12,000
on the standard deduction, $9,000 at $45,000 itemized, and the standard again at
$25,000; California $3,650 and $2,700. The refusals, the parse (absent and null
are unanswered, 0 is an answer), the report's rows, the template's nulls and the
CLI's refusal of a file that leaves the question out are pinned too. The tests
written before AA21 priced on the standard deduction, and a helper declares 0
for them rather than every one of them restating it.

### Review, round 1

A local review found 14. Twelve are fixed here, one is filed and one is left:
- **New York was blocked.** The state-deduction refusal ran before the branch
  that declines to project New York, so a New York run without the state figure
  refused the whole report, federal included, on an answer nothing reads. The
  decline now runs first, and New York is not asked for it up front.
- **An unfetched federal standard deduction read as $0.** `_num` reads None as
  0, so a half-fetched cache priced a declared-standard filer on no deduction:
  $18,000 of tax where a $30,000 standard deduction gives $12,000. Older than
  AA21, and now refused, absent or null. Round 2 found the state side refused
  only the null.
- **A separate filer's spouse can force the other deduction.** Federally, a
  spouse who itemizes takes the standard deduction away (§63(c)(6)(A));
  California makes the two match. Either way the projection's larger figure
  under-states the tax, and only the skill said so. The report and its JSON now
  carry `mfs_deduction_note` wherever the federal standard deduction was taken,
  and `state_mfs_deduction_note` wherever a state is projected.
- **Siblings.** `/tax-extend` runs the projection at the deadline and now says
  what to do when an older inputs file refuses; `/tax-retirement` works out its
  bracket from the declared deduction.
- **Smaller:** the template no longer says every state's choice is independent
  of the federal one; the pre-fill names TY2026's Schedule A changes; the
  federal table gains Schedule 1-A and QBI rows, so it shows every deduction
  between AGI and taxable income; the CLI test
  matches the federal message exactly; a stale comment and the template's key
  order are fixed.
- **Filed:** AA22, the §170(p) non-itemizer charitable deduction has no place in
  the projection.
- **Left:** a file missing both figures refuses one at a time; the skill asks
  for both together.

### Review, round 2

A second review found 15. Twelve are fixed and three are left:
- **An absent state standard deduction read as $0.** Only a status key present
  at null refused; an absent map, or a status missing after its stand-in, gave
  $5,000 of state tax where the $11,000 joint deduction gives $4,450. Now
  refused like the federal one.
- **Filled in for the taxpayer.** Step 3 pre-filled the state figure as 0 for
  every state but California, whose Schedule CA section is the only state
  itemized total an archive holds, and could overwrite step 2's answer. It now
  pre-fills California only, and only where step 2 has no answer. The refusal's
  "or 0" read as an instruction to an agent meeting it in an older file, so the
  skill now says to ask, never to write the figure.
- **`/tax-retirement` priced the state rate on the standard deduction.** Its
  projection route worked out taxable income by hand. It now reads
  `federal.taxable_income` and `state.taxable_income` from the projection.
- **Held as entered.** A declared itemized total is priced as given while its
  SALT phasedown and medical floor move with income, so a total worked out at
  another income reads the tax either way. The report and its JSON carry
  `itemized_deduction_note` wherever either side itemized, and step 3 asks
  whether this year's income moves it.
- **A state that requires the federal choice.** Where the state and federal
  projections took different deductions, the bracket-estimate note says some
  states would not allow the state's.
- **Smaller:** New York is asked where the engine refuses for it rather than
  never; the separate-filer note reads low "by the tax on the difference"; the
  table comment no longer claims it always adds up; step 5 points at the notes
  instead of restating their rule. Tests now cover every early return that
  never reads the state figure, and the shipped template run through the CLI.
- **Left:** a malformed state figure is refused even where no state is
  projected, as AGENTS.md §8 asks of parse-boundary validation; California's
  separate-filer rule is chosen by state code, as the module's other
  California-only behaviour is; and the engine and skill changes share one
  commit, which the squash-merge makes moot.

### Simplify

Four review angles over the whole diff: reuse, simplification, efficiency and
altitude. Both standard-deduction refusals now go through one `_fetched_num`.
The tests share `_state_law` and a `_report` builder, the early-return sweep
takes in New York's recapture case, and a CLI check the shipped-template test
already made is gone. `/tax-extend` points at `/tax-project` steps 2 and 5
instead of restating them, and step 2 asks for the deductions with step 3's
archive defaults in one pass rather than two. Filed: AA23, since moving AA20
to shipped dropped its record of the deeper fix, re-running
`compute_schedule_a` at each priced AGI.

### Codex review

A local Codex review found one, and it held: round 2's `/tax-retirement`
rewrite ran the projection whenever `projection_inputs.json` existed, where
the old text required its full-year forecast figures. A retirement-only file,
holding contributions and no income, projected AGI and taxable income of $0,
reporting every phase-out as clear. The precondition is back, and the skill
names that file as not qualifying.

### Not done

A separate filer whose spouse's choice forces the other deduction is told, not
priced: the projection does not ask how the spouse files.
The declared figure is the taxpayer's estimate: the projection does not
recompute Schedule A from projected AGI, and says so wherever it priced an
itemized total. AA23 would price it.

## AA22 — the projection, on the year's cash gifts to charity

### What was wrong

From TY2026 a filer who takes the standard deduction also deducts cash gifts to
charity, up to a fetched cap, below AGI on Form 1040 line 13c (§170(p)).
`/tax-compute` takes it through `compute_charitable_non_itemizer`, but the
projection had no input for it, so the gifts stayed in projected taxable
income. On the test cache a joint filer with $1,500 of gifts was projected
$12,000 of federal tax where the year comes to $11,700, and every W-4 and
estimated payment the report recommends was priced from the higher figure. The
compute step also weighs the itemized total against the standard deduction plus
this deduction, so a donor who itemizes by a little can do better on the
standard deduction; the projection compared the bare figures. AA21's review
found it.

### What shipped

- **Declared, from TY2026.** `forecast_overrides.charitable_cash_gifts` carries
  the cash the taxpayer expects to give to charity this year, or 0 for none.
  Ruled required, as AA21's deductions are: an unanswered one refuses from
  TY2026, since reading it as 0 is the over-statement being fixed. Before
  TY2026 it is never priced, though a malformed one refuses in any year, as
  every forecast override does. The template ships it as null.
- **Priced by the engine's function.** Where the standard deduction wins,
  `compute_charitable_non_itemizer` prices the gifts against the fetched cap.
  An itemized total above the standard deduction plus every gift declared
  itemizes without reading the cap. Anywhere else it is read, since short of
  the figure any cap up to the gifts could decide the choice, and an unfetched
  one refuses, naming the one block to fetch. The deduction comes off taxable
  income and the QBI cap's base, as line 13c does, and the report's table and
  JSON carry it.
- **The choice.** The itemized total is weighed against the standard deduction
  plus this one, as the compute step weighs them, so gifts can tip a filer just
  over the line onto the standard deduction. A separate filer's note says
  itemizing gives it up.
- **The skills.** `/tax-project` asks for the gifts with the deductions,
  pre-filled from the archived return where that return took the deduction.
  `/tax-setup` fetched the cap only for a standard-deduction filer with gifts,
  which setup cannot know, since the deduction itself can decide that choice;
  it now fetches the cap for any TY2026 filer with cash gifts. A cache
  fetched to the old rule still lacks it for an itemizer, and both
  `/tax-project` and `/tax-compute`, whose provisional call for the choice
  refuses there too, say to fetch that one block rather than re-run setup or
  re-ask the taxpayer.

### Tests

Hand-computed on the test cache: joint $11,700 at $1,500 of gifts against
$12,000 without, $11,600 at $5,000 capped to $2,000, and single $15,800 capped
to $1,000. The choice is pinned both ways ($31,000 itemized takes the standard
deduction at $1,500 of gifts and itemizes at $500), and so is the QBI cap's base
($4,139.27 taxable, where taking the gifts after QBI reads $3,739.27). So are
the refusal, a TY2025 run that neither refuses nor applies the figure, an
unfetched cap refused where it is read and not read where it cannot decide,
the report's row, the separate filer's sentence and the template's null.

### Simplify

Four review angles; efficiency found nothing to change. From the rest:
- **One owner for the choice.** The itemize-or-standard comparison was
  written twice, once in a shortcut that skipped the cap and once in
  `_projected_deduction`, and the caller zeroed the deduction for an itemizer
  after both. `_federal_deduction` now returns the deduction, the choice and
  line 13c, making the comparison through `_projected_deduction` alone.
- **Point at the owner.** `/tax-extend` and `/tax-retirement` listed the
  figures `/tax-project` step 2 requires, and step 2 counted them, so each new
  one meant editing three skills. They now name step 2, and the count is gone.
- **The cap-fetch rule**, above.
- **Smaller:** `_declared_deduction` is `_declared_answer`, since it reads the
  gifts too; the table-adds-up check had two copies, one missing line 13c, and
  is one; three local test wrappers are one; the separate filer's note is
  built with a plain `if`.
- **Recorded, not built:** no engine function owns the itemize-or-standard
  choice with §170(p) in it. `compute_schedule_a`'s election ignores it,
  `/tax-compute` adds it by hand and the projection in code. That belongs to
  AA23, which re-runs `compute_schedule_a` and would otherwise inherit an
  election that is wrong for a donor near the margin.
- **Left:** a legacy inputs file missing every figure refuses one at a time;
  step 2 asks for all of them together.

### Review

Three local reviews. What they found is fixed here, except as the last
three bullets say:
- **The gift default read a field nothing writes.** Step 3 pre-filled the
  gifts from `charitable_non_itemizer.raw`, which `/tax-compute` never fills,
  so a TY2027 projection from a TY2026 archive defaulted them to 0 and
  over-stated the tax. The default is now line 13c, the capped deduction,
  with a question about anything given beyond it.
- **A cache with no year dropped the gifts.** The gate read a cache that
  declared none as before TY2026: $12,000 where $1,500 of gifts gives
  $11,700. A second review found the first fix, letting the year requested
  win, priced gifts on a cache for another year, which Schedule 1-A's gate
  refused, and still dropped them where no year was requested. Both gates
  now read one helper, `_cache_year`: the cache's own year, a plain int
  matching the one requested. Where it has none, Schedule 1-A's override
  clears as before and declared gifts refuse. Only a direct call reaches
  either, since `build_report` refuses such a cache first.
- **Which gifts qualify went unsaid.** The engine's notes (cash only, no
  donor-advised fund or supporting organization, a written acknowledgment
  from $250) reached no reader, so a gift that does not qualify read the tax
  low with no banner. `charitable_gifts_note` carries it wherever the
  deduction was taken.
- **"The larger of" was no longer true federally.** Step 2, the template's
  comment on the itemized total, the `deduction` field's comment and
  `_projected_deduction`'s docstring said the larger of the two was priced,
  where gifts can now make the standard deduction win against a larger
  itemized total. The separate filer's note called such a total "the
  smaller"; it now says itemizing can deduct less.
- **A set-aside itemized total went unexplained.** Where gifts tip the
  choice, $31,000 itemized against $30,000 plus $1,500, nothing said why the
  declared total was not used. The gifts note now says so, and how little
  of them need qualify for itemizing to deduct more. The third
  review found the first wording weighed the itemized total against every
  gift where the choice weighed it against the capped deduction, and named
  only the case where none qualify: at $1,500 of gifts against $31,000,
  losing $600 of them already makes itemizing better. It now names the
  deduction and that margin.
- **An unfetched cap refused with no way through.** A cache set up before
  the cap-fetch rule changed lacks the cap for an itemizer, and one whose
  gifts exceed the itemized total's margin over the standard deduction
  refuses, since short of the figure any cap up to the gifts could decide
  the choice. The refusal said "fetch via /tax-setup", which reads as the
  whole interview, and step 2's rule against writing a 0 made re-asking the
  taxpayer the likely response. The refusal, `/tax-project` step 2 and
  `/tax-compute` now name the one block to fetch.
- **Smaller:** a cap block that is not an object is an unfetched one rather
  than a traceback; the fetcher's schema comment states the new fetch rule;
  a TY2025 `build_report` with the gifts null, and the non-joint cap's
  refusal, are tested. The second review also found two separate-filer
  comments still calling the choice "the larger", `/tax-retirement` no longer
  naming the half of SE tax taken before AGI, and a no-gifts shortcut the
  engine already made, whose test could not fail; the shortcut is gone and
  the test pins the engine. The third found the year refusal saying the
  cache declared no year where it declared another, and naming gifts the
  taxpayer declared where none were; `_validate_tax_law_year` repeating
  `_cache_year`'s rule, which it now asks; the year checked twice in
  `_federal_deduction`; and the separate filer's sentence on the gifts
  tested only on stand-ins, which a `build_report` run now covers.
- **Recorded already:** no engine function owns the itemize-or-standard
  choice with §170(p) in it; AA23's entry carries it.
- **Left:** the gifts note states §170(f)(8)'s $250 acknowledgment threshold,
  as four of the engine's notes and `answers.py` already do. Whether AGENTS.md
  §2's list of written figures should name it is the owner's call.
- **Left:** on a cache that cannot show the year being projected, a
  Schedule 1-A override clears, over-stating the tax, where the gifts
  refuse. Main cleared it the same way, only a direct `project_federal_tax`
  call reaches either, and a refusal would change a rule main's tests pin.

## AA25 — the gates, run without the functions their shell exported

### What was wrong

Bash imports every function exported into its environment, and runs the file
`BASH_ENV` names, before a script's first line, and a function runs ahead of
the PATH lookup. A function inherited from a contributor's shell could
therefore stand in for any command the three scripts that decide a verdict
run, and each then reported on work it had not done:

- **`harden-runtime-perms.sh`**, under a stub `chmod`, left `result.json`
  readable by others and exited 0 with nothing on stderr.
- **`scripts/pre-commit-pii-scan.sh`**, under a `grep` that always answered
  "no match", printed its clean banner over a staged AWS key and exited 0.
- **`skill-invariants.sh`**, under a stub `jq`, left its payload unread and
  exited 0 over a red suite with nothing on stderr.

AA4's review found the first, where a test fixture had removed these
variables from the tests rather than making the hook ignore them. Sweeping
the other scripts that decide a verdict found the other two.

The first version of the fix re-ran each script under `-p` whenever `$-`
lacked it, and its review found that short in three ways, each reproduced:

- **The permissions hook lost sight of the functions it screened for.** It
  asked `type -t` whether `echo`, or a read-only utility, was really a
  function before trusting a command as a bounded write or as a read, and
  under `-p` it can see none. With an exported `echo`, a new file in another
  year stayed at 644 after `echo x > archive/2024/out.md`, where main hardened
  it. Its test still passed, because the index pass hardened that test's
  file, which already existed.
- **`p` in `$-` is not proof of the re-run.** Bash turns `-p` on from an
  exported `SHELLOPTS` after importing the functions, and under any exported
  `SHELLOPTS` a gate that has re-run hands `privileged` on to every gate it
  starts, so a gate started by a gate would have kept them. With
  `SHELLOPTS=privileged` the scan's clean banner and the hook's 644 came back.
- **The re-run went through `exec`**, which a function of that name replaces
  outside POSIX mode.

It also left the git hook wrappers `agents/install.sh` writes running under
the functions, though each runs git itself before the scan is handed
anything: a `git` answering `branch` with another name let a commit onto
main, and one whose `rev-list` printed nothing pushed a full SSN unscanned.

A review of the second version found the child bash that answers for the
permissions hook inheriting that same `privileged`. Under an ordinary exported
`SHELLOPTS` it skipped `BASH_ENV`, so an `echo` defined there was trusted and
the other year's file stayed at 644, reproduced; the routes each tested one
setting, and none combined the two. It also found the trust tests' control
running under the runner's own functions, so an `echo` exported in a
contributor's shell failed them, reproduced.

A second local review found the replay itself unfaithful. The hook's own start
runs `BASH_ENV` before the re-run, so the child's run was the second: a file
behind an include guard it exported defined nothing, and a relative path named
nothing after the hook's `cd`. Both left the other year's file at 644 behind
an `echo` the file defined, reproduced, where main hardened it.

### What shipped

- **Five scripts re-run themselves under bash's privileged mode (`-p`)**,
  which imports no function and reads no `BASH_ENV`: the PII scan, both git
  hook wrappers, and both tool-time hooks, the skill hook second, after the
  statement that turns an inherited `xtrace` off. The re-run is recognised by
  the PID `exec` keeps, and `exec` runs with `POSIXLY_CORRECT` set, where a
  special builtin is found before any function. The scan's comment explains
  the form once, and the other four point to it.
- **The permissions hook asks whether a command's name arrived as a
  function** before trusting it as a bounded write or a read. Any `BASH_ENV`
  counts as yes, since it cannot be run again faithfully, which costs the
  read-only shortcut for whoever sets one. For an exported function a child
  bash answers: without `-p` it imports the entry as the agent's shell did.
- **One set of routes for every gate's test**, the `inherited_function`
  fixture in `tests/conftest.py`: exported, defined by a `BASH_ENV` file
  behind an exported include guard,
  exported under `SHELLOPTS=privileged`, exported beside a no-op `exec`, and
  a `BASH_ENV` file beside an ordinary exported `SHELLOPTS`. Each script's
  test runs all five.
- **An exposure only the scan finds** for the tests of the hook's trust in a
  command: a new file behind timestamps set back, with a control run on a
  twin tree that must leave it exposed. The inherited-function test and the
  PATH-shim `ls` test use it; the shim test had passed with its check
  deleted, for the same reason as the `echo` test. Their runs start from the
  runner's environment less its functions, since nothing inherited is their
  premise, and so does the one older test of the read-only shortcut that an
  inherited `pwd` or any `BASH_ENV` failed. Every other test keeps the
  contributor's environment.
- **`@needs_jq` wherever a hooks-module test needs jq.** The skill hook is a
  documented no-op without it, so on a host without jq most of its tests
  failed, and a shared fixture errored every test that used it, instead of
  skipping.
- `pii-gate.md` and `claude-automation.md` state the form, the skill hook's
  place in it, and what the re-run does not cover.

### Verified

- **Against the first version of the fix**, the new cases fail, each as
  described above: the `SHELLOPTS` and `exec` routes of the three scripts,
  the exported and `BASH_ENV` routes of the permissions hook's trust test,
  and every wrapper case. The rest pass there: the first
  version closed those routes, and where its guard let a function through,
  `type -t` could still see it.
- **One mutation at a time**, each part of the fix removed alone, turns its
  own cases red. Keying one script's re-run on `$-` reds its `SHELLOPTS`
  case, and the scan's also reds the push test's, the gate-started-by-a-gate
  case. Dropping `POSIXLY_CORRECT` reds the `exec` case. Removing a wrapper's
  lines reds its cases, asking the child with `-p` reds the exported routes
  of the trust test, and replaying `BASH_ENV` instead of counting it reds
  both of its routes, through the include guard.
- **One branch is untested:** an answer from the child that is neither a
  builtin, a file nor a function counts as a function. Only a broken bash
  produces one, and it is the safe answer.
- **With jq hidden from PATH**, the hooks module passes, skipping what needs
  jq.
- **With an inherited pass-through `chmod` and a `BASH_ENV` defining one**,
  the hooks and PII-scan modules pass with no fixture, the read-only
  shortcut's tests included. With pass-through functions exported for every
  name the shortcuts trust, or with a `BASH_ENV` that defines none, the
  permissions hook's tests pass.

### Left

- **Bash 3.2 still reads `GLOBIGNORE` and `CDPATH` in privileged mode.** That
  is the macOS `/bin/bash`. The skill hook clears `CDPATH` itself; the other
  four clear neither.
- **The functions stay in what each script's children inherit.** The skill
  hook's verdict is the suite's, and the suite starts bash; a gate it runs
  re-runs itself, but a test's own `bash -c` imports what it is handed.
- **A clone gets the wrapper change only when it re-runs**
  `bash agents/install.sh --hooks-only`.
- **Refuted under the threat model:** a caller that exports `SHELLOPTS`
  with `privileged` and sets `GATE_REEXEC_PID` to the PID the script will
  have, by `exec`ing it, passes the check. Nothing does that by accident.
- **More routing tests pass with the exposure scan off** (AA26).
- **Tests that drive a script through PATH shims** elsewhere, the PII scan's
  fake `git` and preflight's stubbed interpreter among them, still run their
  shims under whatever the contributor's shell exported. That is noise in a
  test, not a hole in a gate.

## AA4 — the permissions hook's full walk, pinned by its shape

### What was wrong

`test_the_permissions_hook_timeout_covers_a_full_archive_walk` held the
permissions hook's registered timeout above the cost of a full walk of a
reference archive. It priced the walk in CPU seconds, on the view that CPU is
the figure that survives a machine doing something else, and converted to wall
with a fixed factor. On a machine with two kinds of core that view does not
hold: CPU seconds move with the kind of core a pass lands on and the clock it
runs at. Re-measured idle, with every core busy and under file-system churn,
the correct hook's cost per entry moved by about two times, and so did the
cost of the per-file chmod regression the test exists to catch. The floor sat
in the narrow gap between the two: a correct hook read over it, failing the
test when AA4 was filed and again during AA22's merge, while the regression
under churn read barely above it. A gate that fires on correct code teaches an
agent to reason past it, and the recorded outcome is that the assertion gets
deleted.

The first repair kept the timing and made it a ratio: the hook's CPU over that
of a reference walk spawning one chmod per file, the two alternating, on the
view that both would land on the same cores at the same clock. Its review
re-ran it under partial load, a condition its own measurements had skipped,
and they did not: the minimum of each list came from different passes, the
margin between a correct hook and the regression shrank to a fraction of what
it was idle, and single paired readings of the regression fell under the
limit.

The second stopped timing. It counted the chmod processes a pass spawns, which
read the same on every run, and converted the count back to wall with a stated
budget per process. Its review showed that the conversion carried the problem
forward. The per-file regression was caught only because the hook chmods every
directory twice, so batching the traversal repair, or dropping the repeat, let
a chmod per file through at exactly the limit. The projection scaled the
fixture's directory density up to a reference archive the test described as
file-heavy, and in that shape the correct hook's floor fell to a small fraction
of the timeout, which any timeout clears. The floor also came out close to the
real walk's wall time, so it bounded nothing in either direction. And an
exported bash `chmod` function in a contributor's shell runs before the shim's
PATH lookup, which hid the per-file chmods from the count altogether.

The third pinned the walk's shape, and its review found the bound still too
loose, the check duplicating one already there, and a fixture at the wrong
depth. Letting the added files cost a tenth of a chmod each passed a hook
batching ten files a chmod. The check sat beside
`test_full_archive_pass_batches_file_chmods`, which pinned the same property
with a fixed limit of its own that a batch of ten also met. And the module
fixture that dropped inherited bash functions made the tests ignore them
rather than the hook: under an exported stub `chmod` the hook itself left
`result.json` readable and exited 0. That is AA25, fixed in the hook.

The fourth review, after AA25 merged, found the shape blind to directories.
Both archives had the same ones, so a walk repeating the traversal repair over
every directory's subtree, whose chmods grow with directories times depth,
more than doubled its count and passed; reproduced. The timeout test accepted
any positive number, so it passed a cut of any size. The xargs-derived bound
came out zero at the fixture's size on every host, and was asked of a
different command in a different environment than the hook's. And the helper
counting a full pass neither emptied `.claude/` nor its log, so a second call
on one workspace would have counted an incremental pass on top of a full one.

### What shipped

- **The shape, not a price.** `test_full_archive_pass_batches_file_chmods`
  walks three archives and counts their chmods. One with the first's
  directories and three times its files must spawn no more: both fit one
  xargs command line, so the added files join the batch. One with the first's
  files a level down, a folder each, may spend at most
  `FULL_WALK_CHMODS_PER_DIRECTORY` on each added directory, two, the traversal
  repair's and the walk's own. This replaces that test's fixed limit over one
  directory, and each call walks a fresh `.claude/` with an empty log.
- **Every file through a counted chmod.** Each archived file must appear in a
  chmod the shim logged. That catches a hook hardening files by a spelled-out
  `/bin/chmod` instead of the batch. One doing it as well as the batch still
  passes, since a PATH shim cannot count a process it is never asked to start.
- **The timeout is held where it is registered.**
  `test_the_permissions_hook_timeout_is_not_cut` replaces
  `test_the_permissions_hook_timeout_covers_a_full_archive_walk`. It holds the
  timeout at `PERMS_TIMEOUT_FLOOR` or above, a budget the test compares
  against rather than a reading, so a cut waits on AA24's measurement. A
  missing timeout, `true` and a non-finite one each fail with their own
  message.
- **Every pass with no payload gets an empty stdin.**
  `TestRuntimePermissionsHook._run` passes one, and every test in the class
  that ran the hook to completion by hand now goes through it, the three that
  wrote their own copy of the chmod-logging shim using `_chmod_logging_shim`.
  The hook reads its payload to EOF, and under `pytest -s` an inherited
  terminal never sends one.

### Verified

The correct hook passes, and so do its variants that batch the traversal
repair or drop the repeat directory chmod. A chmod per file fails, and so does
a batch of ten; a third chmod per directory fails, and so does the traversal
repair repeated over each directory's subtree. A per-file `/bin/chmod` in
place of the batch fails on the logged-file check; one alongside the batch
passes, as stated above. A hook that does nothing fails on the exposure check.
A registered timeout under the floor, missing, `true`, 0, -1 or infinite
fails. Without the reset, a second full-pass count on one workspace comes out
different from the first. With the empty stdin removed, `pytest -s` under an
open stdin hangs. The counts are in the pull request's description, as ruling
Q13 places a changed threshold's reading.

### Left

- **Nothing prices the walk against its timeout (AA24).** The walk's two
  chmods per directory, its per-entry shell loop, its traversals and its index
  refresh are unmeasured against the registered timeout, and in a file-heavy
  archive the loop is nearly all of the cost.
- **A fixed batch at least the size of the dense archive passes.** It is no
  finer than xargs's own at that size.
- **A single recursive chmod would fail the logged-file check.** It names no
  file, and a hook that moved to one would need that check rewritten.
- **Linux has not run it.** The file check assumes GNU's xargs puts the dense
  archive's paths in one command, as BSD's does here, and the hook has no
  platform branch. AA2's run on 2026-10-01 is its first reading.

## AA5 — the runtime lock, held to a universal resolution

### What was wrong

`test_every_lock_was_resolved_universally` covered `requirements-dev.txt` and
`requirements-test.txt` and left out `requirements.txt`, the runtime lock and
the only one that reaches a taxpayer's documents. The lock was universal, so
nothing was wrong yet; nothing would have noticed a regeneration without the
flag.

### What shipped

- The runtime lock joins the parametrization, so all three locks are held to
  `--universal` in their regenerate comment.

### Verified

With `--universal` removed from `requirements.txt`'s header, the new case
fails and the other two pass.

## AA8 — the suite's count, out of the hook

### What was wrong

`test_the_measured_costs_counts_match_live_collection` held MEASURED COSTS'
scale row, the `pytest tests/` reconciliation sentence and the suite's skip
count to a live collection of the whole suite. So every change that added a
test anywhere had to hand-edit `.claude/hooks/skill-invariants.sh`, and that
edit selects the hooks module, the dominant row of a cold pass. Since ruling
Q13 the row's only job is its ratio to a cold pass, which needs no count.

### What shipped

- **The scale row states no count.** Its middle column reads `every module`,
  and the test requires it to stay uncounted, so a count cannot creep back in
  unread.
- **The suite-wide reconciliation is gone**: the `pytest tests/` sentence, the
  suite skip count and `_SUITE_PAIR`, which the timing test also trusted as a
  source of numerals.
- **The collection covers only the seven modules the hook selects**, named by
  the decomposition table, instead of the suite. A renamed or deleted module
  fails the collection and says so.
- The comments in the hook and the test, and `test-invariants.md`, now say the
  check goes red when a test is added to one of the seven, not anywhere. The
  backlog's Status sentence no longer says the block states the suite's size.

### Verified

The counts test fails with a count put back in the scale row, fails with a
test added to `tests/test_claude_agents.py`, and passes with a test added in a
new module the hook does not select.

## AA17 — a move abroad, stopped at the gate

### What was wrong

The interview asked only whether the taxpayer moved states, and
`part_year_residency` blocked on that answer. A move to or from another country
is not a move between states, so a Californian who emigrated in June answered
"no" truthfully, passed the gate and got a full-year resident return taxing the
months after they left: an over-statement, the direction AGENTS.md §4 makes a
hard error. `lives_abroad` did not cover it; it drives only the MANUAL `feie`
note, and describes a whole year abroad rather than a move.

### What shipped

- **A rule of its own, `move_abroad`,** keyed on
  `flags.moved_abroad_this_year`, UNSUPPORTED: part-year allocation is not
  modeled whichever side of the border the other home is on, and a non-citizen
  can also have a dual-status federal year, which is not modeled either.
- **A separate question, not a wider one.** The states question kept its flag;
  the interview now asks the abroad question next to it in Group 2.
- **The flag is required.** Every profile written before this answered only the
  states question, and "no" to that is not "no" to this, so an unanswered flag
  blocks as `unanswered_gate_questions` rather than reading as False.
- **WHO-IT-FITS sizes the block.** Its row comes from the migration files'
  foreign rows the page already measured (0.13% to 0.32% of households), and
  the "not blocks" bullet that tracked this item is gone. Seattle's
  all-returns range moves from 17–18% to 18%; no other cell changes.

### Verified

The flag blocks through the parametrized rule test. A profile holding every
gate answer but this one blocks with `unanswered_gate_questions` naming it;
with the flag taken out of the required list that test fails, and with the rule
deleted two tests fail (the rule test and the WHO-IT-FITS roster).

## AA15 — line 58, refusing a City or Yonkers tax nobody computed

### What was wrong

`compute_city_and_yonkers_total` took the City resident tax (line 47a) and both
Yonkers taxes (lines 55 and 56) as arguments defaulting to 0.0. A caller that
skipped the function computing one got a line 58 without it and nothing to say
so, and a line nobody computed read the same as a computed zero: an
under-statement with no banner. The same default hid line 47a until AA13 and
lines 55-56 until AA12, and reviews found both, not a gate.

### What shipped

- **The three household flags are required** keyword arguments —
  `nyc_resident`, `yonkers_resident`, `yonkers_nonresident_earnings` — held to
  bool, and the three taxes default to None.
- **A flagged tax that was not passed is refused**, naming the function that
  computes its line. A computed zero passed explicitly is taken.
- **A tax passed for an unflagged household is refused too**: either the flag
  is wrong, and the gate reads it, or the amount is someone else's. An
  unflagged tax left out is the zero the flag asserts.
- `/tax-compute`'s New York reference says to pass the flags from the profile,
  and never to drop an argument to get past the refusal.

Of the backlog's two remedies this is the first. The second, shipping the lines
`null` with a check that refuses a null line for a flagged household, would
also catch an agent that never calls the total or types line 58 by hand, which
this does not. Its natural home is the tie-out gate, `agents/lib/tie_out.py`,
which already reads `new_york_it201` and already checks a profile flag against
result lines (`may_qualify_for_eic` against the EIC lines).

### Verified

With the flagged-but-missing refusal switched off, three cases fail (one per
line); with the unflagged-amount refusal switched off, three others fail. The
engine self-test, mypy and ruff pass.

## AA14 — the State table's one departure from its schedule

### What was wrong

Every published row of the 2025 State tax table was compared with
`compute_ny_tax`. The single and head-of-household columns agreed on every row,
and the married-joint column was a dollar LOW on 22 rows, every one in the
$23,600-27,900 band: the $23,600-23,650 row publishes 978 and the engine gave
977. The engine builds each row as the rate schedule at the row's midpoint,
with the PUBLISHED base, and that band's published base is 976.

### What it turned out to be

The table is built on 976.25 in that band, the bands beneath it summed and not
rounded. That base reproduces all 22 rows, and 976, 976.5 and 977 do not. It is
not a rule New York applies everywhere. Summing every band breaks 510 single
rows (13,900 band, 599.5 against a printed 600) and 17 head-of-household rows
(17,650 band, 730.25 against 730), and both of those columns follow the printed
base on every row. The statute, Tax Law §601(a), also says $976. The 2024 table
has the same 22 rows on the same schedule, so it is how the table is built, not
a one-year slip. The City table agrees with its printed bases on every row.

The form tells a taxpayer in the table region to use the table, so the table's
figure is the one that is filed.

### What shipped

- **`tax_table.table_bases`, fetched.** It is a list of `{schedule, min}`
  naming each band where the table is not built on the printed base. It names
  the band and never the figure: `_ny_table_bases` sums the bands beneath and
  uses that, unrounded, for the table only. A transcribed figure could not be
  checked, because it differs from the printed one by cents, and a typed
  977.2 moved a row up a dollar with nothing refusing. Every entry is checked
  on every return, whichever column it is for: an unknown schedule letter, a
  floor that meets no band, a band named twice or an entry carrying any other
  key refuses. The schedule, and the worksheets above the table, keep the
  printed base.
- **A key that was never fetched gets a note, not a refusal.** It moves a line
  by at most the table's rounding dollar, inside the acceptance bar, and
  refusing would stop every cache written before the key existed. The NY
  compute reference now carries `compute_ny_tax`'s and
  `compute_nyc_resident_tax`'s `notes` into the result, which nothing did
  before, so the note reaches the user.
- **The cache can hold it.** `set` validates `table_bases` as its own table,
  since a None seed read as a number and refused every list. The freshness
  walker accepts `[]`, the result for a table with no departure, which it
  otherwise flags as an empty list forever; None still flags.
- **`/tax-setup` says how to find a departure**, row by row: a cents-sized
  base difference tips only some rows, so a band's first row can agree while
  later ones differ. It names no year's entries.
- **The test fixture gained schedule b**, so the head-of-household column is
  now tested.

### Verified

The 22 joint rows are pinned against the published figures. So are the rows on
either side of every fractional base in the table region, in all three columns.
With the fixture's entry removed, 23 tests fail. With the engine ignoring the
entry, 24 fail. `set` writes a one-entry list and `[]`, and the freshness check
reads both as fetched.


## AA27 — urllib3 2.7.0 failed the Dependency audit

The first CI run after the minutes returned, on #189, failed pip-audit on three
advisories against urllib3 2.7.0 (CVE-2026-97687, -97688, -97689), fixed in
2.8.0. urllib3 comes into `requirements-dev.txt` alone, through `requests`, so
`main` was red on the same job and no branch could go green. The dev lock was
regenerated with `--upgrade-package urllib3`: the diff is that one pin and its
two hashes. pip-audit then reports no known vulnerabilities in any of the three
locks. It rides #189 rather than a pull request of its own so that the AA14
change can merge on a green run.

## AA28 — New York's brackets, read as one list

### What was wrong

New York's brackets are keyed by §601 rate schedule, `{"a": [...], "b": [...],
"c": [...]}`, the City's too, and `_TABLE_VALIDATORS` matched `state.brackets`
as the flat list every other state has. Three things followed:

- **The freshness audit flagged every fetched New York cache.**
  `validate_cache` runs the write path's rules over the whole state block, so a
  correct cache reported `state.brackets: brackets must be a list` and read
  INCOMPLETE. `/tax-compute` answers INCOMPLETE by re-fetching the named
  fields with `set`.
- **`set` refused that re-fetch,** for `state.brackets` and for the whole
  `state` block alike, so the remedy looped. AA14's tests had worked around it
  by writing the New York block straight into the file.
- **One schedule at a time went in unchecked.** `state.brackets.a` matched no
  pattern and got only "expected a list", so a gap between bands, a missing
  published base or a transposed one entered the cache as fetched, and the
  return refused it later.

### What shipped

- **The engine's schedule rules are one function,** `_ny_check_schedule`,
  taken out of `_ny_schedule_tax`: `rate`, `min` and the published `base` on
  every band, bands that meet, each base within New York's dollar of rounding
  of the bands beneath it, and only the last band open. Two things changed on
  the way. The engine now checks the WHOLE schedule before reading a band,
  where it used to stop at the band the income landed in, so a bad band
  anywhere refuses every return on that schedule. And it now checks values,
  not only presence: a NaN base passed the dollar comparison, every comparison
  with NaN being False, and a closed top band is refused outright rather than
  when an income falls off it. Both are refusals, the safe direction.
- **The fetcher checks each schedule,** State and City, with the generic
  bracket rules and then that function, as AA14 shares
  `_ny_table_bases_entries`. A table validator applies only where the seed is
  a list, so any block keyed by schedule or status recurses rather than being
  read as one table.
- **The audit reads a schedule as stored.** `set` still normalises an
  inclusive "over X" floor before writing it, but the audit had run the same
  normalisation on its own copy, so a hand-written floor a dollar high read
  FRESH while the engine refused it as a gap.
- **The recapture tiers, the same shape one block over, get the same
  treatment:** `_ny_check_recapture_tiers`, shared by the engine and the
  fetcher. A tier with no base was a `KeyError` in `compute_ny_tax`, and a
  closed top tier left every return above it in no tier.
- The generic refusal message printed `{{rate, min, max}}` with doubled braces;
  it prints single ones.

### Verified

A cache holding the published TY2025 schedules and tiers audits clean, and
`set` writes the whole New York state block, `state.brackets`, one schedule
and one schedule's tiers. A gap, a missing base, a base $50 off, a NaN, bool,
negative or string base and a closed top are refused for a State and a City
schedule; a string base is an audit finding, not a crash. Mutation checks:
with the engine's value checks off, 13 tests fail; with the closed-top rule
off, 1; with the engine skipping the tier check, 1; with the audit normalising
its copy, 1; with the recursion guard removed, 4; with the tiers pattern off,
5.

## AA29 — a line 58 that read an uncomputed line as $0

### What was wrong

AA15 made `compute_city_and_yonkers_total` refuse a flagged line 47a, 55 or 56
it was not passed, but that guarded only a line 58 computed through it. The
template shipped all three lines as 0, so a line nobody computed was
indistinguishable from a computed zero, and a line 58 an agent assembled without
the function, or typed into `result.json`, under-stated what the return owes
with every gate green.

### What shipped

- **The three lines ship `null`, not 0,** like line 59: a line nobody computed
  stays visibly uncomputed. An unflagged household leaves its line null, or
  takes the 0 the total returns for it.
- **The tie-out gate checks them, and line 58.** Reading the household flags
  from the profile, `tie_out` fails (exit 2) when a flagged line is null,
  absent, not a finite non-negative number, or its block was pruned; when a
  household flag is present but not a boolean; and when line 58 is below line
  54 plus lines 54e, 55 and 56, the terms no City credit reaches. A computed
  $0 passes, and so does a line 58 above the floor, the loud direction. It is
  the one place every route to line 58 meets, a total typed by hand included.
- **One table.** `NY_HOUSEHOLD_LINES` in `ny.py` pairs each flag with its
  argument, block, field and line; `compute_city_and_yonkers_total` and the
  gate both read it, and the tests parametrize over it.
- **The remedy is the real one.** A household the engine refuses (a part-year
  City or Yonkers resident, a joint return with one resident spouse, Form
  Y-203 on a joint return) still owes the tax, so the gate still fails it, and
  says to prepare the tax by hand and never enter 0 rather than pointing at a
  function that will refuse again. `/tax-compute` Step 12.7, the New York
  reference and `/tax-verify`'s line map say so.

Changing the template moves its digest, which the validation receipt binds,
so every existing receipt goes stale and the gates re-run once.

### Not done — AA30

A 0 written after the template still passes as computed: the gate checks a
line's value, not where it came from. And the refused households are caught
here, at Step 12.7, rather than at the capability gate before any work is done.

### Verified

Tests cover each line (from the shared table) with null, absent, NaN, a bool, a
negative and a string; a pruned block; a non-boolean flag; line 58 at 0, short
by a term, null and a string; the MCTMT counted from its own block; a line 58
above the floor; an unflagged null; a non-New-York return and a missing
profile; the template's nulls; and the CLI reading the flags from the profile,
in both output modes.
Mutation checks: the findings left out of the verdict (28 tests fail), the
CLI not passing the flags (1), a non-boolean flag skipped (3), no line-58
floor (5), the floor ignoring the `mctmt` block (1), a negative accepted (3),
the template back to 0 (1), and a mislabelled row in the shared table (1).

## AA18 — a territory resident passed the capability gate

### What was wrong

Since AA16 the compute step adds a §931 or §933 exclusion back into modified
AGI, which a bona fide resident of Puerto Rico or American Samoa can have
without `lives_abroad`. Nothing collected one: the interview did not ask, the
profile had no field, and the gate accepted a PR, VI, GU, AS or MP address
with no rule. Modified AGI went in equal to AGI, so the SALT cap's phasedown
and Schedule 1-A's phase-outs read the smaller figure — an over-stated
deduction, the direction AGENTS.md §4 makes a hard error — and the excluded
income had no path into or out of the return at all.

### What shipped

- **`flags.bona_fide_territory_resident`**, in the profile template and asked
  in `/tax-setup` Group 2, and a required gate question: unanswered blocks as
  `unanswered_gate_questions`, never read as a no.
- **`territory_resident`**, an UNSUPPORTED rule naming each territory's form
  (§933, Form 4563 for §931, Form 8689 for §932, the territory's own return
  under §935). It blocks on the flag, and on a territory ADDRESS or
  `residency.domicile_state` whatever the flag says — the address is where the
  mail goes, the domicile is the residence. `_TERRITORIES` is the one copy of
  the five codes; `_USPS_STATES` is built from it. Like `part_year_residency`
  it can be acknowledged, as AGENTS.md §4 provides for every unsupported rule;
  the rule note and the provisional-income guidance say the excluded income
  is then added back by hand.
- **A territory gets its own state note**, not "bracket-based ESTIMATE": a
  territory is not a state, and the limitations of an acknowledged return
  said both.
- `docs/WHO-IT-FITS.md` names it among the blocks no public table measures,
  and the residency guardrails place it beside the other residency rules.

### Verified

Tests: the flag blocks; each of the five territory addresses blocks with the
flag false, and so does a territory domicile behind a stateside address; no
territory gets a bracket-estimate note; the set is exactly the five; a state
address with the flag false does not block; an unanswered flag blocks as an
unanswered gate question; the block can be acknowledged. Mutation checks: no
address derivation (5 tests fail), the flag not required (1), the rule
demoted to MANUAL (7), a territory dropped from the set (1), the interview no
longer asking (2), the template losing the flag (1), the domicile not read
(1), and the territory note removed (5).

### Not done — AA31

The template ships every required gate flag `false`, so a profile built from
it carries an answered "no" to a question nobody asked; `unanswered_gate_
questions` fires only on a key that is absent or null. This item adds one more
such flag; the fix belongs to all of them.

## AA31 — the template pre-answered the gate's required questions

### What was wrong

`taxpayer_profile_template.json` shipped every `_REQUIRED_GATE_FLAGS` entry as
`false`, and `/tax-setup` builds the profile from it. A question the interview
skipped — a headless run whose `answers.md` lacked it — therefore reached the
gate as an answered "no", and `unanswered_gate_questions` fires only on an
absent or null key. "Absence is not a no" held for a legacy profile and not
for a new one. Found by AA18's bias review.

### What shipped

- **The required gate flags ship `null`**, as `nyc_resident_spouses` already
  did: a profile straight from the template blocks, naming every one, until
  each is asked.
- **The eval fixture answers them**, from `_REQUIRED_GATE_FLAGS`, instead of
  inheriting the template's answers.
- **The interview answers a question its condition skipped.** Five are asked
  only when something holds — the City and both Yonkers questions when New
  York appears, the ISO question when ISOs were exercised, the Form 8615 one
  when there are dependents — and the template's `false` used to answer them
  otherwise. The interview now says to set each `false` there, the tips
  question is asked in every year, and `/tax-setup` says every required flag
  must end `true` or `false`. Found by this item's own review: without it, a
  correctly run interview blocked every filer the conditions skipped.
- The gate's blocker reads "Screening questions unanswered", not "Profile
  predates the capability gate": a new profile reaches it too.

### Verified

A test reads the template: every required flag ships null, and a profile
built from it blocks; and each conditionally asked flag is set `false` in the
interview where its condition skips it. Mutation checks: one flag back to
`false` (1 test fails), the fixture no longer answering (2), the ISO
question's `false` removed (1).

## AA30 — the refused City and Yonkers households, blocked at the gate

### What was wrong

`state/ny.py` refuses a part-year New York City or Yonkers resident, a joint
return with one (or no) whole-year resident spouse, and Form Y-203 on a joint
return or beside a Yonkers residence. Those refusals surfaced at /tax-compute
Step 10, and since AA29 the tie-out fails the return again at Step 12.7 —
after the work, although every answer that decides it is an interview
question asked before any.

### What shipped

- **`ny_household_refusals`** in `ny.py`, beside `NY_HOUSEHOLD_LINES`: the
  refused households, from answered flags. A test runs each case through the
  engine's own guards, so the gate and the engine cannot drift apart.
- **`ny_city_or_yonkers_household_unmodeled`**, raised at Step 0.7 on a New
  York return only — elsewhere the engine never runs, and a Connecticut
  commuter's Yonkers earnings are `nonresident_source_income`'s. It names the
  form (IT-360.1, IT-201 lines 50, 51 and 57, Form Y-203).
- **It can be acknowledged**, as every unsupported situation can (AGENTS.md
  §4): the City or Yonkers tax is then prepared by hand and recorded in
  `answers.md`. The note warns against the only other way past it, a false
  residency answer — a whole year or a second resident spouse OVER-states the
  tax, and dropping the flag drops it.
- **The tie-out exempts an acknowledged refused household** from the engine's
  line 47a, 55 or 56, since the hand figure belongs on line 50, 51 or 57; only
  those households, and only with the acknowledgement.
- An unanswered count or full-year answer is left to the engine, which
  refuses it by name.

The skill hook's test budget is nearly spent: the gate's cases are loops, not
parametrized tests, and the engine-agreement test lives in a module the hook
does not run. AA9 and AA24 are where that budget is measured.

### Not done — AA32

The tie-out still checks a household line's value, not its provenance (AA30's
other half), so a 0 written after the template passes as computed.

### Verified

Seven refused households block on a New York return with the form named, and
none blocks on a Connecticut one; seven not refused (whole-year, both spouses,
a single Y-203, unanswered counts) pass; the block acknowledges; the engine
refuses each case the predicate names and accepts each it passes; the tie-out
exempts an acknowledged household and still holds an unacknowledged one.
Mutation checks: the block not scoped to New York (3 tests fail), not
acknowledgeable (3), the tie-out exemption off (2), the exemption without the
acknowledgement (3), the predicate refusing two resident spouses (4); and from
the first round, the block never raised, the Y-203 condition dropped and the
part-year case unchecked (1 each).

## AA9 — the skill hook times itself against its own timeout

### What was wrong

An overrun of the registered timeout CANCELS the hook, and a cancelled check
reads to the agent exactly like a pass. The headroom test guards the half of
that the test count drives; a per-test cost that climbs while the count stands
still is the one blind spot MEASURED COSTS names, and ruling Q13 leaves it to a
cold pass re-timed only when a threshold moves. Raised by AA3's review rounds
(#172).

### What shipped

- **The pass times itself** with bash's `SECONDS` around the pytest run, and
  when it used three quarters or more of the timeout `settings.json` registers
  for this hook, it says so: on a green pass, exit 1 — surfaced, not blocking,
  the convention `harden-runtime-perms.sh` already uses — after the stamp has
  advanced; on a red one, a line beside the failure.
- **The timeout is read, not restated**: `jq` reads it from `settings.json`,
  so the check is a comparison against a threshold, inside Q13; a timeout it
  cannot read is no warning, the state every pass was in before.
- One test drives a shim that sleeps against a one-second registered timeout,
  with an ample one and with none, in one test because the hook's own test
  budget is nearly spent. `docs/contributing/claude-automation.md` says what
  the warning means.

AA24 asks the same of `harden-runtime-perms.sh` and stays open.

### Verified

The test covers a slow green pass (exit 1, the stamp advanced), a slow red one
(exit 2 with the line beside the failure), an ample timeout and an unreadable
one; only the slow cases sleep. The exit-code contract in the hook's header and
in `claude-automation.md` names exit 1's second meaning, told apart from the
signal's by its stderr. `jq` matches the registration with `contains`, not a
regex that needs Oniguruma, and takes the tightest numeric timeout.

Mutation checks, each turning the test red: the threshold never met, the green
warning exiting 0, a warning at any speed, the red path's line dropped, and
the warning sent before the stamp advanced.
