@AGENTS.md

# CLAUDE.md

Project truth for every agent lives in `AGENTS.md`, imported above. This file adds only
what is specific to working in this repository through Claude Code. Some agents read both
files together, so the two must never disagree.

## Skills

`bash agents/install.sh` symlinks each `.agents/skills/<name>/SKILL.md`, plus its
`references/` directory where it has one, into `~/.claude/skills/<name>/`, then the
gitignore entries and git hooks (`--copy` where symlinks are unavailable, `--hooks-only`
for hooks and gitignore alone). Re-run it after a change that adds or splits a reference:
an install predating progressive disclosure has only the body, which loads as a complete
skill while every step it delegates is missing. `/codex-loop` is the live case — a stale
install leaves an agent improvising the review reply bodies its
`references/reply-verification.md` exists to fix. It and `/state-module` are contributor
tooling, labelled by `CONTRIBUTOR_SKILLS`, not exempted.

## Hooks

`.claude/settings.json` is tracked and registers two `PostToolUse` hooks, both under
`.claude/hooks/`. They fire at TOOL time, which the git hooks cannot — those run at commit
time and everything the second hook protects is gitignored, so it never reaches a commit.
Neither replaces them; the git hooks are what cover other agents.

- `skill-invariants.sh` (`Write|Edit|Bash`, timeout 480s) — fires on CHANGE, not on state.
  A Write/Edit is filtered by its path; a Bash payload is opaque, so it is filtered by
  `.claude/.skill-invariants-stamp` plus one `find -newercm`, and exits 0 in milliseconds
  when nothing watched moved, which is what stops `ls` and `git status` buying a pytest
  run. It then runs only the CHECKS the changed paths can turn red — seven test modules, or
  a node-id subset of one of THEM. A new source file behind an assertion ALWAYS goes in
  `select_modules_for` (or editing it selects nothing), and in `WATCHED_ROOTS` only where
  no entry already contains it — they are `find` ROOTS, so `agents` reaches `agents/lib`.
  Exit 0 silent, 2 blocking with the failing tail, 1 when TERM/INT/HUP killed the pass —
  no verdict, so it must not block. No-ops without `jq` or a `.venv`.
- `harden-runtime-perms.sh` (`Write|Edit|NotebookEdit|Bash`, timeout 60s) — re-asserts
  AGENTS.md §1's `chmod 600` on the runtime artifacts and its `chmod 700` on the runtime
  directories. `Bash` is in the matcher because `result.json` is written by a script.
  Exits 1 to surface a failed permission change without blocking the call; never the
  blocking exit code 2. Its archive net asks `find` for wrong MODES, not
  `-newercm "$STAMP"`: a drift a stamp-filtered scan misses once falls behind the
  advancing stamp forever.

**Both stamps advance on a VERDICT, not on a run** — settle this before editing either.
pytest 0 or 1, or a chmod that ran, moves the change boundary, red included, so a failure
is reported ONCE and the `git diff` and `pytest` needed to diagnose it are not themselves
gated. pytest 2-5, or a chmod that failed, HOLDS it, or an edit sitting on disk through
the outage ends up older than the boundary and is never checked again. Red is REMEMBERED
(stamp v2: version, then one line per red check) and unioned into the next pass AFTER the
fast path, so a quiescent call stays free; green clears it. Advance or hold, NEVER a third state: the archive manifests' mid-publish flag is `.archive-index-dirty`'s PRESENCE, not this stamp's absence. Unlinking the stamp for that ran on EVERY opaque Bash call, and put one symbol in charge of the archive pair's consistency and of every unrelated tree's boundary at once.

**Every timing lives in one place — the `MEASURED COSTS` block in `skill-invariants.sh`.**
Three contradictory sets of one quantity is what this change shipped. Everywhere else — prose,
comment, docstring, assertion message alike — ALLOWED is naming the block
("skill-invariants.sh MEASURED COSTS") and quoting a RATIO from it; FORBIDDEN is restating an
ABSOLUTE: a second, a test count, a per-test rate, a host, a date, which go stale while still
reading as fresh. Exempt is a threshold the code COMPARES against: `SECONDS_PER_COLLECTED_TEST`
and the registered timeout are budgets chosen above the block, not copies of it, so they stay.
Read it for HEADROOM as much as spread: an overrun does not fail, it CANCELS the hook silently.
Both are driven by `tests/test_claude_hooks.py`, real scripts and failing paths included: a hook
that always passes and one that never fires look identical from outside.

**The `.claude/` runtime artifacts are a three-way mirror**: the tracked `.gitignore`,
`IGNORE_PATTERNS` in `agents/install.sh` (the only cover for a workspace whose
`.gitignore` is older or missing), and the `.claude/` case of the PII gate. A test pins
the first two as SETS; the names are not re-listed here, because a fourth copy would drift
as readily as the others. Each HOOK-WRITTEN name ends in `*` — usually a `mktemp` sibling moved over the real name, so a timed-out hook leaves the sibling holding it; on the dirty marker, written in place and holding nothing, the glob is merely defensive. No rule may name
`.claude/` itself; `settings.json` and the `skills/` symlinks are tracked.

## Subagents

`.claude/agents/` holds three tracked definitions under two acceptance regimes, and
`tests/test_claude_agents.py` requires every file there to be in exactly one of them —
an agent in neither is one nothing decides how to accept.

**Two read-only REVIEWERS.** Neither is granted `Write`, `Edit` **or `Bash`** — a shell
is a write capability, and an auditor that can quietly resolve its own findings cannot
be trusted about them.

- `tax-law-freshness-auditor` — the only check on AGENTS.md §2. `tax-review` audits the
  aggregated result and cannot see that a bracket was right for last year, and the tie-out
  compares the ledger to the result, so correct parsing of a wrong figure ties out
  perfectly. As the only agent with network access it is held to a closed minimal tool
  set, a shell plus an outbound connection in a workspace holding a real return being what
  §1 forbids; that bound is enforced by the grant, while "read `tax_law_<year>.json` and
  nothing else" is only prompt-level. `tests/test_claude_agents.py` resolves every schema
  path it names against `create_empty_tax_law`, so a renamed key cannot leave it reporting
  the absence as a finding.
- `bias-direction-reviewer` — scoped to `agents/lib/` diffs and instructed to report
  NOTHING that is not a directional-bias or unstated-precondition finding. Codex already
  reviews everything else, and a second general reviewer is noise.

**One parse WORKER**, `tax-parse-worker` (V6). A CLOSED grant holding nothing that reads,
writes, runs or fetches: the parent extracts through `parse_utils.py` and hands over the
masked text, so a PDF's injected instruction cannot be acted on whatever it says. By
ABSENCE, the one mechanism here that fails closed — a `PreToolUse` hook that times out
lets the call through, `permissions` rules are session-wide, and `Read` is measurably NOT
directory-confined. No replay (it forms no judgement); its acceptance is the grant
assertion plus the probe in `docs/ops/review-subagent-acceptance.md`. Hosts without a
closed sub-task tool set take the SEQUENTIAL path, banner-marked NOT enforced.

**Acceptance before trusting either reviewer:** replay a known answer, never plausibility, from a FRESH
session — definitions load at session start, so an edited one is not what a same-session replay
tests. **Both PASSED 2026-09-06**; recipe, pre-committed criteria and the traps (a squash merge
leaves nothing in `<sha>^`, so `--grep=P1` is the wrong search) are in
`docs/ops/review-subagent-acceptance.md`. Re-run before the season-open runbook.

## Feature workflow

Substantial features follow the superpowers flow: brainstorm → spec → plan → subagent-driven
implementation → review → ship; trivial markdown or JSON edits go inline. Specs and plans go
under `docs/superpowers/{specs,plans}/YYYY-MM-DD-<feature>`; precedent `tax-rollover` (#8).

## Git conventions for an assistant session

AGENTS.md gives the push command; this is the per-agent convention it defers to.

- **Never run `git push` yourself.** Print the command and let the user run it; once they
  confirm, open the PR with `gh pr create`. Never commit to `main` — the user
  squash-merges. Delete a remote branch with
  `git push origin --no-verify --delete <branch>`, the plain form being blocked.
- **Waiting on CI: bind the poll to the commit** — `gh run list --commit "$SHA"`. Three ways to
  exit on an unmeasured green: `gh pr checks` reads the PREVIOUS commit's runs; an empty check
  list is "nothing pending"; MCP `get_status` reads STATUSES, not CHECK RUNS: `total_count: 0`.
- Merging a stack: `gh pr update-branch --rebase` puts each PR on the new `main`;
  `git merge-tree --write-tree <base> <head>` dry-runs the merge for conflicts.
- `git grep -E` is POSIX ERE — `\s` and `\d` match nothing and report a clean "no
  results". Use `[[:space:]]` / `[[:digit:]]` before believing an absence.

## Reviewing a PR

**The bot review is OFF** (owner, 2026-09-07: its budget is spent). Driving a change to a
clean pass is still part of shipping it; what does it now is `/simplify` — four review
agents over the diff in parallel, one angle each, plus a fourth angle chosen for the
change (a PRIVACY pass over anything that spawns a process or writes a file) — followed by
a read of your own diff before their results land.

- **Never take a reuse recommendation on faith.** "Same code" is not "same question":
  importing `pdf_render._is_amount` into the eval comparator was recommended, plausible
  and wrong — it answers "does this print with a dollar sign", so it called `tax_year`
  money and put 2026 and 2025 within a dollar. Share the TABLE, not the rule.
- **Mutation-check every fix**: flip it off, confirm the new test goes red, restore.
- **Reproduce a finding before believing it**, and sweep the siblings — a finding's scope
  is where it was SPOTTED, not where the defect lives.

`/codex-loop` still holds the `gh` and GraphQL commands, the marker pattern and the reply
and resolution blocks, for whenever the bot comes back. Its traps are recorded there
rather than here; a second copy is a second chance to drift.
