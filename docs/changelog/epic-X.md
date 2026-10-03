# Epic X — Tax correctness, known and unfixed

Backlog: `docs/roadmap/BACKLOG.md`. Epic X was adopted 2026-09-05 as part of
the staff agent-developer review. X1-X3
are the confirmed directional defects the 08-12 and 09-05 reviews carried; they run
ahead of the orchestrator because they are small and touch CA filers.

## X2 — Fail closed on missing capital-gains breakpoints (P1) — PR #86

**Defect (E5):** `compute_scenario` fell to `compute_bracket_tax` when either
`zero_pct_limit` / `fifteen_pct_limit` was `None`, and `projection.py`'s own
worksheet branch (which never calls `compute_scenario`) had the same fall-through.
Qualified dividends and net LTCG were priced at ordinary rates — over-stating the
tax the user acts on, silently. Only `loss_harvest.py` guarded; `scenario.py`,
`roth_ladder.py`, `ptet.py` and `projection.py` did not.

**Shipped:**
- `tax_computations.qdcg_worksheet_applies(preferential_income, zero, fifteen)`:
  the one guard. False with nothing preferential (wages-only and net-loss cases stay
  usable), True with both breakpoints, ValueError naming `/tax-setup` otherwise.
  Used by `compute_scenario` and `project_federal_tax` (translated to
  `ProjectionError` there).
- `projection.capital_gains_limits(tax_law, filing_status)`: the one READ site,
  replacing the four `.get` chains; a source sweep pins that nothing else in
  `agents/lib` reads the key. X1's status-specific keys land here.
- `ptet._main` lists `ValueError` so the refusal is an `Error:` line, not a
  traceback; scenario, roth_ladder, loss_harvest and projection already did.
- Six skill prerequisites (scenario, roth-convert, project, ptet, withholding,
  harvest) state the refusal; doc-sync pinned.

**Review round (bias-direction reviewer, before merge):** the guard asked only
`is None`, and `load_tax_law` is a bare `json.loads`, so a `NaN` breakpoint
passed it and the worksheet's `min(qdcg, nan - ordinary)` kept `qdcg` — the
whole preferential stack at 0%, silently (under-statement); a zero or negative
breakpoint pushed the stack out of the 0% band (over-statement). The accessor
now refuses any non-`None` breakpoint that is not a positive finite number, and
a 15% breakpoint below the 0% one, naming the key path and `/tax-setup`, so every
keystone inherits the check. And `qualified_dividends` went through `_num`: a
sign slip of -6000 beside a $5,000 LTCG summed to a negative preferential
income the guard read as "nothing to price", and the gain was taxed at ordinary
rates with no refusal — the fall-through by the input door. Both dividend
fields are non-negative at the parse boundary and box 1b may not exceed box 1a.

**Cleanup pass (same round):** the guard was defined a second time in
`compute_form_6251`'s Part III coordination (the same fall-through by the AMT door);
it now calls `qdcg_worksheet_applies`, and so does `loss_harvest`'s entry-point
guard, which keeps its harvest-specific message but no longer re-derives what
counts as preferential income. The accessor's leaf validation is the
`_indexed_limit_bad` predicate the Schedule 1-A check already used, plus the
`> 0` and ordering rules that are the read site's own; a scalar stored where the
per-status map belongs is refused instead of raising `AttributeError`. The
one-read-site sweep matches both keys. The five skills carry one prerequisite
bullet for the cache, not two adjacent ones.

**Kept:** `loss_harvest`'s own entry-point guard, whose message names the exact
keys; it is the keystone's guard wrapped in a harvest-specific message now.

## X3 — Capital-loss carryforward by character; finite values at rollover's boundary (P1) — PR #88

**Defect (E6 and the combined carryforward):** `prior_year.capital_loss_carryforward`
was one combined figure with no Schedule D line to land on (line 6 is short-term,
line 14 long-term), and `rollover._read_json` was a bare `json.loads`, so `NaN` and
`±Infinity` passed every transform's range check and came out as `nan`/`inf`.

**Shipped:**
- Profile template: `capital_loss_carryforward_short_term` / `_long_term`; the legacy
  combined field stays, documented. Result template: Schedule D
  `line_6_prior_year_carryover` / `line_14_prior_year_carryover`.
- `rollover._capital_loss_carryforward` returns the split from the worksheet block,
  or a combined lower bound (MFS-aware cap) flagged `contradiction` (explicit zero
  block beside a loss beyond the cap) or `legacy` (no block). `_capital_loss_changes`
  writes both split fields and retires a non-zero legacy value, or offers the lower
  bound as an `ask` on the legacy field — never a guessed split.
- `_read_json` refuses non-finite floats everywhere in a document; `apply_changes`
  reads through it; `_amount` makes every transform boolean-proof.
- tax-compute consumes the split on lines 6/14 and STOPs on a combined-only figure;
  tax-setup Group 9 asks for the split; tax-rollover names the `ask`.

**Review round (bias reviewer 5, cleanup 12; before merge):**
- A gain year, or a loss inside the cap, emitted NOTHING, so a stale split the
  interview had already put in the profile survived the roll and landed on
  Schedule D. The archive saying "nothing carries" is an answer: zeros are
  returned and a non-zero target is offered an `ask` to retire it; only an
  archive with no Schedule D figure at all is silent.
- A joint-year carryforward rolled unprompted onto a separate-year return. Reg.
  §1.1212-1(c) allocates it between the spouses by each one's own net loss, and
  a decedent's dies with them (Rev. Rul. 74-175): an MFJ → anything transition
  turns every split write into an `ask` naming both.
- The combined-bound branch left a non-zero split in place, which disarmed
  /tax-compute's STOP (armed only while both split fields are zero); it asks to
  zero the split beside the bound it offers.
- A one-sided or sign-slipped worksheet block was trusted whole. The
  contradiction test is now a TOTAL: a block whose total is below abs(line 16) −
  cap — impossible for a correct worksheet — is offered for resolution.
- `_capital_loss_cap` silently took the $3,000 branch for any status it could
  not read; it takes the engine's constants, reads every spelling, and for an
  unreadable one keeps the $3,000 cap — the SMALLER bound, because the bound is a
  value the `ask` lands — and says so (the code review caught the first cut
  assuming the MFS cap, which over-states the bound for every non-MFS filer).
- The block was bounded below but not above: a block above abs(line 16), or any
  non-zero block beside a gain, is the pre-deduction loss written where the
  carryover belongs and is a contradiction too. A loss inside the cap with no
  block is a zero bound offered loud, not proof that nothing carries. The
  joint-to-separate guard fails closed on a null current status. The legacy
  figure is retired as an `ask` in a gain year. `_load_current_state` sits
  inside the CLI's error boundary. Schedule D lines 6/14 take the carryover as
  a NEGATIVE number, which the compute reference now says.
- Cleanup: the non-finite walk is `secure_fs.first_non_finite`, shared with the
  tax-law cache reader; `yoy_diff` reads amounts through `_amount` (its own
  reader took `true` as 1); the split targets are pinned against FIELD_MAP.

**Review round (Codex, local, against the branch below):** two findings, both
fixed, and both the same mistake in different clothes — reading an ABSENCE as
an answer.

- **An unfilled worksheet block read as a computed zero.** `_amount` reads a
  missing figure as 0, which is the right default for something being summed
  and the wrong answer to "did anybody write this down?", so
  `loss_carryforward_to_next_year: {}` beside a loss inside the cap came back
  as "the return establishes that nothing carries" — and the changes then
  retired a live carryforward out of the target profile. Both sides must be
  written down for the block to be an answer; a half-filled one stays the
  contradiction the docstring already called it.
- **A bound of zero could land.** `apply_changes` writes an `ask`'s
  prior_value once its category is approved, so the zero offered for a loss
  inside the cap wrote "no carryforward remains" into the profile on the
  strength of an archive whose own reason says one may remain, and the split
  asks beside it zeroed a figure the filer may have keyed from the paper
  worksheet — all three fields zero, and /tax-compute's STOP never armed. A
  zero bound now carries the profile's own value, so approving it changes
  nothing, and it proposes no split write at all: only a bound that EXISTS can
  be run past.

## X1 — Status-specific capital-gains breakpoints (P1) — PR #89

**Defect:** the schema keyed the §1(h) 0%/15% breakpoints `single`/`mfj` only and
every reader collapsed HoH and MFS onto `single`. Head of household has its own
breakpoints (higher than single) and MFS is half of joint (its 15% breakpoint is far
below single), so the collapse under-stated one filer's 0% band and over-stated the
other's 15% band.

**Shipped:**
- Seed: `zero_pct_limit` / `fifteen_pct_limit` keyed `single`, `mfj`, `mfs`, `hoh`.
- `projection.capital_gains_limits` reads `_full_status_key` (QSS → mfj). A legacy
  cache reads `None` for HoH/MFS and X2's guard refuses — never the single figure.
- `set` orders the four (HoH between single and joint, MFS never above single,
  fifteen above zero per status); the fetch reference populates all four;
  AGENTS.md §8's gotcha corrected; the five fixture caches gained the keys.
- Both acceptance replays re-run (the seed change moved Replay B's fixture digest)
  and filed under `## Results — 2026-09-06`; CLAUDE.md's claim moved with them.
- **Found by that replay, unplanted:** `create_empty_tax_law` seeded the Form 8938
  US-resident MFJ thresholds with the foreign-resident single pair ($200K/$300K
  instead of $100K/$150K), and `check_form_8938_required`'s fallback, the FBAR skill
  text and the test fixture all carried the same doubled pair — a domestic couple
  with $120K abroad was told no Form 8938 was due. Corrected in all four places;
  the auditor was dispatched a second time against the corrected seed and that run
  is the one recorded.

**Deferred, deliberately:** the auditor definition still says the map is keyed
single/mfj (its item 7); editing it needs the fresh-session replay — Y2, with the
`_provenance` check from V4.

**Review round (bias reviewer 3, cleanup 10; before merge):**
- A cache created before this branch still carries the doubled Form 8938
  `mfj_us` pair, the engine prefers the cache, and nothing pinned it. The
  seed-complete blocks (`se_tax`, `niit`, `additional_medicare_tax`,
  `foreign_tax_credit_simplified`, `form_8938_thresholds`) are bill-fixed, so
  `validate_cache` reads a leaf that differs from the seed as INCOMPLETE, naming
  the older seed as the cause. The table itself lives once, in
  `tax_computations.FORM_8938_THRESHOLDS`, and the seed and the engine's
  fallback both read it.
- The four-status orderings were non-strict, so a copied `single` column — the
  usual slip, since many secondary tables print only single/joint/separate —
  passed every pair. §1(h)'s own relations are checked: head of household is
  STRICTLY above single for both breakpoints, the separate 15% breakpoint
  strictly below single's, and the separate 0% breakpoint EQUAL to single's.
- The chain joint ≥ head of household ≥ single ≥ separate is derived from the
  schema for every per-status map (`_sibling_pairs` walks `_STATUS_CHAIN`), so
  the ordering table shrank to what the chain cannot see; `niit.threshold` and
  the like are covered for the first time.
- `project_state_tax` read the state standard deduction through
  `_binary_status_key`, collapsing head of household onto single: California
  gives HoH the joint amount, so the deduction was halved and the state
  estimate over-stated. It reads the full status (separate → single, surviving
  spouse → joint as stand-ins) and refuses an unfetched key.
- Cleanup: `_tax_law_2026()` in the projection tests is current-shaped with an
  explicit `_legacy()`; the 8938 test table is read from the seed; the
  acceptance-record control asserts its precondition and postcondition; the
  `_binary_status_key` docstring no longer names the capital-gains limits.
- Code review of the round's own fixes: the full-status state deduction gave
  head of household a ZERO deduction where a template carries no `hoh` row (the
  generic states); it takes single as the stand-in. A two-key breakpoint cache
  reads INCOMPLETE from `validate_cache` — the walker cannot see an absent key,
  so FRESH said proceed while the accessor refused. tax-compute's worksheet
  steps name the filer's status key and STOP on a missing breakpoint. The FBAR
  skill's Form 8938 figures are derived from the one table by test. The rebase's
  four duplicated invariant bullets are removed. The auditor definition's
  keying note stays deferred to the Y2 fresh-session replay.

## Q8 + Q10 — the degrade policy, and §164(b)(7)'s actual schedule

Two owner rulings of 2026-09-13, shipped together because both are the SALT cap.

### Q8 — when a fallback may degrade instead of stopping

AGENTS.md §2 said STOP; AGENTS.md §4 said an over-stating fallback is a hard
error and an under-stating one degrades with a banner. Neither said which won,
so it was being decided case by case — and by the time the question was asked
there were two instances in the engine, both over-stating. The rule now in §2:

> A fallback may DEGRADE instead only when BOTH hold: the output is ADVISORY,
> and it UNDER-states. Anything else STOPs.

`compute_ca_itemized` refuses when the R&TC §17077 threshold is unfetched and
the return carries deductions subject to the phase-out. It used to note the gap
loudly, which is what the under-statement direction is owed and not this one —
skipping the phase-out over-states a deduction on a FILED return. The gate the
banner already had is kept and matters more now: §17077 exempts medical and
investment interest, so a return made only of those has deductions and still
nothing the phase-out can touch, and refusing there would be a STOP on correct
data.

Paying for the §2 sentence at the byte cap moved two ANSWERS out of the
instruction file into the docs that already carried them: §8's list of
statutory-fixed thresholds (to `tax-law-cache.md`, which had been deferring to
§8 for it) and §4's `meta.limitations` manifest mechanism (to
`skill-catalog.md`, which states it in more detail).

### Q10 — §164(b)(7) is a statutory step schedule

The question was whether a skill may STATE a figure that is both statutorily
named and fetched. The answer was yes, on the `tax-safe-harbor` terms — and
implementing it found that `ptet._salt_cap` had three defects, every one in the
over-stating direction:

1. **A flat $40,000 for the whole of TY2025-2029.** (A)(ii) names $40,400 for
   TY2026 and (A)(iii) steps 101% a year after it. X6 found this figure wrong in
   the skill's prose and corrected it there; the engine had the same error, and
   the skill was then right about a number the code still got wrong.
2. **MFS as a second hardcoded pair.** §164(b)(6)(B) makes a separate return
   HALF the applicable limitation amount — one rule, not two figures to keep in
   step, and the pair was already out of step for TY2026.
3. **The (B) MAGI phasedown not modeled at all**, for a tool whose users are
   pass-through owners — the population most likely to be over the threshold.
   30% of the excess over $500,000 (TY2025; $505,000 for TY2026, then 101% a
   year), halved for MFS, never below $10,000. The OBBBA increase is fully spent
   by about $100,000 of excess MAGI, so this is not a rounding difference.

Q8's ruling made (3) a choice between refusing and modeling, and the statute
specifies the whole of it, so it is modeled.

### What the review corrected, including the direction

**The bias direction in the first draft was backwards, and the fix was too
strict because of it.** The docstring said an over-stated cap "inflates the PTET
benefit". It deflates it: more SALT headroom means more of the state tax was
federally deductible ANYWAY, so the election rescues less — measured at
$5,580 of net saving with no headroom against $960 at $15,400 of it. So
omitting the phasedown leaves the cap too high, which UNDER-states the benefit
and errs toward not electing. (X6's converse claim was right and stays right: a
cap too LOW over-states the benefit.)

That matters because it is the rule's own test. `/tax-ptet` is advisory and the
omission under-states, so §2 PERMITS a degrade — the first draft made it a STOP,
stricter than the rule it was written to implement. It is a loud banner now,
naming the direction: modeled headroom is a CEILING and the benefit below it a
FLOOR. CA's refusal stands, because that one over-states a deduction on a filed
return. The two cases landing on opposite sides is the rule working.

**A fetched cap is a BASE, not an answer.** The first draft treated a populated
`federal.salt_cap` as already post-phasedown and skipped both the reduction and
the banner. The cache carries the YEAR's statutory amount — identical for every
taxpayer — and (B) reduces it per RETURN, so fetching the figure the cache
exists for was the one way to get the unreduced cap: `_salt_cap(2026, "MFJ",
{"amount": 40400}, 600000)` returned $40,400 where the statutory path returned
$11,900.

**Three smaller ones, all reproduced.** The banner computed its reduction
against the UNHALVED statutory base, so every MFS return read as a phasedown of
half its cap — an ordinary §164(b)(6)(B) halving misreported as a MAGI
reduction — and promised a $10,000 floor to a filer whose floor is $5,000. A
negative MAGI was refused, though §164(b)(7)(B)(iv) defines it as AGI plus the
§911/§931/§933 exclusions and a loss year is a real return. And CA's new
refusal fired on a return made entirely of the exempt categories: the subject
amount was the ROUNDED subtotal minus unrounded exempt figures, leaving about
1e-13 of "deductions subject to the phase-out" — enough to arm the refusal,
which then printed "$0.00" as the amount at stake. A refusal whose own message
says nothing is at stake is the cry-wolf case arriving through a true rule.

The caveat that said the phasedown was "not modeled here" is gone with the
limitation, and what replaced it says which of the two actually happened. A
caveat describing a limitation the code no longer has is the lying kind: it
teaches a reader to discount the figure that is now right.

### A test had certified the defect

`test_salt_cap_absent_falls_back_to_statutory`, in the TY2026 absence matrix,
asserted $40,000 for TY2026. The matrix exists to prove each fallback is
reachable and correct, and it had pinned the wrong one — so the flat schedule
was not merely untested, it was **certified as intended**. That is why nothing
went red when X6 found the same figure wrong in the skill text: the engine's own
absence test agreed with the error.

## X8 — value-validate the filing status across the whole engine

Q9 converted `state/ny.py` and pinned the two depth modules. Sweeping the
siblings found the same defect class in three more, and a finding's scope is
where the defect lives rather than where it was spotted.

### Two reproduced defects, both silent

`ptet._salt_cap(2026, "Married Filing Separately")` returned **$40,400** where
`"MFS"` returns $20,200 — DOUBLE a separate return's cap. The test was
`(filing_status or "").upper() == "MFS"`, a type check wearing a value check's
clothes: the spelled-out status is not `"MFS"`, so it took the joint branch. An
over-stated cap over-states SALT headroom, which under-states the modeled PTET
benefit; the reverse spelling error over-states it. Either way, a plausible
number with nothing downstream able to notice.

`retirement_dashboard` read the `single_covered` IRA phase-out window for the
same spelling where `"MFJ"` reads `mfj_covered` — a narrower band than the
taxpayer is entitled to, across both the traditional-IRA and Roth fractions.

### Two that refused the CANONICAL spelling

Neither mis-branched, so neither is a defect; both were a fifth and sixth
spelling of one rule, and each rejected the engine's own output.
`check_form_8938_required` carried a private lowercase-only set, so `"MFJ"` —
exactly what `_normalize_filing_status` returns — raised. `state_brackets_for_
status`, the accessor kept from the NY bracket crash, looked a raw argument up
in a lowercase-keyed cache and reported the miss as a missing SCHEDULE, which
reads as "re-run /tax-setup" about a cache that is complete. That message is now
left to a canonical status the cache genuinely lacks, which is the fetchable
case.

### The sweep, and why its roster is derived and its classification declared

The hand-built list that opened X8 had **ten** entries. The derived roster has
**twenty-nine** — the first sweep only counted unconditional top-level
validation, so nineteen functions that do validate were invisible to it, and a
twentieth that did not could have hidden among them just as easily. That is the
same shape as the figure lint before #155: a list nobody has finished reads
exactly like a list with nothing left on it.

Two questions, and only one is answerable from the tree. WHICH functions must
be accounted for is a fact about the source. HOW each one is accounted for is
not: four validators live in four modules, and two functions validate by
DELEGATING, which no predicate over a single function body can see. So the tree
supplies the roster and `VALIDATION` supplies the classification, and a function
in neither fails.

Each half is then held to what it can actually prove. A `"direct"` claim is
checked against the tree. A `"delegates: X"` claim is checked in both halves —
the caller provably hands `filing_status` to the named callee, and the callee
provably refuses a status that does not exist. `_salt_cap` is deliberately NOT
in the validator set even though it validates internally, because listing it
would let `ptet.analyze` read as direct when it is delegating.

**The first delegation probe was a false green.** It called the two functions
with `filing_status` alone and accepted `"positional argument"` in the error —
which the resulting `TypeError` satisfies without reaching any validator, so it
would have stayed green with every validator deleted. Calling them properly is
not cheap either: both validate other arguments long before the status, so an
end-to-end probe needs a whole return as a fixture. Splitting the claim into the
half the tree can answer and the half a call can answer is what made it
checkable at all.

The hand-walk sweep from #152 also fired during this change: `_entry_points`
enumerated `agents/lib` itself instead of asking `tests/engine_tree.py`. It
takes `engine_files()` now.

### What the review found — the sweep was certifying a falsehood

**`"direct"` proved presence, not execution.** The first version checked the
claim with `ast.walk` over the whole function body, so a validating call in an
untaken branch satisfied it. Wrapping a real normalisation in `if False:` left
the class green while the function accepted an invalid status — and
`compute_qbi_deduction` was a standing counterexample the entire time, returning
`total_deduction=200.0` for `"Married Filing Separately"` while declared
`"direct"`, because its validation sits on the above-threshold path.

This is the same defect twice: the `state/` sweep in #156 had it, it was fixed
there with a probe, and the engine-wide sweep written days later reintroduced
it. The predicate is now the strict one — an unconditional top-level statement —
and the classification is THREE-way rather than two, because four functions
legitimately validate only where the status can change the answer. Those are
`"conditional:"` now, each carrying the reason the unvalidated path is safe, and
the QBI one is probed by calling it: below the §199A threshold the deduction is
20% for every status, so the path that never validates cannot disagree with the
path that does. A category that certifies what it cannot check is worse than no
category.

**A flat bracket list skipped the check.** `state_brackets_for_status` returns
early for a state whose `brackets` is a plain list, and the validation had been
put beside the SCHEDULE lookup two branches down — so the module that had just
been converted still had an arm returning bands for a status that does not
exist. The status is what the caller is asking about; refusing a bad one cannot
depend on how one state happens to key its table.

### The second review pass, and the live defect it found

**§199A halved a deduction for a missing status.** Above the threshold
`compute_qbi_deduction` read `is_mfj = _normalize_filing_status(filing_status)
== "MFJ" if filing_status is not None else False` — so a `None` status silently
took the non-MFJ branch, and MFJ's phase-in range is DOUBLE every other
status's. Reproduced: `total_deduction` of **$6,666.67** where an MFJ filer is
owed **$13,333.33**, with `form_8995a_applied` reporting True and no warning.
That under-states a deduction on a FILED return, so under §2 as amended by Q8 it
STOPS. Below the threshold the deduction is 20% for every status and the branch
is not reached, so nothing is refused where nothing can differ — the same gate
shape as the CA phase-out and the PTET banner.

The `"conditional:"` reason written for it in the first pass was TRUE and
INCOMPLETE: it described the below-threshold path correctly and said nothing
about the above-threshold one, which is where the number was wrong. A reason
that only covers the safe half is how a real gap gets a justification written
for it.

**The predicate was wrong in both directions at once.** It certified validation
that never runs — `x = False and _validate(filing_status)` is a top-level
assignment whose call short-circuits away, and an early `if filing_status is
None: return ...` sits above the validator and skips it. And it rejected
validation that always runs: a bare `return _validate(filing_status)`, and every
function with an ordinary guard clause before the status check, which is five of
them.

It scans the PROLOGUE in order now, stopping at the first statement that can
divert control, with three things declared transparent because they cannot: a
nested `def`; a `try` that coerces and re-raises without returning; and an `if`
whose every branch terminates — a raise-guard always, an early `return` only
when that branch never reads the status, since an early exit that builds its
answer from an unvalidated status is the whole thing being looked for. Eight
shapes are probed directly, four that must fail and four that must pass,
because the engine contains none of them — which is exactly when a broken
predicate looks fine.

## Q8 + Q10 follow-up — what the re-review of the MERGED change found

PR #158 was reviewed once, its findings were fixed, and the FIX COMMIT was never
reviewed before merging — about 180 lines of `ptet.py`, more new engine code
than the original review saw. Re-reviewing the merged result found three more,
all reproduced, and all the same family: a caveat or a refusal firing where the
figure it names cannot change the answer.

The arithmetic core was recorded as clean on that second reading — the statutory
steps and the distributed MFS halving both held. That verdict was wrong, and the
next round found why: see the floor below. A second reading that clears an area
is evidence about the cases it tried, not a certificate over the area.

- **The CA refusal fired at a zero or negative AGI.** §17077 reduces by 6% of
  AGI OVER a published threshold, and every published tier is positive, so at
  AGI ≤ 0 the phase-out reduces nothing at any of them. Refusing there is a STOP
  on a return the missing figure cannot affect. The `subject > 0` half of the
  same shape was fixed one round earlier — the gate needed both.
- **The degrade banner quoted a flat 30% to an MFS filer**, who is reduced at
  15%: §164(b)(6)(B)'s halving distributed through the reduction. The note
  overstated their exposure twofold, and a caveat that misreports the rule it is
  warning about is the lying kind. It quotes the rate it would actually apply.
- **The degrade banner fired where headroom was already zero.** The phasedown
  can only LOWER the cap, so where other state and local taxes already exhaust
  the un-reduced one there is no headroom at any MAGI and the benefit is
  identical either way. That gate needs `salt_room`, so it now sits after it
  rather than before.

Three conditions had already been put on that banner and a fourth was still
missing. The lesson is not about this banner: a gate written from the cases
someone thought of covers the cases someone thought of, and the only way the
fourth arrives is somebody trying inputs the author did not.

## The fifth round — and the arithmetic defect the fourth introduced

That follow-up was itself reviewed before merging, which is how the rest of
this was found. **The floor was raising caps.** §164(b)(7)(B)(iii) floors the
RESULT OF THE REDUCTION, and a floor on a reduction cannot raise the thing it
reduces — but written as a bare `max(floor, base - rate * excess)` it did. A
cache carrying a cap below $10,000 came back RAISED to it the moment a MAGI was
supplied, so passing `--magi` INCREASED the modeled headroom. `git log -S` puts its origin in
#158's per-status rewrite, and it SHIPPED: the bare `max(floor, …)` is on `main`
at `ptet.py:490` as this is written. It was not caught before landing — it was
caught ON `main`, by a round that re-read merged code, which is the only reason
this correction can be made at all.

**The banner's fourth condition was still the wrong question.** "Is there
headroom" is not what decides the answer: what a PTET election rescues is
`min(salt_room, total_ptet)`, so headroom that is large at BOTH ends of the
range a missing MAGI spans changes nothing even though the headroom itself
changes a lot. Both ends are priced now — the un-reduced cap and
§164(b)(7)(B)(iii)'s floor — and the banner fires only when the CREDITED amount
differs between them.

**The cap is reported whether or not the benefit depends on it.** Gating the
banner on the benefit left the un-reduced figure printed flatly as "your
$40,400 SALT cap" on a return whose real cap could be $10,000. The benefit
banner stays gated on the benefit; the FIGURE carries its own qualification.

**And the CA refusal had a third condition missing**: the phase-out only
reduces the itemized total, so when that total is already below the CA standard
deduction no threshold can change the ELECTION, and the number on the return is
the same at every one of them.

Five rounds on one area, and each round's fix needed the next round. The pattern
worth keeping is not the individual gates: **a gate is a claim about which
inputs cannot matter, and that claim is only ever as complete as the inputs
somebody tried.** The rounds stopped finding new conditions when the gate
started comparing the OUTCOME at both ends of the unknown range instead of
enumerating the cases where the unknown was thought not to matter.

### Sixth round: the exemption that assumed a standard deduction exists

The CA third condition above shipped with a defect of exactly the shape it was
written to prevent. "The standard deduction wins at every threshold, so the
missing figure cannot change the answer" presumes the filer HAS a standard
deduction — and §63(c)(6)(A) takes it away from an MFS filer whose spouse
itemizes. `compute_ca_itemized` FORCES `use_itemized` for that filer further
down the same function, so the itemized total is what reaches the return and
the phase-out does change it. A $500,000-AGI separate filer with $1,000 of real
estate tax got a $1,000 CA deduction where the phase-out gives $200 — an $800
OVER-statement on a filed return, the direction AGENTS.md §2 makes a hard error.
The reduction is capped at 80% of the subject deductions, so $200 is the answer
at every threshold up to about $486,667 on this return, and $1,000 at a
threshold of $500,000 or more. The figure is therefore undetermined without the
cache, and a gate is right to refuse rather than compute it. Two earlier drafts
of this paragraph were wrong in opposite directions: the first called the figure
*knowable*, contradicting the gate shipping beside it; the second said the
breakpoint sits above "every published §17077 tier", which is a claim about
tiers this paragraph never fetched and is false for the joint one. The
breakpoint is a property of THIS return's numbers, not of the schedule. The exemption and the forcing each read correctly alone, which is why the
round that wrote the first did not re-read the second.

**So the enumeration was replaced rather than extended.** Adding
`not mfs_spouse_itemizes` would have been a sixth hand-proof of "this input
cannot matter", and five of those had already produced one wrong answer. The
refusal now prices BOTH ENDS of the range the missing threshold spans — the
reduction is monotone in the threshold and reaches the return through a single
expression, so a zero-threshold evaluation and an infinite-threshold evaluation
settle it, and it refuses only where those two differ. `thr >= 0` is the only
fact about §17077 it needs, and that is not an indexed figure. The election it
compares against is the one the function actually makes, called rather than
restated, which is the coupling whose absence caused this round. Checked against
the four enumerated conditions over a grid of AGI, subject, subtotal, standard
deduction and the coupling flag: measured against those conditions AS CORRECTED
no verdict differs where a phase-out is possible, and it adds no STOP — it
removes the sub-cent ones where 6% of AGI rounds to $0.00 at every threshold.
Measured against what actually SHIPPED it adds 116 stops over that grid, every
one an MFS filer whose spouse itemizes. Both numbers are stated because "adds
no STOP" is true of one comparison and false of the other, and only the second
shows the defect being fixed. `ptet.analyze` and `loss_harvest` already
derived instead of enumerating; this was the last site that did not.

**And the bias direction was still recorded backwards in two test docstrings.**
The PTET benefit moves OPPOSITE to the SALT cap — what an election rescues is
the state tax the cap blocks, so more cap means less to rescue: $5,580 of
modeled savings at a $10,000 cap against $0 at $60,000 on the same fixture.
Both docstrings said an over-stated cap "inflates" the benefit. One of them used
that to argue the §2 degrade path was CLOSED when it was open; the other used it
to certify the MFS reading taken as the one that "cannot over-state", when in
fact the taken reading advertises the LARGER benefit and therefore rests on
§164(b)(7)(B)(i)'s text alone. A correct statement of the same direction sat
a few tests away in the same file the whole time.

The direction is now asserted rather than argued: the modeled benefit is pinned
NON-INCREASING in the cap across four caps. A claim about bias direction that
lives only in prose has now been written backwards three times in this epic and
caught three times by someone running the number.

### Seventh round: the sibling that was cited as precedent

`compute_ca_itemized` enforced half of California's separate-return rule. FTB
Form 540 requires that separate spouses "either both itemize your deductions
(even if the itemized deductions of one spouse/RDP are less than the standard
deduction) or both take the standard deduction" — it binds in BOTH directions,
and only the spouse-ITEMIZES half was implemented. A separate filer whose spouse
took the STANDARD deduction still itemized whenever itemizing was larger:
**$12,000 of CA deduction against the $5,706 allowed on the reproduced case,
$6,294 OVER-stated on a filed return**, which is AGENTS.md §2's hard-error
direction.

**It was the sibling of a defect already fixed.** #156 found the same defect in
the OPPOSITE half of `compute_ny_itemized` — New York was missing the
spouse-itemizes forcing, California the reverse — and fixed both halves there. The comment it left behind said
`compute_ca_itemized` "has forced the mirror-image federal §63(c)(6)(A) case
since it was written" — true of the half being looked at, and false as a claim
about the function. A finding's scope is where the defect LIVES, not where it
was spotted, and here the sweep stopped because the sibling had been inspected
closely enough to be cited as precedent.

Two framings helped it hide. The skill said CA "mirrors the federal
forced-itemize rule", and it does not — §63(c)(6)(A) denies the standard
deduction when the spouse itemizes and says nothing about the other direction,
while CA and NY both state "both or neither" outright. And `mfs_spouse_itemizes`
was meaningful WITHOUT `married_filing_separately`, because the old forcing keyed
on the flag alone. Now that the coupling keys on the status, the flag alone
would silently restore the ordinary comparison — the standard deduction while
the spouse itemizes, the same over-statement by a different route — so that
combination is refused rather than ignored. One test in the suite carried it and
had been green on it.

The election is one expression, read by the phase-out refusal and by the
election itself, so both halves arrived in both places at once. That structure
came out of the sixth round; this is the first defect it has carried correctly.

**Then four reviews found that the fix had introduced two more.** Worth
recording, because both were the same mistake in different clothes.

`married_filing_separately` defaulted to False and the intake step records only
the OTHER flag, so a caller doing exactly what the skill said produced the
original $6,294 over-statement one omitted keyword away. And once the coupling
bound both ways, `mfs_spouse_itemizes=False` stopped being NEUTRAL — it had
meant "run the ordinary comparison" and now asserted something about the
spouse's return — so an omitted answer asserted it silently, barring a filer
from itemizing whose spouse in fact itemizes. That direction over-states by up
to the whole standard deduction on the commonest separate-return shape: one
spouse holding the mortgage and property tax, the other holding almost nothing.
Both are refused now, and the flag is tri-state so that UNKNOWN is not "no".

The same structure that carried the fix correctly also carried a regression:
with the election constant, both endpoints of the phase-out gate returned the
standard deduction, so the §17077 refusal could never fire for that filer and
`itemized_total` came back un-reduced. Refusing there would be a STOP on a
return the missing figure cannot change, so the counterfactual is LABELLED
instead — it used to print "itemizing would have been $12,000" to a preparer
deciding whether the spouse should itemize, over-stated by the whole uncomputed
reduction.

**And the prose was wrong in five more places than the two the fix named.** The
skill still told the agent line 18 was the "larger of" the two figures, which
defeats `use_itemized` in BOTH directions and re-creates the defect one step
downstream. The function's own docstring, the question put to the TAXPAYER, and
the intake bullet all still carried the one-directional framing. `epic-W.md`
still asserted the strongest form of the refuted claim. Three new references
named `compute_ny_it196`, a function that has never existed. And the timeline
was wrong twice: #156's citation stood for one DAY, not a month, while the half
it cited had been missing since #33 on 2026-07-13 — two months.

**And a fifth review found the §2 reasoning itself was wrong.** The draft above
argued that a spouse who is CONSIDERED UNMARRIED and files as head of household
is outside the coupling, that the engine cannot see it, and that a banner
covered it because barring them merely UNDER-states. §2 does not say that. It
allows a banner instead of a stop only when the output is ADVISORY *and*
under-states, and line 18 of a filed CA return is not advisory. An
under-statement is not a safe harbour on a return — $12,000 against $5,706 is
the same $6,294, pointing the other way.

So the exception is modelled rather than narrated.
`mfs_spouse_is_a_separate_filer` says whether the spouse files their own
separate return or is considered unmarried, and the function REFUSES without it
— but only where the two answers give different numbers, which is the same
both-ends discipline the §17077 gate uses. Most returns can leave it unknown;
the return where $6,294 turns on it cannot proceed without it. The interview
asks after every "no" regardless, because a question costs nothing there and a
refusal arrives mid-computation.

The exception is also ONE-DIRECTIONAL, and the first draft of it was symmetric.
§63(c)(6)(A) zeroes the standard deduction of "a married individual filing a
separate return where EITHER spouse itemizes", and this filer is such an
individual whatever their spouse's filing status — so a spouse who itemizes
still forces itemizing here even when they are considered unmarried. Written
symmetrically it handed that filer `max(total, std)` and a standard deduction
they do not have. The same round also added the parameter to the positional
block, which shifted `married_filing_separately` by one for every caller that
passed it by position, and gave the exception a result without giving it a
note — so it selected $12,000 and then said the standard deduction had been
taken, contradicting itself by the whole margin.

**The interview was also collecting one federal answer for three
jurisdictions.** The elections are independent — this function's own note says
CA itemizes even when the federal return takes the standard deduction — so a
spouse may take the federal standard deduction and itemize for California.
Feeding the federal boolean to `compute_ca_itemized` barred this filer from
itemizing on a CA return where the spouse had in fact itemized: $6,294 again,
a third time, in the third direction. The question now asks per return, and
asks the follow-up that the coupling turns on.


## X7 — planning-tool carry-forwards

Merged as PR #218 on 2026-10-03 after local review, 5,952 passing full-suite
tests, a subsequent 399-test final focused run and all six CI checks passing.

The PTET SALT-offset probe reduces the deduction directly rather than adding
ordinary income, so AGI-gated credits cannot contaminate the estimated rate.
The existing keystone deduction parameter supplies the knob V1 left open.

The scenario keystone now excludes the deducted self-employed tip portion from
QBI, bounded by applied Schedule 1-A and business income. `tax-compute` records
`se_tips_deducted` from the worked source split. An older tipped business return
without the split refuses, rather than inferring employee or business tips.
Scenario, Roth, PTET and harvest carry the normalized input through their runs.

Harvest retains each replacement purchase in its existing FIFO matcher and
emits a read-only basis plan per sold lot. Known taxable replacements gain the
disallowed loss and the sold holding period; the seller's own IRA/Roth-IRA replacements
lose it permanently. Missing quantities, account classifications or IRA ownership leave the
basis unresolved. Spouse IRAs and other retirement regimes require manual treatment. Purchase costs, where supplied, price the matched shares'
before/after basis. Rounded allocations conserve the lot's disallowed loss.
The plan adds no current-year savings and never mutates the input holdings.
Future sales and wash-sale chains retain their scope limits.

Matching and basis authority: [IRS Publication 550](https://www.irs.gov/publications/p550)
and [Revenue Ruling 2008-5](https://www.irs.gov/pub/irs-drop/rr-08-05.pdf).

## X9 — figure lint across Markdown wraps (#228)

The shared ordinary/safe-harbor detector normalizes prose wraps within Markdown
blocks and pairs inline backtick runs by length. Identifier and CLI text cannot
supply limit words; amounts and K/M suffixes remain visible. The bounded
sentence window records every amount in a matched clause, protecting paired
exclusions and joint/separate thresholds as well as wrapped floors.

The corpus comparison retained all legitimate former amounts and removed the
CLI `--max` false alarm. Newly visible allowances are already-stated statutory
figures, with thresholds/floors pinned to engine constants. Ten regression
tests cover both false-negative and false-positive cases and exercise both
actual gates. No new tax-law amount enters a skill. Design:
`docs/superpowers/specs/2026-10-03-wrapped-figure-lint-design.md`.


## X10 — retirement split, HSA eligibility and state conformity (#224)

The dashboard tracks Roth deferrals within aggregate employee YTD and prices
only the plan-confirmed pre-tax portion of remaining high-earner catch-up under
Treas. Reg. §1.414(v)-2(b)(1). Missing split or payroll confirmation retains
Roth-only savings. The indexed high-earner threshold is fetched for every
covered year; the former annual literal/default is removed.

HSA capacity requires confirmed first-of-month eligible months and December
eligibility, or a twelve-month tier schedule. Tier changes and catch-ups are
prorated; a separately confirmed last-month election takes the greater limit
and carries the following-year testing-period reminder. A joint spouse's
separate catch-up uses its own eligibility inputs without doubling the family
base. Previously eligible coverage remains visible even if current coverage
is none. Missing eligibility blocks capacity recommendations.

A year/source-bound per-account matrix covers all 50 states and DC. Its law
values are fetched on demand from current-year primary guidance; it contains
no implicit conformity findings. Absent, stale, unpinned and partial treatment
shows zero state savings with an unverified note. False and true are explicit,
independent account findings. The setter binds complete rows to the cache year;
partial edits invalidate that binding. Historical exception sets no longer make
absence read as a deduction. See the retirement-inputs design spec for sources
and scope; synthetic matrix tests are arithmetic examples, not legal findings.
