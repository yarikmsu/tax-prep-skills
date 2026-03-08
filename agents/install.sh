#!/bin/bash
# Install tax preparation skills to .claude/skills/
# Can be run from any directory — uses script location to find source files.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/skills"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"
SKILLS_DIR="$WORKSPACE_ROOT/.claude/skills"

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
    name=$(basename "$skill")
    ln -sf "$skill" "$SKILLS_DIR/$name"
    echo "  Linked: $name"
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
FBAR_*.md
*.pdf
EOF
    echo "  Created .gitignore (excludes sensitive tax data)"
else
    # Append sensitive patterns not already present
    for pattern in "taxpayer_profile.json" "tax_law_*.json" "result.json" "answers.md" "summaries/" "FBAR_*.md" "*.pdf" ".venv/" ".env" "*.swp" "*.swo" "*~" ".DS_Store" "__pycache__/" "*.pyc"; do
        if ! grep -qF "$pattern" "$GITIGNORE" 2>/dev/null; then
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
# Git pre-commit hook — runs PII guard.
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
    echo "  Installed pre-commit hook (PII guard)"

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
echo "Available commands: /tax-setup, /tax-parse, /tax-compute, /tax-generate, /tax-verify, /tax-review, /tax-fbar"
