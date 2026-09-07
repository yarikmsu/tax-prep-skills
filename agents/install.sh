#!/bin/bash
# Install tax preparation skills globally for Claude Code, at
# ~/.claude/skills/<name>/SKILL.md.
#
# Only Claude Code needs this. Codex, Cursor, Kimi Code and Copilot discover
# .agents/skills/ in the cloned repo automatically — see
# docs/contributing/agents-compat.md.
#
# Usage: bash agents/install.sh [--copy | --hooks-only]
#   --copy        Copy each SKILL.md instead of symlinking. Use on Windows clones
#                 without developer mode, where symlink creation fails.
#   --hooks-only  Install the git hooks and .gitignore entries but skip the global
#                 Claude Code skill install. Use this on Codex, Cursor, Kimi Code
#                 or Copilot: they discover .agents/skills/ in the clone directly,
#                 so a ~/.claude/skills tree would be inert.
#
# Can be run from any directory — uses script location to find source files.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"
SOURCE_DIR="$WORKSPACE_ROOT/.agents/skills"
SKILLS_DIR="$HOME/.claude/skills"

INSTALL_MODE="link"
case "${1:-}" in
    "")           ;;
    --copy)       INSTALL_MODE="copy" ;;
    --hooks-only) INSTALL_MODE="hooks-only" ;;
    *)
        echo "Error: unknown argument '$1' (expected --copy, --hooks-only, or nothing)"
        exit 1 ;;
esac

installed=()
if [ "$INSTALL_MODE" != "hooks-only" ]; then

if [ ! -d "$SOURCE_DIR" ]; then
    echo "Error: skills directory not found at $SOURCE_DIR"
    exit 1
fi

mkdir -p "$SKILLS_DIR"

# Count skills found (handle case where glob matches nothing)
shopt -s nullglob
skill_dirs=("$SOURCE_DIR"/*/)
shopt -u nullglob

if [ ${#skill_dirs[@]} -eq 0 ]; then
    echo "Error: no skill directories found in $SOURCE_DIR"
    exit 1
fi

for skill_dir in "${skill_dirs[@]}"; do
    name=$(basename "$skill_dir")   # e.g. tax-setup
    source_skill="${skill_dir%/}/SKILL.md"
    if [ ! -f "$source_skill" ]; then
        echo "  Skipped: $name (no SKILL.md)"
        continue
    fi
    # A destination that is ITSELF a symlink makes every write below resolve into
    # its target: `mkdir -p` succeeds on it silently, `ln` writes through it, and
    # the `rm -rf` guarding references/ would delete the SOURCE tree inside the
    # clone. Dropping the link removes only the link.
    if [ -L "$SKILLS_DIR/$name" ]; then
        rm -f "$SKILLS_DIR/$name"
    fi
    mkdir -p "$SKILLS_DIR/$name"
    dest="$SKILLS_DIR/$name/SKILL.md"
    if [ "$INSTALL_MODE" = "copy" ]; then
        # Remove the destination first: after a previous link-mode install it is a
        # SYMLINK, and cp writes THROUGH a symlink rather than replacing it. That
        # either fails (source and destination resolve to the same file) or, when
        # the link points at a different clone, silently overwrites that clone's
        # tracked skill file while leaving this install still a symlink.
        rm -f "$dest"
        cp "$source_skill" "$dest"
    else
        ln -sfn "$source_skill" "$dest"
    fi

    # A skill is a DIRECTORY, not a file. Under progressive disclosure SKILL.md
    # carries the gates and links the procedure out to references/*.md, so
    # installing the body ALONE leaves the agent a stub that names files it cannot
    # open — 1588 of tax-compute's 1698 lines. Nothing errors: the agent reads a
    # well-formed skill, finds no procedure at Step 1, and improvises the return.
    # The Step 12.7 tie-out gate does not catch it either, because it compares
    # parse_ledger.json against result.json and correctly-parsed documents tie out
    # against an invented computation.
    source_refs="${skill_dir%/}/references"
    dest_refs="$SKILLS_DIR/$name/references"
    # Clear first in BOTH modes. `rm -rf` on a symlink removes the link and not
    # the tree behind it, and without this `ln -sfn` fails outright against a real
    # directory left behind by an earlier --copy install.
    rm -rf "$dest_refs"
    if [ -d "$source_refs" ]; then
        if [ "$INSTALL_MODE" = "copy" ]; then
            cp -R "$source_refs" "$dest_refs"
        else
            ln -sfn "$source_refs" "$dest_refs"
        fi
        suffix="/ (SKILL.md + references/)"
    else
        suffix="/SKILL.md"
    fi

    if [ "$INSTALL_MODE" = "copy" ]; then
        echo "  Copied: $name$suffix"
    else
        echo "  Linked: $name$suffix"
    fi
    installed+=("$name")
done

if [ ${#installed[@]} -eq 0 ]; then
    echo "Error: no skill directory contained a SKILL.md"
    exit 1
fi

# Report skills that no longer exist upstream. A renamed or withdrawn skill
# otherwise stays installed and invocable, and under --copy it is
# indistinguishable from a live one — for a toolkit whose central hazard is a
# stale tax figure that still looks plausible, that is worth surfacing.
#
# REPORTED, NOT DELETED. ~/.claude/skills is shared with every other skill the
# user has, from any source, and a `tax-` prefix does not prove we installed it.
# Deleting someone's files to pre-empt a situation that has never arisen — no
# skill has ever been renamed or withdrawn — is the wrong side of that trade.
# The one unambiguous case is removed: a symlink that resolves into THIS clone's
# .agents/skills and no longer has a target is ours by construction, already
# broken, and shows up in the agent as a skill whose body cannot be read.
#
# The scan is over EVERY installed entry, not just `tax-*`. Ownership is proven
# by where the link resolves, not by the name — and .agents/skills now also holds
# contributor skills (codex-loop, state-module) that a name-based glob would skip
# while claiming to have checked. The speculative NOTE below stays narrow, which
# is where the name actually carries information.
for existing in "$SKILLS_DIR"/*; do
    [ -e "$existing" ] || [ -L "$existing" ] || continue
    ename="${existing##*/}"
    still_here=0
    for name in "${installed[@]}"; do
        [ "$name" = "$ename" ] && { still_here=1; break; }
    done
    [ "$still_here" -eq 1 ] && continue

    link="$existing/SKILL.md"
    target="$(readlink "$link" 2>/dev/null || true)"
    case "$target" in
        "$SOURCE_DIR"/*)
            if [ ! -e "$link" ]; then
                rm -rf "$existing"
                echo "  Removed: $ename (dangling link into this clone)"
                continue
            fi
            ;;
    esac
    # Past this point ownership is a GUESS, so the guess stays narrow: only a
    # `tax-` name is plausibly ours under --copy, where no link survives to prove
    # it. Warning about every unrelated skill in a shared directory would be
    # noise, and noise is what teaches people to skip the installer's output.
    case "$ename" in
        tax-*) ;;
        *) continue ;;
    esac

    echo "  NOTE: $SKILLS_DIR/$ename is installed but no longer in .agents/skills/."
    echo "        If it came from this toolkit it is stale — remove it by hand."
done

fi  # end skill install (skipped under --hooks-only)

# --- .gitignore -------------------------------------------------------------
#
# ONE list, used by BOTH the create path and the append path. They were two
# hardcoded lists and they had already drifted: the create path was missing
# eight patterns the append path adds, so a workspace that started WITHOUT a
# .gitignore got a different set of protections from one that started with one.
# The security-relevant entries are the runtime workspace directories — a
# taxpayer's documents/ and reviews/ live there — and hand-mirroring the list
# that protects them is the same mistake this repository already shipped once
# between the git hook and CI.
IGNORE_PATTERNS=(
    "taxpayer_profile.json" "tax_law_*.json" "result.json" "parse_ledger.json"
    "answers.md" "summaries/" "documents/*" "!documents/.gitkeep" "projections/"
    "withholding/" "archive/" "scenarios/" "reviews/" "FBAR_*.md" "*.pdf" "holdings.json"
    "crypto_transactions.json" "tax_calendar_*.ics" "validation_receipt.json"
    # projection_inputs.json holds YTD wages, withholding and estimated payments
    # — the mid-year input to tax-project and tax-withholding. It was in the
    # tracked .gitignore, in the PII gate's sensitive-filename case AND in the
    # permissions hook, and missing from this list alone, so a workspace whose
    # .gitignore this installer wrote was the one place it stayed visible:
    # measured on a scratch clone seeded with 29 runtime artifacts, it was the
    # only one `git add -A` staged.
    #
    # The two lists are now compared in BOTH directions, because only one of
    # them was ever self-correcting. This installer's WORKSPACE_ROOT is the
    # clone root, so running it here APPENDS anything the array has that the
    # tracked .gitignore lacks — which is why that direction has always been
    # empty and why watching it proves nothing. Nothing at all pushed an entry
    # the other way.
    "projection_inputs.json"
    # generate_tax_pdf.py is source code but a per-workspace RUNTIME artifact,
    # and the one such file whose contents the PII gate cannot vet: that gate
    # matches SSN and credential SHAPES plus sensitive filenames, and a
    # taxpayer's NAME has none of those. .planning/ holds agent handoffs, which
    # quote whatever was being worked on. Both must be here as well as in the
    # tracked .gitignore — this list is what protects a workspace that has no
    # .gitignore yet, or one older than these entries.
    "generate_tax_pdf.py" ".planning/"
    # Per-machine Claude Code state, and the one part of this list that must
    # name artifacts instead of a directory: `.claude/settings.json` carries the
    # project's PostToolUse hooks and `.claude/skills/` holds tracked relative
    # symlinks, and git never descends into an excluded directory — so a
    # `.claude/` line here would write a .gitignore that makes the repository's
    # own config uncommittable in every workspace this installer touches.
    #
    # The hook-written files carry a trailing `*` because each is built as a
    # `mktemp` sibling (`.perms-stamp.XXXXXX`) and moved over the real name. A
    # hook killed by its timeout mid-refresh leaves the sibling holding what the
    # finished file would have held, and .archive-files is a NUL-separated index
    # of every archived taxpayer document PATH — identity data that the PII scan
    # cannot recognise by shape. The exact-name form ignored only the version
    # that is rarely the one lying around.
    #
    # `*.local.md` is here as well as `*.local.json`: hookify writes its local
    # rules from conversation analysis, so they quote the work in progress. All
    # of these belong in this list as well as in the tracked .gitignore, for the
    # reason above — this list is what protects a workspace that has no
    # .gitignore yet, or one older than these entries.
    ".claude/*.local.md" ".claude/*.local.json"
    ".claude/.perms-stamp*" ".claude/.skill-invariants-stamp*"
    ".claude/.archive-dirs*" ".claude/.archive-files*"
    ".claude/.archive-index-dirty*"
    # The non-tax tail, and every entry that used to be missing from it was
    # missing for the same reason: it reads as clutter until you ask what the
    # file actually holds.
    #
    # `*.bak` and `*.tmp` are the partial-copy shapes an editor or a
    # half-finished script leaves beside the file it was rewriting, and beside
    # result.json that copy IS the return. The permissions hook already chmods
    # them 600 for exactly that reason, which is the tell that they are not
    # clutter; `*.swp`/`*.swo`/`*~` were here without them, covering one editor's
    # spelling of the idea and not the other's.
    #
    # `.env.*` and `.env.local` are here as well as `.env` because the bare name
    # matches neither of them, and `.env.local` is the form tooling actually
    # writes — the tempting reading is that `.env` is the general case and these
    # are variants of it, when in gitignore terms it is three unrelated rules.
    #
    # `Thumbs.db` is `.DS_Store`'s Windows counterpart and the worse of the two:
    # `.DS_Store` records positions and view options, while Thumbs.db is a
    # THUMBNAIL CACHE holding rendered pictures of the images beside it — a
    # scanned W-2 in a directory the root-scoped `documents/*` rule does not
    # reach. This installer's `--copy` mode exists for Windows clones, so that
    # is a supported host and not a hypothetical one.
    #
    # The IDE pair closes the list rather than being left as a documented
    # exception. `.vscode/*` must be an artifact rule for the same reason
    # `.claude/` is: git never descends into an excluded directory, so a
    # `.vscode/` line would make the tracked settings.json that points Copilot
    # at .agents/skills uncommittable and no negation could undo it. The
    # negation must FOLLOW the exclusion for git to honour it, so the two travel
    # as an adjacent pair here, in the order they are written out.
    ".venv/" ".env" ".env.*" ".env.local"
    "*.swp" "*.swo" "*~" "*.bak" "*.tmp"
    ".DS_Store" "Thumbs.db" "__pycache__/" "*.pyc"
    ".vscode/*" "!.vscode/settings.json" ".idea/"
)

GITIGNORE="$WORKSPACE_ROOT/.gitignore"
if [ ! -f "$GITIGNORE" ]; then
    printf '%s\n' "# Sensitive tax data — never commit" > "$GITIGNORE"
    printf '%s\n' "${IGNORE_PATTERNS[@]}" >> "$GITIGNORE"
    echo "  Created .gitignore (excludes sensitive tax data)"
else
    # Append only what is missing, so a user's own entries survive.
    for pattern in "${IGNORE_PATTERNS[@]}"; do
        if ! grep -qxF "$pattern" "$GITIGNORE" 2>/dev/null; then
            printf '%s\n' "$pattern" >> "$GITIGNORE"
            echo "  Added $pattern to .gitignore"
        fi
    done
fi

# Install git hooks for PII protection.
#
# Refuse to write into a DIFFERENT repository's hooks. Nothing checked that
# $WORKSPACE_ROOT was itself a repository root, so a toolkit unpacked inside an
# unrelated clone resolved that clone's hooks directory and silently replaced
# its pre-commit hook — bricking the outer project, whose commits then failed
# looking for a scanner path that does not exist there.
TOPLEVEL="$(cd "$WORKSPACE_ROOT" && git rev-parse --show-toplevel 2>/dev/null || true)"
HOOKS_INSTALLED=1
HOOKS_DIR=""
if [ -n "$TOPLEVEL" ] && [ "$(cd "$TOPLEVEL" && pwd -P)" != "$(cd "$WORKSPACE_ROOT" && pwd -P)" ]; then
    HOOKS_INSTALLED=0
    echo "  WARNING: $WORKSPACE_ROOT is inside another git repository ($TOPLEVEL)."
    echo "  Refusing to install hooks into a repository that is not this toolkit."
    echo "  Clone the toolkit as its own repository, or run 'git init' here first."
elif [ -n "$TOPLEVEL" ]; then
    # `--git-path hooks` honours core.hooksPath; `--git-common-dir` does NOT.
    # Any user running husky, lefthook, the pre-commit framework, or a corporate
    # `core.hooksPath` got hooks written to .git/hooks — a directory git then
    # never consults. The installer reported success, --hooks-only exited 0, and
    # a full SSN committed cleanly. `--git-path` also still resolves correctly
    # inside a linked worktree, which is what the previous code was written for.
    HOOKS_PATH="$(cd "$WORKSPACE_ROOT" && git rev-parse --git-path hooks 2>/dev/null || true)"
    case "$HOOKS_PATH" in
        "")  HOOKS_DIR="" ;;
        /*)  HOOKS_DIR="$HOOKS_PATH" ;;
        *)   HOOKS_DIR="$WORKSPACE_ROOT/$HOOKS_PATH" ;;
    esac

    # ...but core.hooksPath can be GLOBAL, and then it resolves to the same
    # directory for every repository on the machine. Installing there replaced
    # whatever hook governed all of them, and this toolkit's PII guard then
    # looked for scripts/pre-commit-pii-scan.sh in each OTHER repository's root,
    # did not find it, and refused every commit there — reproduced end to end.
    # Honouring core.hooksPath and OWNING it are different things.
    #
    # Containment is measured against the common git dir as well as the
    # worktree, because in a linked worktree the hooks directory legitimately
    # lives under the MAIN clone's .git and so is outside this working tree.
    # Comparing only against the worktree would refuse the very case
    # `--git-path` was adopted for.
    if [ -n "$HOOKS_DIR" ]; then
        _common="$(cd "$WORKSPACE_ROOT" && git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
        [ -n "$_common" ] || _common="$TOPLEVEL/.git"
        _common_real="$(cd "$_common" 2>/dev/null && pwd -P || printf '%s' "$_common")"
        _top_real="$(cd "$TOPLEVEL" && pwd -P)"
        # The hooks dir need not exist yet, so containment is judged on its parent.
        _hooks_real="$(cd "$(dirname "$HOOKS_DIR")" 2>/dev/null && pwd -P || true)"
        _inside=0
        for _base in "$_top_real" "$_common_real"; do
            [ -n "$_hooks_real" ] || continue
            if [ "$_hooks_real" = "$_base" ] || \
               [ "${_hooks_real#"$_base"/}" != "$_hooks_real" ]; then
                _inside=1
            fi
        done
        if [ "$_inside" -eq 0 ]; then
            HOOKS_INSTALLED=0
            HOOKS_DIR=""
            echo "  WARNING: core.hooksPath resolves outside this repository:"
            echo "    $HOOKS_PATH"
            echo "  That directory is shared with every repository using this setting."
            echo "  Refusing to replace hooks that govern other projects."
            echo "  Install the guard by hand, or unset core.hooksPath for this clone:"
            echo "    git -C \"$WORKSPACE_ROOT\" config --unset core.hooksPath"
        fi
    fi
fi
if [ -n "$HOOKS_DIR" ]; then
    mkdir -p "$HOOKS_DIR"
fi

# Never destroy a hook this installer did not write. Another hook manager owning
# pre-commit is a normal setup, and silently clobbering it removes a protection
# the user chose. The marker is what distinguishes our own earlier install (safe
# to replace) from someone else's hook (must be preserved).
HOOK_MARKER="# tax-prep-skills-managed-hook"
for _hook in pre-commit pre-push; do
    [ -n "$HOOKS_DIR" ] || break
    _path="$HOOKS_DIR/$_hook"
    # `-e` alone is FALSE for a DANGLING symlink, so no backup was taken and the
    # `cat >` below then FOLLOWED the link, creating the hook at its target —
    # outside the hooks directory, where git never runs it. The installer
    # reported success and nothing guarded commits. `-L` sees the link itself.
    if [ -e "$_path" ] || [ -L "$_path" ]; then
        if ! grep -qF "$HOOK_MARKER" "$_path" 2>/dev/null; then
            mv "$_path" "$_path.pre-tax-prep-skills.bak"
            echo "  Backed up your existing $_hook hook to $_hook.pre-tax-prep-skills.bak"
            echo "  It no longer runs — chain it from the new hook if you still need it."
        elif [ -L "$_path" ]; then
            # Our own marker, but reached through a symlink: writing would still
            # follow it out of the hooks directory. Replace it with a real file.
            rm -f "$_path"
        fi
    fi
done

if [ -n "$HOOKS_DIR" ] && [ -d "$HOOKS_DIR" ]; then
    cat > "$HOOKS_DIR/pre-commit" << 'HOOK_EOF'
#!/bin/bash
# tax-prep-skills-managed-hook
# Git pre-commit hook — blocks commits to main and runs PII guard.

# Block direct commits to main/master
BRANCH=$(git branch --show-current 2>/dev/null)
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
    echo ""
    echo "=========================================="
    echo "  COMMIT BLOCKED — Direct commit to $BRANCH"
    echo "=========================================="
    echo ""
    echo "  All changes must go through feature branches and PRs."
    echo "  Create a branch first:"
    echo ""
    echo "    git checkout -b feature/<name>"
    echo ""
    echo "  If this is intentional, use: git commit --no-verify"
    echo ""
    exit 1
fi

# Run PII guard.
#
# Resolve the root of the tree BEING COMMITTED, not the directory the hook file
# lives in. Hooks are shared across worktrees (they live in the common .git dir),
# so deriving the root from the hook's own path always lands on the primary
# checkout — which may sit on a different revision, or be sparse. The guard would
# then scan the wrong tree, or not be found at all and let the commit through
# unscanned. --show-toplevel is evaluated at commit time in the committing tree.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ]; then
    echo "COMMIT BLOCKED: cannot resolve the repository root, so the PII scan"
    echo "cannot run. Refusing to commit unscanned."
    exit 1
fi
if [ -x "$REPO_ROOT/scripts/pre-commit-pii-scan.sh" ]; then
    "$REPO_ROOT/scripts/pre-commit-pii-scan.sh"
else
    echo "COMMIT BLOCKED: PII scan script not found at"
    echo "  $REPO_ROOT/scripts/pre-commit-pii-scan.sh"
    echo "Refusing to commit unscanned. Run 'bash agents/install.sh' to fix,"
    echo "or 'git commit --no-verify' if you have verified the diff by hand."
    exit 1
fi
HOOK_EOF
    chmod +x "$HOOKS_DIR/pre-commit"
    echo "  Installed pre-commit hook (branch guard + PII guard)"

    cat > "$HOOKS_DIR/pre-push" << 'HOOK_EOF'
#!/bin/bash
# tax-prep-skills-managed-hook
# Git pre-push hook — blocks automated pushes.
echo ""
echo "=========================================="
echo "  PUSH BLOCKED"
echo "=========================================="
echo ""
echo "  Push blocked by pre-push hook."
echo "  To push after reviewing your commit:"
echo ""
BRANCH=$(git branch --show-current 2>/dev/null || echo "HEAD")
echo "    git push --no-verify -u origin $BRANCH"
echo ""
exit 1
HOOK_EOF
    chmod +x "$HOOKS_DIR/pre-push"
    echo "  Installed pre-push hook (push blocker)"
else
    HOOKS_INSTALLED=0
    echo "  WARNING: could not locate a git hooks directory for $WORKSPACE_ROOT"
    echo "  Git hooks were NOT installed. PII protection is not active."
    echo "  Run this from inside a git clone of the repository and re-run install.sh."
fi

echo ""
if [ "$INSTALL_MODE" = "hooks-only" ]; then
    echo "Git hooks installed. Skills were NOT copied to $SKILLS_DIR —"
    echo "Codex, Cursor, Kimi Code and Copilot read .agents/skills/ in this clone."
    echo "Confirm discovery with your agent's skill list (Codex: /skills)."
else
    # "skills", not "tax skills": .agents/skills also holds the contributor
    # skills, and a count labelled with a prefix that does not describe all of
    # them is a small lie the reader has no way to check.
    echo "Installed ${#installed[@]} skills to $SKILLS_DIR"
    # Generated from what was actually installed — a hardcoded list silently goes
    # stale every time a skill is added.
    printf 'Available commands: '
    printf '/%s ' "${installed[@]}"
    printf '\n'
    echo ""
    echo "Other agents (Codex / Cursor / Kimi Code / Copilot) do not need this —"
    echo "they discover .agents/skills/ in this repo automatically. Next time,"
    echo "bash agents/install.sh --hooks-only installs only the git hooks."
fi
echo "Per-agent sharp edges: docs/contributing/agents-compat.md"

# EVERY mode, not just --hooks-only. The hooks are the security setup step
# whichever mode installs them, and the previous guard covered only one of the
# three — so `bash agents/install.sh` (the form README's Quick Start, Upgrading
# and Contributing sections all use) printed "Installed 20 tax skills" and
# exited 0 with nothing guarding commits. This check runs last so the user still
# sees what DID get installed before being told what did not.
if [ "$HOOKS_INSTALLED" -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "  Git hooks were NOT installed"
    echo "=========================================="
    echo "Nothing is guarding commits in this working tree: no branch guard and"
    echo "no PII scan. See the warning above for why. Do not put real taxpayer"
    echo "documents here until this is resolved."
    exit 1
fi
