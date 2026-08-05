@AGENTS.md

# CLAUDE.md

Project truth for every agent lives in `AGENTS.md`, imported above. This file adds
only what is specific to working in this repository through Claude Code. Nothing
here restates or overrides AGENTS.md — some agents read both files together, so
the two must never disagree.

## Skills

`bash agents/install.sh` symlinks each `.agents/skills/<name>/SKILL.md` — and its
`references/` directory, where the skill has one — into `~/.claude/skills/<name>/`,
then installs the gitignore entries and git hooks. Add `--copy` on a clone where
symlink creation is unavailable.

Re-run it after pulling a change that adds or splits a `references/` file: an
install predating progressive disclosure has only the body, which loads as a
complete skill while every step it delegates is missing.

The tracked relative symlinks in `.claude/skills/` already provide project-local
discovery, so the global install is only needed to use the skills from other
directories.

## Feature workflow

Substantial features follow the superpowers flow: brainstorm → spec → plan →
subagent-driven implementation → review → ship. Trivial markdown or JSON edits go
inline.

- Specs: `docs/superpowers/specs/YYYY-MM-DD-<feature>-design.md`
- Plans: `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`
- Precedent for spec + plan structure: the `tax-rollover` pair (PR #8).

## Git conventions for an assistant session

AGENTS.md gives the push command that works past the pre-push hook. This is the
per-agent convention it defers to:

- **Never run `git push` yourself.** Print the exact command and let the user run it.
- After the user confirms the push, open the PR with `gh pr create`.
- Never commit to `main` — the user performs the squash merge.
- Delete a remote branch with
  `git push origin --no-verify --delete <branch>` (the pre-push hook blocks the
  plain form).

## Automated PR review loop

Pull requests are reviewed by the Codex bot, and driving that loop to a clean
pass is part of shipping a change.

- The review triggers on PR open, ready-for-review, or an `@codex review` issue
  comment. A plain `git push` does **not** trigger one — post the comment.
- Findings arrive as inline review comments tagged P1/P2. A clean pass arrives as
  a top-level issue comment ("Didn't find any major issues"), **not** as a formal
  review — poll issue comments as well as reviews and inline comments.
- Filter results by `created_at` against the trigger time, not by commit SHA:
  GitHub re-anchors un-outdated inline comments to the current PR HEAD, so a
  stale finding can masquerade as a fresh one.
- **A clean pass only ends the loop if it reviewed an unchanged HEAD.** A pass on
  a commit that later gained commits was scoped to that older diff.
- Resolve threads with the GraphQL `resolveReviewThread` mutation — there is no
  REST endpoint. Find unanswered threads by querying `reviewThreads` and
  filtering out IDs already present in some comment's `in_reply_to_id`.
- Self-review against the review's own lenses before re-triggering; it converges
  in far fewer rounds than round-tripping every finding through the bot.
