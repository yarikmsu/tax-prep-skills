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
    "eval_run.json"
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
    # Coverage data. A taxpayer workspace never produces these — they are a
    # contributor's — but this array and the tracked .gitignore are pinned as
    # equal SETS, because nine entries once lived in one and neither installer
    # path. An inert entry in a workspace costs nothing; a set that is allowed
    # to differ costs the check.
    ".coverage" ".coverage.*" "coverage.xml" "htmlcov/"
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
# Git pre-push hook — scans every OUTGOING commit for PII, and passes a clean one.
#
# This hook used to refuse EVERY push unconditionally, which made
# `git push --no-verify` the documented working form. `--no-verify` disables
# the pre-push hook and nothing else, so the habit cost nothing here — it cost
# something one layer up. That is the same flag that disables the pre-commit
# PII gate, and typing it by reflex on a repository whose one absolute rule is
# that a full SSN never leaves the machine is training, not protection. A gate
# that has to be bypassed to do the ordinary thing gets bypassed everywhere.
#
# So the hook now does the work the blanket refusal only gestured at: every
# commit this push would PUBLISH is scanned by the same script the pre-commit
# hook and CI run, and a clean branch pushes normally.
#
# The destination guard is kept deliberately. The pre-commit hook blocks
# COMMITTING to main and nothing else blocked PUSHING there, so dropping the
# blanket refusal without replacing this would have removed a protection while
# claiming only to remove friction.
#
# WHAT IS TRADED, stated rather than denied: the old hook forced a human to
# look before anything left the machine, and this one decides automatically for
# the classes the scanner recognises. Content it cannot recognise still goes —
# a taxpayer's NAME in a section header has neither an SSN shape nor a
# credential shape, and so does a COMMIT MESSAGE, which the scanner reads at
# none of its call sites. The trade is deliberate. A gate that fires on every
# push is one people learn to wave through, and it was being waved through with
# the flag that also turns off the commit-time scan.
#
# Recovering the outgoing range has several edges — a ref deletion, a root
# commit, a shallow boundary, a ref that publishes no commit at all, and a ref
# whose target is not a commit. What each one is and why it is handled the way
# it is: docs/contributing/pii-gate.md.
set -u

# A `git replace` mapping rewrites what every command here READS while the pack
# sent to the remote carries the original objects, so the scan would inspect one
# tree and the push would publish another. Set for this whole process, so the
# scanner inherits it too.
export GIT_NO_REPLACE_OBJECTS=1

REMOTE_NAME="${1:-origin}"
# $2 is where git is ACTUALLY pushing, which is not always where $1 fetches
# from: `remote.<name>.pushurl` and an `insteadOf` rewrite both separate them.
REMOTE_LOCATION="${2:-$REMOTE_NAME}"
NL=$'\n'

block() {
    echo ""
    echo "=========================================="
    echo "  PUSH BLOCKED"
    echo "=========================================="
    echo ""
    echo "  $1"
    echo ""
    echo "  Emergency bypass: git push --no-verify"
    echo ""
    exit 1
}

# Resolved at push time in the tree being pushed, for the reason the pre-commit
# hook states: hooks live in the common git dir and are shared across worktrees,
# so the hook's own path always names the primary checkout.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || block "Cannot resolve the repository root, so the PII scan cannot run.
  Refusing to push unscanned."
SCAN="$REPO_ROOT/scripts/pre-commit-pii-scan.sh"
[ -x "$SCAN" ] || block "PII scan script not found or not executable at
    $SCAN
  Refusing to push unscanned. Run 'bash agents/install.sh' to fix."

# WHAT THE DESTINATION ALREADY HAS — asked of the destination, not remembered.
#
# Remote-tracking refs were the bound here and they are a local CACHE of what
# some remote held when it was last fetched. That is a different question from
# "what does this push's destination have", and every way the two come apart
# ends the same: the hook decides a commit is already there, finds nothing
# outgoing, and skips the ref without scanning it. A `pushurl` pointing
# somewhere else entirely, a remote that rewrote or deleted the history since
# the last fetch, a push by URL with no tracking refs at all — all of them.
#
# Lazy, so a push that needs no range (every ref an update, git having supplied
# the base) costs no round trip.
REMOTE_HAS=""
REMOTE_HAS_READY=0
load_remote_has() {
    [ "$REMOTE_HAS_READY" -eq 0 ] || return 0
    local out line sha
    # `< /dev/null` here is CONSISTENCY, not a fix. It was added believing that
    # `ls-remote` over SSH would drain the ref protocol the way a bare `ssh` in
    # a `while read` loop does; git connects its own pipes to a transport
    # helper, so the helper never sees this stdin and no such drain was
    # observed. The redirect stays because the scanner below genuinely needs
    # one and a reader should not have to work out why only one call has it.
    out=$(git ls-remote --heads --tags "$REMOTE_LOCATION" 2>/dev/null < /dev/null) \
        || block "Cannot ask '$REMOTE_LOCATION' which commits it already has, so the
  range this push would publish cannot be established.
  Refusing to push unscanned."
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        sha="${line%%	*}"
        # Only what this clone can traverse. A sha the destination has and this
        # clone does not cannot bound anything, and leaving it out can only
        # widen the scan.
        git cat-file -e "${sha}^{commit}" 2>/dev/null || continue
        REMOTE_HAS="$REMOTE_HAS$sha$NL"
    done <<< "$out"
    REMOTE_HAS_READY=1
}

# git's pre-push protocol, one line per ref:
#   <local ref> <local sha> <remote ref> <remote sha>
while read -r local_ref local_sha remote_ref remote_sha; do
    # FIRST, before the deletion skip: deleting a branch is a write to that
    # branch, and the guard is about the destination, not the payload.
    case "$remote_ref" in
        refs/heads/main|refs/heads/master)
            block "Direct push to ${remote_ref#refs/heads/}.
  Changes go through a feature branch and a pull request; the squash merge is
  the only thing that writes to that branch."
            ;;
    esac

    # An all-zero LOCAL sha is a ref DELETION, which publishes no content.
    case "$local_sha" in
        *[!0]*) ;;
        *) continue ;;
    esac

    # A REF NAME is published as-is and is not file content, so nothing
    # downstream masks it — the pathname rule's argument exactly. The rule
    # itself stays in the scanner; this hands it the name.
    #
    # THE REMOTE REF ONLY. The local one was passed too and it is not published,
    # so it bought nothing — and it cost: `git push <sha>:refs/heads/x` makes
    # the local ref the SHA, and about one 40-hex sha in eight contains a
    # maximal run of exactly nine digits, which is the bare-SSN rule. A gate
    # that refuses a correct push at random is the cry-wolf shape this
    # repository has already paid for once, in the same rule, over the digest
    # runs inside a hashed lock.
    names_out=$(printf '%s\n' "$remote_ref" \
                | PII_SCAN_CONTEXT=PUSH "$SCAN" --names "$local_sha" 2>&1) || {
        printf '%s\n' "$names_out"
        exit 1
    }
    printf '%s\n' "$names_out"
    # Exit 0 is not evidence here either, and this mode has its own way of
    # producing one: given nothing on stdin it reports zero names checked and
    # succeeds. One name went in, so one must come back.
    case "$NL$names_out$NL" in
        *"${NL}PII scan clean — 1 name(s) checked.${NL}"*) ;;
        # WITHOUT NAMING THE REF. Every message after this point may print it,
        # because by then it has passed these very patterns — but here it has
        # not, and the name is the candidate value. Printing it performs the
        # disclosure the check exists to prevent, into scrollback, which is the
        # detected-but-not-redacted shape this gate has paid for once already.
        # The scanner's own refusal prints a redacted form; this arm is for the
        # case where it printed nothing at all.
        *) block "The name check reported nothing about the ref being pushed, so nothing
  here knows it looked at it. The name is not repeated here: it has not been
  checked, and it is the value in question." ;;
    esac

    # WHAT THE REF POINTS AT. `git rev-list <blob>` succeeds with EMPTY output,
    # so a tag on a blob or a tree read as "publishes no commit" and went
    # through carrying its whole content, unscanned.
    target_type=$(git cat-file -t "${local_sha}^{}" 2>/dev/null || true)
    [ "$target_type" = "commit" ] || block "'$local_ref' points at a ${target_type:-unreadable object}, not a commit.
  Nothing here can scan that, and the scan is not optional."

    newset=""
    case "$remote_sha" in
        # The destination has this ref, and git has just told us where it is.
        *[!0]*) base="$remote_sha" ;;
        *)
            # A branch the destination does not have yet — the ordinary case in
            # this repository, since every change is a new feature branch.
            load_remote_has
            revargs=("$local_sha")
            for _sha in $REMOTE_HAS; do revargs+=("^$_sha"); done
            newset=$(git rev-list "${revargs[@]}" 2>/dev/null) \
                || block "Cannot enumerate the commits '$local_ref' would publish.
  Refusing to push unscanned."
            if [ -z "$newset" ]; then
                echo "PII scan: '$local_ref' publishes no commit '$REMOTE_LOCATION' lacks."
                continue
            fi
            # `--topo-order --reverse` so the first line is a genuine ANCESTOR.
            # Default order is reverse CHRONOLOGICAL, which a skewed committer
            # date reorders: taking the last line then picked a descendant, and
            # its parent — an outgoing commit — became the base and was excluded.
            oldest=""
            while IFS= read -r _line; do
                oldest="$_line"
                break
            done <<< "$(git rev-list --topo-order --reverse "${revargs[@]}" 2>/dev/null)"
            [ -n "$oldest" ] || block "Cannot order the commits '$local_ref' would publish.
  Refusing to push unscanned."
            # The commit OBJECT is the authority on the parent, and it is the
            # only thing that can be: `git rev-list --parents` and `git rev-parse
            # <sha>^` report the parents THIS CLONE HAS, which makes a shallow
            # boundary indistinguishable from a root. Only the HEADER block, and
            # in pure bash, because `grep '^parent '` over the whole object
            # false-matches a commit MESSAGE line beginning "parent " and a grep
            # that cannot RUN reports "no match" — the answer that skips a check.
            obj=$(git cat-file commit "$oldest" 2>/dev/null) \
                || block "Cannot read the commit object for '$oldest'.
  Refusing to push unscanned."
            recorded=""
            while IFS= read -r line; do
                [ -n "$line" ] || break
                case "$line" in "parent "*) recorded="${line#parent }"; break ;; esac
            done <<< "$obj"
            if [ -z "$recorded" ]; then
                # A true root. The scan re-includes a root base of its own
                # accord and diffs it against the empty tree.
                base="$oldest"
            elif git rev-parse -q --verify "${recorded}^{commit}" > /dev/null 2>&1; then
                base="$recorded"
            else
                block "Commit '$oldest' records a parent this clone does not have.
  A shallow history cannot be scanned commit by commit, and the range this hook
  would build silently omits its oldest commit. Fetch the full history
  (git fetch --unshallow) and push again."
            fi
            ;;
    esac

    outgoing=$(git rev-list "$base..$local_sha" 2>/dev/null) \
        || block "Cannot enumerate '$base..$local_sha'. Refusing to push unscanned."
    # The scan re-includes a ROOT base, so the range it covers is one larger.
    if [ -n "$newset" ] && [ "$base" = "$oldest" ]; then
        outgoing="$oldest${outgoing:+$NL}$outgoing"
    fi
    if [ -z "$outgoing" ]; then
        # A force REWIND to an ancestor lands here: the ref moves and publishes
        # nothing. Blocking it was cry-wolf on a correct push.
        echo "PII scan: '$local_ref' publishes no new commit."
        continue
    fi

    # THE RANGE MUST COVER WHAT WAS ENUMERATED. A base is a single commit and
    # the outgoing set need not be a single chain, so this is checked rather
    # than reasoned about — it is the one assertion that does not depend on
    # having got the traversal order right.
    for _sha in $newset; do
        case "$NL$outgoing$NL" in
            *"$NL$_sha$NL"*) ;;
            *) block "The range '$base..$local_sha' does not cover commit $_sha, which
  this push would publish. Refusing to scan a range that is not the push." ;;
        esac
    done

    expected=0
    for _sha in $outgoing; do expected=$((expected + 1)); done

    # Every commit is inspected separately, so a first push to a destination
    # holding none of them is a long wait with nothing on screen — which is how
    # `--no-verify` gets back into somebody's muscle memory. A count the code
    # COMPARES against is a budget, not a measurement.
    if [ "$expected" -gt 25 ]; then
        echo "PII scan: '$remote_ref' publishes $expected commits, each inspected"
        echo "separately. This will take a moment."
    fi

    # `< /dev/null`: the scanner would otherwise inherit the REMAINING ref lines
    # as its stdin, and anything that consumed them — the scanner itself, or a
    # tool it calls — would make this loop's next `read` see EOF. The refs after
    # the first would then be published with neither a scan nor the guard above.
    #
    # Exit 0 is not evidence either. The scanner is read from the WORKING TREE,
    # so what runs is whatever is on disk: a half-finished local edit, or a
    # rewrite this very push would publish. The verdict is its own coverage
    # line, matched WHOLE and against the count enumerated here — a substring
    # test passed a stub whose output was "No PII scan clean result is available".
    out=$(PII_SCAN_CONTEXT=PUSH "$SCAN" "$base...$local_sha" 2>&1 < /dev/null) || {
        printf '%s\n' "$out"
        exit 1
    }
    printf '%s\n' "$out"
    # The count is read as a NUMBER and compared with `-ge`, not matched as the
    # exact figure. The scan derives its own count from `merge-base(base,head)`
    # and this one comes from `base..head`; those agree whenever the base is an
    # ancestor, which is every ordinary push, and a force push onto a history
    # with more than one merge base makes the scan enumerate MORE. Demanding
    # equality there refuses a correct push, and the property actually wanted is
    # that the scan covered at least what was enumerated here.
    reported=""
    while IFS= read -r _line; do
        case "$_line" in
            "PII scan clean — "*" commit(s) inspected individually,")
                reported="${_line#PII scan clean — }"
                reported="${reported% commit(s) inspected individually,}"
                ;;
        esac
    done <<< "$out"
    case "${reported:-x}" in
        ''|*[!0-9]*)
            block "The PII scan exited 0 without reporting what it inspected, so nothing
  here knows it ran. It is read from the working tree at
    $SCAN
  Check that file against git before pushing." ;;
    esac
    [ "$reported" -ge "$expected" ] || block "The PII scan reported inspecting $reported commit(s); this push publishes
  $expected. Refusing to treat a partial scan as a clean one."
done

exit 0
HOOK_EOF
    chmod +x "$HOOKS_DIR/pre-push"
    echo "  Installed pre-push hook (PII scan + branch guard)"
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
