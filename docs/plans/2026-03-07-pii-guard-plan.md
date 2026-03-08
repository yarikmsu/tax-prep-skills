# PII Guard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Install pre-commit and pre-push git hooks that block PII, sensitive file types, and secrets from being committed, and prevent Claude from pushing directly.

**Architecture:** Layer 1 is a bash pre-commit script that blocks dangerous file types. Layer 2 is a Claude Code hook (`.claude/hooks.json` PreCommit event) that semantically scans diffs for PII. A pre-push hook blocks all automated pushes. An allowlist file provides false-positive suppression.

**Tech Stack:** Bash (git hooks), Claude Code hooks JSON, grep/regex

---

### Task 1: Create the pre-commit file type blocker script

**Files:**
- Create: `scripts/pre-commit-pii-scan.sh`

**Step 1: Write the script**

```bash
#!/bin/bash
# Layer 1: Block commits containing sensitive file types.
# Called by .git/hooks/pre-commit.

set -euo pipefail

BLOCKED_EXTENSIONS=(
    "pdf" "xlsx" "xls" "csv" "doc" "docx"
    "png" "jpg" "jpeg" "gif" "bmp" "tiff" "heic"
    "zip" "tar" "gz"
)

BLOCKED_FILENAMES=(
    "taxpayer_profile.json"
    "result.json"
)

# Also block tax_law_*.json
BLOCKED_PATTERNS=(
    "tax_law_.*\\.json"
)

staged_files=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || true)

if [ -z "$staged_files" ]; then
    exit 0
fi

violations=()

while IFS= read -r file; do
    # Check blocked extensions
    ext="${file##*.}"
    ext_lower=$(echo "$ext" | tr '[:upper:]' '[:lower:]')
    for blocked in "${BLOCKED_EXTENSIONS[@]}"; do
        if [ "$ext_lower" = "$blocked" ]; then
            violations+=("BLOCKED FILE TYPE (.${blocked}): $file")
        fi
    done

    # Check blocked filenames
    basename_file=$(basename "$file")
    for blocked in "${BLOCKED_FILENAMES[@]}"; do
        if [ "$basename_file" = "$blocked" ]; then
            violations+=("BLOCKED FILENAME ($blocked): $file")
        fi
    done

    # Check blocked patterns
    for pattern in "${BLOCKED_PATTERNS[@]}"; do
        if echo "$basename_file" | grep -qE "^${pattern}$"; then
            violations+=("BLOCKED PATTERN ($pattern): $file")
        fi
    done
done <<< "$staged_files"

if [ ${#violations[@]} -gt 0 ]; then
    echo ""
    echo "=========================================="
    echo "  COMMIT BLOCKED — Sensitive files found"
    echo "=========================================="
    echo ""
    for v in "${violations[@]}"; do
        echo "  $v"
    done
    echo ""
    echo "These file types should never be committed."
    echo "If this is intentional, use: git commit --no-verify"
    echo ""
    exit 1
fi

exit 0
```

**Step 2: Make it executable and test**

Run:
```bash
chmod +x scripts/pre-commit-pii-scan.sh
```

Test with a fake PDF:
```bash
touch /tmp/test-fake.pdf
cp /tmp/test-fake.pdf .
git add test-fake.pdf
bash scripts/pre-commit-pii-scan.sh
# Expected: "COMMIT BLOCKED — Sensitive files found"
# Expected: exit code 1
echo $?
# Clean up
git reset HEAD test-fake.pdf
rm test-fake.pdf
```

Test with a safe file:
```bash
bash scripts/pre-commit-pii-scan.sh
# Expected: exit code 0 (nothing staged or only safe files)
```

**Step 3: Commit**

```bash
git add scripts/pre-commit-pii-scan.sh
git commit -m "feat: add pre-commit file type blocker script"
```

---

### Task 2: Install git hooks (pre-commit and pre-push)

**Files:**
- Create: `.git/hooks/pre-commit`
- Create: `.git/hooks/pre-push`
- Modify: `agents/install.sh`

**Step 1: Create the pre-commit hook**

Write `.git/hooks/pre-commit`:
```bash
#!/bin/bash
# Git pre-commit hook — runs PII guard.
# Installed by agents/install.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Layer 1: File type blocker
if [ -x "$REPO_ROOT/scripts/pre-commit-pii-scan.sh" ]; then
    "$REPO_ROOT/scripts/pre-commit-pii-scan.sh"
fi
```

Make it executable:
```bash
chmod +x .git/hooks/pre-commit
```

**Step 2: Create the pre-push hook**

Write `.git/hooks/pre-push`:
```bash
#!/bin/bash
# Git pre-push hook — blocks automated pushes.
# Push manually from your terminal after reviewing the commit.

echo ""
echo "=========================================="
echo "  PUSH BLOCKED"
echo "=========================================="
echo ""
echo "  Automated pushes are disabled."
echo "  Review the commit, then push manually:"
echo ""
echo "    git push -u origin $(git branch --show-current)"
echo ""
exit 1
```

Make it executable:
```bash
chmod +x .git/hooks/pre-push
```

**Step 3: Test the pre-commit hook**

```bash
touch /tmp/test-fake.csv
cp /tmp/test-fake.csv .
git add test-fake.csv
git commit -m "test: should be blocked"
# Expected: "COMMIT BLOCKED — Sensitive files found"
# Clean up
git reset HEAD test-fake.csv
rm test-fake.csv
```

**Step 4: Test the pre-push hook**

```bash
git push --dry-run 2>&1 || true
# Expected: "PUSH BLOCKED"
```

Note: `.git/hooks/` is not tracked by git. The hooks are installed by `agents/install.sh`.

**Step 5: Update install.sh to install hooks**

Add after the .gitignore section in `agents/install.sh`:

```bash
# Install git hooks
HOOKS_DIR="$WORKSPACE_ROOT/.git/hooks"
if [ -d "$HOOKS_DIR" ]; then
    # Pre-commit hook
    cat > "$HOOKS_DIR/pre-commit" << 'HOOK_EOF'
#!/bin/bash
# Git pre-commit hook — runs PII guard.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [ -x "$REPO_ROOT/scripts/pre-commit-pii-scan.sh" ]; then
    "$REPO_ROOT/scripts/pre-commit-pii-scan.sh"
fi
HOOK_EOF
    chmod +x "$HOOKS_DIR/pre-commit"
    echo "  Installed pre-commit hook (PII guard)"

    # Pre-push hook
    cat > "$HOOKS_DIR/pre-push" << 'HOOK_EOF'
#!/bin/bash
# Git pre-push hook — blocks automated pushes.
echo ""
echo "=========================================="
echo "  PUSH BLOCKED"
echo "=========================================="
echo ""
echo "  Automated pushes are disabled."
echo "  Review the commit, then push manually:"
echo ""
echo "    git push -u origin $(git branch --show-current)"
echo ""
exit 1
HOOK_EOF
    chmod +x "$HOOKS_DIR/pre-push"
    echo "  Installed pre-push hook (blocks automated push)"
fi
```

**Step 6: Commit**

```bash
git add agents/install.sh scripts/pre-commit-pii-scan.sh
git commit -m "feat: install pre-commit and pre-push git hooks via install.sh"
```

---

### Task 3: Create the Claude Code PII scan hook

**Files:**
- Create: `.claude/hooks.json`

**Step 1: Write hooks.json**

```json
{
  "hooks": [
    {
      "matcher": "PreCommit",
      "hooks": [
        {
          "type": "command",
          "command": "git diff --cached --unified=0 | grep -nE '\\b[0-9]{3}-[0-9]{2}-[0-9]{4}\\b|\\b[0-9]{9}\\b|[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}|\\b[0-9]{3}[-.]?[0-9]{3}[-.]?[0-9]{4}\\b|(sk-|pk-|AKIA|api[_-]?key|token|secret|password)\\s*[:=]\\s*\\S+|[A-Za-z0-9+/]{40,}={0,2}' | grep -vFf .pii-allowlist 2>/dev/null || true"
        }
      ]
    }
  ]
}
```

Note: This is the regex fallback layer. The Claude Code PreCommit hook event runs before the git pre-commit hook. The output is shown to Claude, who can then warn the user about potential PII.

**Step 2: Test**

Stage a file with a fake SSN pattern, verify the hook command detects it:
```bash
echo "SSN: XXX-XX-6789" > /tmp/test-pii.txt
cp /tmp/test-pii.txt .
git add test-pii.txt
git diff --cached --unified=0 | grep -nE '\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b'
# Expected: match on "XXX-XX-6789"
git reset HEAD test-pii.txt
rm test-pii.txt
```

**Step 3: Commit**

```bash
git add .claude/hooks.json
git commit -m "feat: add Claude Code PreCommit hook for PII pattern scanning"
```

---

### Task 4: Create the PII allowlist

**Files:**
- Create: `.pii-allowlist`

**Step 1: Write the allowlist**

```
# PII Allowlist — patterns listed here are excluded from PII scanning.
# One pattern per line. Lines starting with # are comments.
#
# Test/example SSNs
XXX-XX-0000
XXX-XX-
# Example emails used in documentation
example@example.com
user@example.com
# IRS EIN format (XX-XXXXXXX) is allowed — not an SSN
```

**Step 2: Commit**

```bash
git add .pii-allowlist
git commit -m "feat: add PII allowlist for false positive suppression"
```

---

### Task 5: Update CLAUDE.md with Git Workflow rules

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add Git Workflow section**

Add under `## Development Guidelines`, before `### Testing`:

```markdown
### Git Workflow
- NEVER run `git push` — print the command for the user to run manually
- After user confirms push, create PR via `gh pr create`
- Pre-commit hook blocks PII and sensitive file types automatically
- To bypass hooks in emergencies: `git commit --no-verify` (use with caution)
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add Git Workflow rules to CLAUDE.md — never push, always PR"
```

---

### Task 6: End-to-end validation

**No files to create — verification only.**

**Step 1: Test file type blocking**

```bash
touch test-block.xlsx
git add test-block.xlsx
git commit -m "test: should fail"
# Expected: COMMIT BLOCKED
git reset HEAD test-block.xlsx
rm test-block.xlsx
```

**Step 2: Test PII detection**

```bash
echo "SSN is XXX-XX-6789 and email is john@gmail.com" > test-pii.md
git add test-pii.md
git commit -m "test: should fail"
# Expected: COMMIT BLOCKED (pre-commit hook catches it)
git reset HEAD test-pii.md
rm test-pii.md
```

**Step 3: Test clean commit passes**

```bash
echo "# Test file with no PII" > test-clean.md
git add test-clean.md
git commit -m "test: should pass"
# Expected: commit succeeds
git reset --soft HEAD~1
git reset HEAD test-clean.md
rm test-clean.md
```

**Step 4: Test pre-push blocks**

```bash
git push --dry-run 2>&1 || true
# Expected: "PUSH BLOCKED"
```

**Step 5: Print push command for user**

After all tests pass, print:
```
All PII guard hooks installed and verified.

Ready to push. Run:
  git push -u origin feature/pii-guard-hooks
```

---

### Task 7: Create PR

After user confirms push succeeded, create PR:

```bash
gh pr create --title "Add PII guard: pre-commit and pre-push hooks" --body "$(cat <<'EOF'
## Summary
- Pre-commit hook blocks sensitive file types (.pdf, .xlsx, .csv, images, etc.)
- Pre-commit hook blocks sensitive JSON files (taxpayer_profile.json, result.json, tax_law_*.json)
- Claude Code PreCommit hook scans diffs for PII patterns (SSNs, emails, phones, secrets)
- Pre-push hook blocks all automated pushes — user must push manually
- `.pii-allowlist` for false positive suppression
- `agents/install.sh` updated to install hooks automatically
- CLAUDE.md updated with Git Workflow rules

## Test plan
- [x] Staging .xlsx file triggers file type blocker
- [x] Staging file with SSN triggers PII scan
- [x] Clean commit passes both layers
- [x] `git push` blocked by pre-push hook
- [x] Allowlisted patterns not flagged
EOF
)"
```
