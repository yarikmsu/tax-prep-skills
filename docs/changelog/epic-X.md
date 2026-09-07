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
