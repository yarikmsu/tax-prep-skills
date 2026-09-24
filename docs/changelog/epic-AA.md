# Epic AA — the 2026-09-20 product-owner review

Backlog: `docs/roadmap/BACKLOG.md`. Epic AA began as the seven items a
product-owner review raised on 2026-09-20, the day the roadmap was collapsed to
one file. They are not a feature theme: AA2 and AA3 are process, AA4 and AA5 are
test hygiene, AA6 and AA7 are copy a first public user would hit, and AA1 is the
one tax finding. AA8 to AA10 were filed later from AA3's review rounds, and AA11
to AA13 from AA7's scoping and its two reviews.

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
