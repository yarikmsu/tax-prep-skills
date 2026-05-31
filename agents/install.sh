#!/bin/bash
# Install tax preparation skills to ~/.claude/skills/<name>/SKILL.md
# Can be run from any directory — uses script location to find source files.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/skills"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"
SKILLS_DIR="$HOME/.claude/skills"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "Error: skills directory not found at $SOURCE_DIR"
    exit 1
fi

mkdir -p "$SKILLS_DIR"

# Count skills found (handle case where glob matches nothing)
shopt -s nullglob
skill_files=("$SOURCE_DIR"/tax-*.md)
shopt -u nullglob

if [ ${#skill_files[@]} -eq 0 ]; then
    echo "Error: no tax-*.md skill files found in $SOURCE_DIR"
    exit 1
fi

for skill in "${skill_files[@]}"; do
    name=$(basename "$skill" .md)   # e.g. tax-setup
    mkdir -p "$SKILLS_DIR/$name"
    ln -sf "$skill" "$SKILLS_DIR/$name/SKILL.md"
    echo "  Linked: $name/SKILL.md"
done

# Create .gitignore for sensitive files if it doesn't exist
GITIGNORE="$WORKSPACE_ROOT/.gitignore"
if [ ! -f "$GITIGNORE" ]; then
    cat > "$GITIGNORE" << 'EOF'
# Sensitive tax data — never commit
taxpayer_profile.json
tax_law_*.json
result.json
answers.md
summaries/
documents/*
!documents/.gitkeep
FBAR_*.md
*.pdf
scenarios/
holdings.json
tax_calendar_*.ics
EOF
    echo "  Created .gitignore (excludes sensitive tax data)"
else
    # Append sensitive patterns not already present
    for pattern in "taxpayer_profile.json" "tax_law_*.json" "result.json" "answers.md" "summaries/" "documents/*" "!documents/.gitkeep" "FBAR_*.md" "*.pdf" "scenarios/" "holdings.json" "tax_calendar_*.ics" ".venv/" ".env" "*.swp" "*.swo" "*~" ".DS_Store" "__pycache__/" "*.pyc"; do
        if ! grep -qxF "$pattern" "$GITIGNORE" 2>/dev/null; then
            echo "$pattern" >> "$GITIGNORE"
            echo "  Added $pattern to .gitignore"
        fi
    done
fi

# Install git hooks for PII protection
HOOKS_DIR="$WORKSPACE_ROOT/.git/hooks"
if [ -d "$HOOKS_DIR" ]; then
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

# Run PII guard
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [ -x "$REPO_ROOT/scripts/pre-commit-pii-scan.sh" ]; then
    "$REPO_ROOT/scripts/pre-commit-pii-scan.sh"
else
    echo "WARNING: PII scan script not found at $REPO_ROOT/scripts/pre-commit-pii-scan.sh"
    echo "Commit is proceeding WITHOUT PII protection."
    echo "Run 'bash agents/install.sh' to fix."
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
    echo "  WARNING: .git/hooks directory not found at $HOOKS_DIR"
    echo "  Git hooks were NOT installed. PII protection is not active."
    echo "  Ensure this is a git repository and re-run install.sh."
fi

echo ""
echo "Installed ${#skill_files[@]} tax skills to $SKILLS_DIR"
echo "Available commands: /tax-setup, /tax-parse, /tax-compute, /tax-generate, /tax-verify, /tax-review, /tax-fbar, /tax-project, /tax-withholding, /tax-scenario, /tax-roth-convert, /tax-harvest, /tax-calendar, /tax-retirement, /tax-ptet"
