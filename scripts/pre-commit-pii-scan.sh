#!/bin/bash
set -euo pipefail

# Get staged files (Added, Copied, Modified only)
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACMR)

if [ -z "$STAGED_FILES" ]; then
  exit 0
fi

BLOCKED=()

# Blocked extensions (case-insensitive)
BLOCKED_EXTENSIONS="pdf|xlsx|xls|csv|doc|docx|png|jpg|jpeg|gif|bmp|tiff|heic|zip|tar|gz"

while IFS= read -r file; do
  filename=$(basename "$file")
  # Allowlist: synthetic test fixtures need to share filenames with real workspace
  # files because the code under test reads result.json / taxpayer_profile.json /
  # tax_law_*.json from a workspace directory. Anything under tests/fixtures/ is
  # reviewed at PR time to confirm it contains only synthetic data.
  case "$file" in
    tests/fixtures/*) continue ;;
  esac
  # Check extension (case-insensitive)
  if echo "$filename" | grep -qiE "\.($BLOCKED_EXTENSIONS)$"; then
    ext=$(echo "$filename" | grep -oiE "\.($BLOCKED_EXTENSIONS)$" | tr '[:upper:]' '[:lower:]')
    BLOCKED+=("  BLOCKED FILE TYPE ($ext): $file")
    continue
  fi

  # Check specific filenames
  if [ "$filename" = "taxpayer_profile.json" ] || [ "$filename" = "result.json" ] || [ "$filename" = "answers.md" ] || [ "$filename" = "parse_ledger.json" ] || [ "$filename" = "holdings.json" ]; then
    BLOCKED+=("  BLOCKED FILENAME ($filename): $file")
    continue
  fi

  # Check tax_law_*.json pattern
  if echo "$filename" | grep -qE '^tax_law_.*\.json$'; then
    BLOCKED+=("  BLOCKED FILENAME ($filename): $file")
    continue
  fi

  # Check FBAR_*.md pattern
  if echo "$filename" | grep -qE '^FBAR_.*\.md$'; then
    BLOCKED+=("  BLOCKED FILENAME ($filename): $file")
    continue
  fi

  # Persisted /tax-review workpapers (reviews/{year}-review.md) derive from
  # PII-bearing result.json — block the directory and the filename pattern
  case "$file" in
    reviews/*|*/reviews/*)
      BLOCKED+=("  BLOCKED PATH (reviews/): $file")
      continue ;;
  esac
  if echo "$filename" | grep -qE '^[0-9]{4}-review\.md$'; then
    BLOCKED+=("  BLOCKED FILENAME ($filename): $file")
    continue
  fi
done <<< "$STAGED_FILES"

if [ ${#BLOCKED[@]} -gt 0 ]; then
  echo ""
  echo "=========================================="
  echo "  COMMIT BLOCKED — Sensitive files found"
  echo "=========================================="
  echo ""
  for line in "${BLOCKED[@]}"; do
    echo "$line"
  done
  echo ""
  echo "These file types should never be committed."
  echo "If this is intentional, use: git commit --no-verify"
  exit 1
fi

exit 0
