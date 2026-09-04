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
  run. It then runs only the CHECKS the changed paths can turn red — six test modules, or
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

## Review subagents

`.claude/agents/` holds two tracked, read-only reviewers. Neither is granted `Write`,
`Edit` **or `Bash`** — a shell is a write capability, and an auditor that can quietly
resolve its own findings cannot be trusted about them.

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

**Acceptance before trusting either:** replay a known answer, never plausibility, from a FRESH
session — definitions load at session start, so an edited one is not what a same-session replay
tests. **Both PASSED 2026-08-27**; recipe, pre-committed criteria and the traps (a squash merge
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
- **Waiting on CI: bind the poll to the commit** — `gh run list --commit "$SHA"`.
  `gh pr checks` may still report the *previous* commit's finished runs, and an empty
  check list trivially satisfies "nothing pending" — both exit on an unmeasured green.
- Merging a stack: `gh pr update-branch --rebase` puts each PR on the new `main`;
  `git merge-tree --write-tree <base> <head>` dry-runs the merge for conflicts.
- `git grep -E` is POSIX ERE — `\s` and `\d` match nothing and report a clean "no
  results". Use `[[:space:]]` / `[[:digit:]]` before believing an absence.

## Automated PR review loop

Codex reviews every PR; driving that loop to a clean pass is part of shipping a change.

- The review triggers on PR open, ready-for-review, or an `@codex review` issue comment. A
  plain `git push` does **not** trigger one — post the comment.
- Findings arrive as inline P1/P2 comments; a clean pass arrives as a top-level issue
  comment ("Didn't find any major issues", matched anchored to the body's start through
  markdown markup and the bot's optional product label), **not** a review — poll all
  three. **An unrecognised marker fails CLOSED**: nothing parses, the reviewed commit
  reads empty, and the loop re-triggers for ever on a PR that already passed. Widen the
  markup class in the skill; never read empty as a pass, a failed fetch being empty too.
- Filter results by `created_at` against the trigger time, not by commit SHA: GitHub
  re-anchors un-outdated inline comments to the current PR HEAD, so a stale finding can
  masquerade as a fresh one.
- **A clean pass only ends the loop if it reviewed an unchanged HEAD.** A pass on a commit
  that later gained commits was scoped to that older diff.
- **A reply is per-finding, not per-round.** A refutation replied to as a fix claims
  publicly that code changed when none did, and the thread is resolved on that claim (PR
  #23 round 17 was all refutations). Record the class with the finding, compose the body
  from it, and verify the reply that LANDED is of that class.
- The round state under `.git/` is APPENDED and re-sourced, so it is last-write-wins: a
  verdict downgraded in shell memory only is worse than lost, the earlier affirmative
  still being the last line and so what the next shell reads.
- Resolve threads with the GraphQL `resolveReviewThread` mutation — there is no REST
  endpoint. Find unanswered threads by querying `reviewThreads` and filtering out IDs
  already in some comment's `in_reply_to_id`.

`/codex-loop` holds the `gh` and GraphQL commands — self-review first, and the reply and
resolution blocks in its two `references/` files. The exact marker pattern and reply
openings live THERE and are deliberately not repeated here; a third copy is a third chance
to drift. The bullets stay because they are the traps, and a trap is only useful loaded.
