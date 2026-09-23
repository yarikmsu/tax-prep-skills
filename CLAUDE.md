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

`.claude/settings.json` registers two `PostToolUse` hooks under `.claude/hooks/`. They
fire at TOOL time, which the git hooks cannot; neither replaces them, and the git hooks
are what cover other agents. Mechanism, exit codes and the reasoning behind each rule
below: `docs/contributing/claude-automation.md`.

- `skill-invariants.sh` (`Write|Edit|Bash`) runs only the checks the changed paths can
  turn red. That decision is `_ARMS` in `.claude/hooks/lib/hook_selection.py`, which
  the script runs and which fails CLOSED — if it cannot answer, every module runs.
  **A new source file behind an assertion ALWAYS goes in `_ARMS`** — or editing it
  selects nothing and the hook reports a silent green over an unchecked edit — and in
  `WATCHED_ROOTS` only where no entry already contains it.
- `harden-runtime-perms.sh` (`Write|Edit|NotebookEdit|Bash`) re-asserts AGENTS.md §1's
  file and directory modes. It exits 1 to surface a failure and **never the blocking 2**.

**Both stamps advance on a VERDICT, not on a run** — settle this before editing either.
pytest 0 or 1, or a chmod that RAN, moves the change boundary — red included, so a
failure is reported once. pytest 2-5, or a chmod that FAILED, holds it, or an edit
sitting on disk through the outage ends up older than the boundary and is never checked
again. Advance or hold, NEVER a third state.

**Every timing lives in one place — the `MEASURED COSTS` block in `skill-invariants.sh`.**
Everywhere else — prose, comment, docstring, assertion message alike — ALLOWED is naming the
block and quoting a RATIO from it; FORBIDDEN is restating an ABSOLUTE — a second, a test
count, a per-test rate, a host, a date — which goes stale while still reading as fresh.
Exempt is a threshold the code COMPARES against, being a budget rather than a copy, and
the one cold-pass reading a change to such a threshold records in its own PR (ruling Q13).

**The `.claude/` runtime artifacts are a three-way mirror**: the tracked `.gitignore`,
`IGNORE_PATTERNS` in `agents/install.sh`, and the `.claude/` case of the PII gate. A
test pins the first two as SETS, and the names are not re-listed here, because a
fourth copy would drift as readily as the others. Each HOOK-WRITTEN name ends
in `*`, a timed-out hook leaving the `mktemp` sibling holding the name. No rule may name
`.claude/` itself; `settings.json` and the `skills/` symlinks are tracked.

## Subagents

`.claude/agents/` holds three tracked definitions under two acceptance regimes, and
`tests/test_claude_agents.py` requires every file there to be in exactly one of them —
an agent in neither is one nothing decides how to accept. Which agent exists for what,
and what each grant is protecting: `docs/contributing/claude-automation.md`.

- **Two read-only REVIEWERS**, `tax-law-freshness-auditor` and
  `bias-direction-reviewer`. Neither is granted `Write`, `Edit` **or `Bash`** — a shell
  is a write capability, and an auditor that can quietly resolve its own findings cannot
  be trusted about them. **Point the auditor at `tax_law_export.py`'s copy, never at the
  workspace**, and give the bias reviewer a cwd outside it: `Read` is not
  directory-confined, so a scope sentence in a prompt is not a boundary.
- **One parse WORKER**, `tax-parse-worker` (V6): a CLOSED grant holding nothing that
  reads, writes, runs or fetches, the parent handing it masked text. Enforced by
  ABSENCE, the one mechanism here that fails closed — hooks fail OPEN, `permissions` is
  session-wide, and `Read` is measurably NOT directory-confined.

**Acceptance before trusting either reviewer:** replay a known answer, never
plausibility, from a FRESH session — definitions load at session start, so an edited one
is not what a same-session replay tests. **Both PASSED 2026-09-06**; the recipe,
pre-committed criteria and traps are in `docs/ops/review-subagent-acceptance.md`. Re-run
before the season-open runbook.

## Feature workflow

Substantial features follow the superpowers flow: brainstorm → spec → plan → subagent-driven
implementation → review → ship; trivial markdown or JSON edits go inline. Specs and plans go
under `docs/superpowers/{specs,plans}/YYYY-MM-DD-<feature>`; precedent `tax-rollover` (#8).

## Git conventions for an assistant session

AGENTS.md gives the push command; this is the per-agent convention it defers to.

- **Never run `git push` yourself.** Print the command and let the user run it; once they
  confirm, open the PR with `gh pr create`. Never commit to `main` — the user
  squash-merges. Delete a remote branch with `git push origin --delete <branch>`.
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
