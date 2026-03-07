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

echo ""
echo "Installed ${#skill_files[@]} tax skills to $SKILLS_DIR"
echo "Available commands: /tax-setup, /tax-parse, /tax-compute, /tax-generate, /tax-verify, /tax-review, /tax-fbar"
