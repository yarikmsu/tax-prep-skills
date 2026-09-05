# Safe-Harbor Advisory Skill — Implementation Plan

> **SHIPPED 2026-08-27** — executed as PR #78 (squash `47db081`), inside the Sep 15
> window the S1 backlog entry boxed it to. Suite 2431 → 3221, CI green on 3.12 and
> 3.14; eighteen review rounds. Two placements deviate from the plan as written, both
> deliberate: the shared skill-set constants went into the existing `tests/skill_text.py`
> rather than a new `tests/skill_sets.py` (Task 1), and the safe-harbor constants into
> `agents/lib/tax_computations.py`, which `projection.py` imports, rather than into
> `projection.py` itself (Task 2). Everything else landed as specified.

**Goal:** Ship ONE engine-free skill — `tax-safe-harbor` — that answers "will I owe an
underpayment penalty, and what do I pay to be safe?" using only numbers the taxpayer
reads off their own prior-year Form 1040. No workspace, no `result.json`, no tax-law
cache, no `agents/lib/`. It is the one useful calculation in this toolkit that is
**structurally incapable of going stale**, which is what makes it safe to run where
nothing can be verified.

**Architecture:** A single `.agents/skills/tax-safe-harbor/SKILL.md` holding the whole
procedure. It calls no engine — deliberately, and that property is machine-checked in
both directions rather than left as an absence. Where the engine IS present the skill
hands off to `/tax-project` + `/tax-withholding` instead of competing with them.

**Stands in for a spec.** The design decisions are §2 below rather than a separate
`docs/superpowers/specs/` file. A second document restating them is a second thing to
drift; precedent for pairing them exists but the decision surface here is small.

**Scope note:** ONE skill, not a bundle of five. That was a deliberate narrowing —
the calendar picks the product, and only one calculation survives the constraint in §1.

---

## 1. Why this calculation and no other

Every other planning skill needs current-year brackets. In a channel with no engine
there is no trustworthy fetch (AGENTS.md §2 — several agents default to cached or
time-shifted web results), so every other skill either lies or refuses.

The safe-harbor thresholds do not come from the cache. `agents/lib/projection.py:634`
and `:639` hardcode `$150,000 / $75,000 MFS` and the `1.10 / 1.00` factor precisely
because they are statutory and unindexed. Same for the CA constants at `:53-69`.

So the **prior-year prong is a sufficient condition provable with zero current-year
law**: pay 100% (or 110%) of last year's total tax and no underpayment penalty can
attach. Inputs are two numbers off a return the user already filed.

**Bias direction (AGENTS.md §4).** `compute_safe_harbor` takes
`min(90% of current, prior-year prong)` — so the prior-year prong alone can only
**over**-state what the user must pay. Overpaid estimated tax comes back as a refund;
underpayment does not come back as anything. The failure this skill can produce is
recoverable and the failure it prevents is not. That asymmetry is the entire licence
for this skill to exist outside the engine, and it must be stated in the skill body,
not just here.

## 2. Design decisions

**D1 — Engine-free is a declared property, not an absence.** A skill sitting beside
twenty engine-gated skills without the gate is the exact shape of the bug PR #75 fixed.
So the exemption is POSITIVE: a new `ENGINE_FREE_SKILLS = {"tax-safe-harbor"}` set,
with tests asserting both directions (§5). An absence proves nothing; a declaration
that is contradicted by the skill text fails a test.

**D2 — Bounded arithmetic.** With no engine the model does the math, so the skill may
only ask for arithmetic a reader can check by eye: one multiplication, one subtraction,
one division. Anything needing brackets, phase-outs, or a worksheet is out of scope BY
CONSTRUCTION — that is the boundary that keeps D1 honest. The skill shows its work.

**D3 — The 90%-of-current prong is refused, not attempted.** It needs current-year law.
The skill names it, says it may be cheaper, and points at the local toolkit. A refusal
that explains what it would take to answer is not a dead end.

**D4 — Unknown prior-year AGI assumes 110%, diverging from the engine.**
`compute_safe_harbor:638` assumes the 100% factor and sets `prior_agi_missing` so a
renderer can print a may-be-UNDERSTATED note. That is right where a banner will be
read. In an unsupervised chat window it is not, so the advisory takes the opposite
default — assume 110%, say so plainly, and offer to lower it if the user supplies AGI.
**This is a deliberate divergence and must be commented as one at both sites**, or it
reads as a bug the next time someone diffs them.

**D5 — Withholding vs. estimates is the payload.** Withholding is treated as paid
ratably across the year; an estimated payment counts when made. So a W-4 line 4(c)
bump, or withholding from a December distribution, cures a shortfall that opened in
Q2 — a September voucher cannot. This is the non-obvious rule most users are missing
and it drives `compute_w4_4c` already.

**D6 — CA gets a block, other states get a refusal.** The CA facts are statutory and
unindexed: the 30/40/0/30 installment weighting with **no September installment**
(R&TC §19136.1, `projection.py:53-58`), the `$1M / $500k MFS` cutoff that removes the
prior-year prong entirely (§19136.3, `:60-64`), the `$500 / $250` de minimis
(§19136, `:66-69`). Other states are named as unmodeled, matching AGENTS.md §4's
CA-first scope. Silence would read as "no state issue."

**D7 — Handoff, not competition.** `tax-withholding` requires `result.json`,
`projection_inputs.json` and the cache; this skill requires none of them and is
correspondingly cruder. When a workspace is present the skill says so and defers.

**D8 — One closing question.** The skill ends by asking *"what did you want this to do
that it couldn't?"* — the only learning signal available from a channel that returns no
telemetry.

## 3. Explicitly OUT of scope, with reasons

- **§6601 interest estimator.** The earlier proposal paired this with the October 15
  crowd. It does not survive §1: the underpayment rate is reset QUARTERLY under §6621
  (federal short-term + 3 points). Any number the skill states goes stale within
  months, in a channel where nothing can check it. Admissible only if the user pastes
  the current rate from IRS.gov — treat as a v1.1 decision, not a launch item.
- **Failure-to-file minimum penalty.** The §6651 *rates* are statutory, but the
  minimum penalty for a return over 60 days late is INDEXED. One indexed figure
  disqualifies the whole block under §1. Either omit it or fetch it — never carry it.
- **The 90%-of-current prong** (D3) and **annualized-income installments** (Form 2210
  Schedule AI): both need current-year law and a worksheet, failing D2.
- **Any identity intake.** No SSN, no name, no address. Nothing this skill asks for is
  PII beyond two figures the user already has.

## 4. Baseline — verified 2026-08-12 against `feat/engine-preflight-and-readme` (`6fe4b3f`)

- `agents/lib/projection.py` already holds every primitive: `compute_safe_harbor`
  (`:623`), `compute_ca_safe_harbor` (`:681`), `compute_w4_4c` (`:653`),
  `compute_estimated_vouchers` (`:662`), `ES_DUE_DATES` (`:51`), `CA_ES_SCHEDULE`
  (`:53`), `CA_HIGH_INCOME_AGI_LIMIT{,_MFS}` (`:63-64`), `CA_ES_DE_MINIMIS{,_MFS}`
  (`:68-69`). **No new tax math is required** — this is the one piece of existing,
  tested math that survives the trip out of the repo.
- The `$150,000 / $75,000` threshold and the `1.10 / 1.00` factor are INLINE literals
  in `compute_safe_harbor` (`:634`, `:639`), not named constants. Task 2 extracts them.
- `tests/test_preflight.py:55` derives the engine-calling roster from skill text via
  `_calls_engine` (`:43`) — a skill containing neither `agents/lib/` nor `agents.lib.`
  falls out of that roster automatically and is not required to carry the gate. That
  is the mechanism D1 makes explicit rather than relying on.
- `tests/test_portability.py:19-24` hardcodes `PRODUCT_SKILLS` (20 names);
  `:34` `CONTRIBUTOR_SKILLS`; `:36` `EXPECTED_SKILLS` must cover every directory.
  Constraints a new skill inherits: `MAX_SKILL_LINES = 500` (`:65`), frontmatter
  `name` = directory name, non-empty `description`, `license: Apache-2.0` (`:69`), a
  `.claude/skills/<name>` relative symlink, and the vendor-neutral-language lint.
- `CONTRIBUTOR_SKILLS` is duplicated in `test_portability.py:34` and
  `test_preflight.py:25`. `ENGINE_FREE_SKILLS` must NOT repeat that mistake — define
  it once and import it (Task 3).

## 5. Tasks

### Phase 1 — the invariant, before the skill exists

- [x] **Task 1 — Decide the home of the shared skill-set constants.** Move
      `CONTRIBUTOR_SKILLS` into one module (`tests/skill_sets.py`, beside the existing
      `tests/skill_text.py`) and import it from both test files, collapsing today's two
      copies. Add `ENGINE_FREE_SKILLS` there, initially EMPTY.
- [x] **Task 2 — Extract the safe-harbor constants** in `agents/lib/projection.py`:
      `PRIOR_YEAR_AGI_THRESHOLD = 150_000`, `..._MFS = 75_000`,
      `PRIOR_YEAR_FACTOR_HIGH = 1.10`, `PRIOR_YEAR_FACTOR = 1.00`, used by
      `compute_safe_harbor`. Behavior-preserving; the existing tests must stay green
      untouched. This is what Task 7 pins the skill prose against.
- [x] **Task 3 — Write the two-directional test** in `tests/test_preflight.py`:
      (a) every skill in `ENGINE_FREE_SKILLS` contains NO engine call in ANY of the
      three forms — path, module, and inline `python3 -c` — reusing `_calls_engine`
      and `_shell_units` as a NEGATIVE assertion; (b) `ENGINE_FREE_SKILLS` is disjoint
      from the derived engine roster and from `CONTRIBUTOR_SKILLS`; (c) a named
      positive control, so the assertion cannot pass vacuously on an empty set.
      **Mutation-check it**: add a fake `agents.lib.` line to the skill and confirm red.

### Phase 2 — the skill

- [x] **Task 4 — Scaffold** `.agents/skills/tax-safe-harbor/SKILL.md` with frontmatter,
      the `.claude/skills/tax-safe-harbor` relative symlink, `PRODUCT_SKILLS` +
      `ENGINE_FREE_SKILLS` registration. Suite green with a stub body.
- [x] **Task 5 — Intake and refusals.** Four inputs: prior-year total tax (1040 line
      24), prior-year AGI (line 11), filing status, YTD withholding + estimates paid.
      Missing prior-year tax → STOP and say why (there is no fallback that does not
      need current-year law). Missing AGI → D4's 110% assumption, stated. No identity
      data is requested at any point.
- [x] **Task 6 — The answer.** Required annual payment, amount still owed, and the two
      routes to close it (D5): W-4 4(c) per remaining paycheck vs. remaining 1040-ES
      vouchers, with the ratable-withholding rule explained rather than asserted. Show
      the arithmetic (D2). Carry the over-statement banner from §1.
- [x] **Task 7 — CA block** per D6, plus the explicit "other states not modeled" line.
- [x] **Task 8 — Handoff (D7) and the closing question (D8).**

### Phase 3 — proof and docs

- [x] **Task 9 — The inverse-of-preflight check.** Copy `SKILL.md` ALONE into an empty
      directory and confirm the procedure is executable end to end with nothing else
      present. This is the property the whole plan exists to deliver, so it gets an
      explicit verification step, not an assumption.
- [x] **Task 10 — Prose-vs-engine parity test.** Assert the skill text cites the same
      threshold and factor values as the Task 2 constants, so the restated law cannot
      drift from the computed law. Same for the CA constants.
- [x] **Task 11 — Docs.** `docs/contributing/skill-catalog.md` entry (including why it
      is engine-free); `docs/contributing/test-invariants.md` for the Task 3 and Task 10
      invariants; README; AGENTS.md §3 — it is NOT in the pipeline diagram, it branches
      off it like the planning skills, and the §4 engine-preflight rule needs one
      sentence acknowledging the declared exception so the two do not read as
      contradicting each other.
- [x] **Task 12 — Tax-law verification pass.** Every statutory citation in the skill
      body confirmed against IRS/FTB source per AGENTS.md §2, live. The figures are
      unindexed, not immortal.

## 6. Open questions

1. **Where does the wedge funnel?** Raised 2026-08-11, still unanswered. If the answer
   is "nowhere, it is a tool I want to exist," the plan is unchanged but Task 8's
   handoff is the whole point. If the answer is "users, eventually revenue," the
   funnel target should be decided BEFORE Task 8 rather than retrofitted.
2. **Distribution.** In-repo (this plan) makes it testable and portable but reaches
   only people who find the repo. Whether it also gets published to an upload channel
   is a separate decision that does not block any task here.
3. **Ordering vs. publish.** Publishing the repository — Epic R of the 08-12 backlog,
   not a task in this plan — is still open, and this skill is most useful in a public
   repo. The plan does not depend on it.

## 7. Risks

- **The exemption becomes an escape hatch.** `ENGINE_FREE_SKILLS` could be used to
  silence the gate on a skill that genuinely needs the engine. Task 3(a) is what stops
  it: membership is not a permission, it is a claim that gets tested.
- **Prose drift from the engine.** Two statements of one rule. Task 10 pins them.
- **Seasonal window.** Q3 estimated payments are due 2026-09-15 (34 days out); the
  extension deadline is 2026-10-15. Shipped after December this lands in the wrong
  season and should be re-scoped rather than shipped late.
