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
