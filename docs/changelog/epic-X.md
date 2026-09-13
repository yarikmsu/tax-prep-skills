# Epic X — Tax correctness, known and unfixed

Backlog: `docs/roadmap/2026-09-05-staff-agent-review.md` (adopted 2026-09-05). X1-X3
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
