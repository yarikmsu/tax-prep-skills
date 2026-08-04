#!/bin/bash
# Install tax preparation skills globally for Claude Code, at
# ~/.claude/skills/<name>/SKILL.md.
#
# Only Claude Code needs this. Codex, Cursor, Kimi Code and Copilot discover
# .agents/skills/ in the cloned repo automatically — see
# docs/contributing/agents-compat.md.
#
# Usage: bash agents/install.sh [--copy]
#   --copy  Copy each SKILL.md instead of symlinking. Use on Windows clones
#           without developer mode, where symlink creation fails.
#
# Can be run from any directory — uses script location to find source files.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"
SOURCE_DIR="$WORKSPACE_ROOT/.agents/skills"
SKILLS_DIR="$HOME/.claude/skills"

INSTALL_MODE="link"
if [ "${1:-}" = "--copy" ]; then
    INSTALL_MODE="copy"
elif [ -n "${1:-}" ]; then
    echo "Error: unknown argument '$1' (expected --copy or nothing)"
    exit 1
fi

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

installed=()
for skill_dir in "${skill_dirs[@]}"; do
    name=$(basename "$skill_dir")   # e.g. tax-setup
    source_skill="${skill_dir%/}/SKILL.md"
    if [ ! -f "$source_skill" ]; then
        echo "  Skipped: $name (no SKILL.md)"
        continue
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
        echo "  Copied: $name/SKILL.md"
    else
        ln -sfn "$source_skill" "$dest"
        echo "  Linked: $name/SKILL.md"
    fi
    installed+=("$name")
done

if [ ${#installed[@]} -eq 0 ]; then
    echo "Error: no skill directory contained a SKILL.md"
    exit 1
fi

# Create .gitignore for sensitive files if it doesn't exist
GITIGNORE="$WORKSPACE_ROOT/.gitignore"
if [ ! -f "$GITIGNORE" ]; then
    cat > "$GITIGNORE" << 'EOF'
# Sensitive tax data — never commit
taxpayer_profile.json
tax_law_*.json
result.json
parse_ledger.json
answers.md
summaries/
documents/*
!documents/.gitkeep
FBAR_*.md
*.pdf
scenarios/
reviews/
holdings.json
crypto_transactions.json
tax_calendar_*.ics
EOF
    echo "  Created .gitignore (excludes sensitive tax data)"
else
    # Append sensitive patterns not already present
    for pattern in "taxpayer_profile.json" "tax_law_*.json" "result.json" "answers.md" "parse_ledger.json" "summaries/" "documents/*" "!documents/.gitkeep" "FBAR_*.md" "*.pdf" "scenarios/" "reviews/" "holdings.json" "crypto_transactions.json" "tax_calendar_*.ics" ".venv/" ".env" "*.swp" "*.swo" "*~" ".DS_Store" "__pycache__/" "*.pyc"; do
        if ! grep -qxF "$pattern" "$GITIGNORE" 2>/dev/null; then
            echo "$pattern" >> "$GITIGNORE"
            echo "  Added $pattern to .gitignore"
        fi
    done
fi

# Install git hooks for PII protection.
#
# Resolve the REAL hooks directory rather than assuming $WORKSPACE_ROOT/.git is one:
# inside a git worktree .git is a FILE pointing elsewhere, so the naive path does not
# exist and the PII guard would silently never install — leaving the last line of
# defense absent exactly where agents most often work.
GIT_COMMON_DIR="$(cd "$WORKSPACE_ROOT" && git rev-parse --git-common-dir 2>/dev/null || true)"
case "$GIT_COMMON_DIR" in
    "")  HOOKS_DIR="" ;;
    /*)  HOOKS_DIR="$GIT_COMMON_DIR/hooks" ;;
    *)   HOOKS_DIR="$WORKSPACE_ROOT/$GIT_COMMON_DIR/hooks" ;;
esac
if [ -n "$HOOKS_DIR" ]; then
    mkdir -p "$HOOKS_DIR"
fi
if [ -n "$HOOKS_DIR" ] && [ -d "$HOOKS_DIR" ]; then
    cat > "$HOOKS_DIR/pre-commit" << 'HOOK_EOF'
#!/bin/bash
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
    echo "  WARNING: could not locate a git hooks directory for $WORKSPACE_ROOT"
    echo "  Git hooks were NOT installed. PII protection is not active."
    echo "  Run this from inside a git clone of the repository and re-run install.sh."
fi

echo ""
echo "Installed ${#installed[@]} tax skills to $SKILLS_DIR"
# Generated from what was actually installed — a hardcoded list silently goes
# stale every time a skill is added.
printf 'Available commands: '
printf '/%s ' "${installed[@]}"
printf '\n'
echo ""
echo "Other agents (Codex / Cursor / Kimi Code / Copilot): no install needed —"
echo "they discover .agents/skills/ in this repo automatically."
echo "Per-agent sharp edges: docs/contributing/agents-compat.md"
