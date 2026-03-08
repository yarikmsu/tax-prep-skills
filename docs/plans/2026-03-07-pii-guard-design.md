# PII Guard — Pre-commit & Pre-push Hooks

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent accidental commit of personal/private data and ensure Claude never pushes directly.

**Architecture:** Two-layer pre-commit gate (file type blocker + Claude PII scan) plus a pre-push hook that blocks automated pushes. Claude prints the push command; user runs it manually; Claude creates the PR.

## Layer 1: File Type Blocker

A git pre-commit hook script (`scripts/pre-commit-pii-scan.sh`) that instantly rejects commits containing sensitive file types.

### Blocked Extensions

| Category | Extensions |
|----------|-----------|
| Documents | `.pdf`, `.xlsx`, `.xls`, `.csv`, `.doc`, `.docx` |
| Images | `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.tiff`, `.heic` |
| Archives | `.zip`, `.tar`, `.gz` |
| Sensitive JSON | `taxpayer_profile.json`, `result.json`, `tax_law_*.json` |

On match: print offending files and exit 1 (commit blocked).

## Layer 2: Claude PII Scan

A Claude Code hook (`.claude/hooks.json`, `PreCommit` event) that semantically scans the staged diff for PII and secrets.

### What It Scans For

**PII patterns:**
- SSNs (full 9-digit, with or without dashes)
- Email addresses
- Phone numbers
- Full names (loaded dynamically from `taxpayer_profile.json` if present)
- Physical addresses
- Bank account and routing numbers

**Secret patterns:**
- API keys (sk-*, pk-*, AKIA*)
- Tokens and passwords near assignment operators
- High-entropy base64 blobs (40+ chars)

### Context Awareness

Claude understands context — masked SSNs (`XXX-XX-1234`) are fine, full SSNs are not. Test data with `000-00-0000` is fine. The scan reads `.pii-allowlist` for known false positives.

### Allowlist Format (`.pii-allowlist`)

One pattern per line, `#` comments:

```
# Test SSNs
000-00-0000
# Example emails in docs
example@example.com
```

### On Match

Print file, line number, finding description. Block the commit (exit 1).

## Pre-push Hook

`.git/hooks/pre-push` blocks all pushes with:

```
ERROR: Push blocked by pre-push hook.
Run the push command manually from your terminal.
```

## Workflow

```
git commit triggered
    ↓
Layer 1: File type blocker (instant)
  → Block .pdf, .xlsx, .csv, images, sensitive .json
  → FAIL → commit rejected
    ↓
Layer 2: Claude PII scan (PreCommit hook, ~5-10s)
  → Semantic scan of staged diff for PII/secrets
  → Reads .pii-allowlist for known exceptions
  → FAIL → commit rejected with findings
    ↓
Commit succeeds
    ↓
Claude prints: "Run: git push -u origin <branch>"
    ↓
User pushes manually
    ↓
Claude creates PR via gh pr create
    ↓
git push attempted by Claude
  → Pre-push hook blocks with error message
```

## CLAUDE.md Updates

Add under Development Guidelines:

```
### Git Workflow
- NEVER run `git push` — print the command for the user to run manually
- After user confirms push, create PR via `gh pr create`
- Pre-commit hook blocks PII and sensitive file types automatically
```

## Files to Create/Modify

| File | Action |
|------|--------|
| `scripts/pre-commit-pii-scan.sh` | Create — file type blocker script |
| `.claude/hooks.json` | Create/modify — add PreCommit Claude PII scan |
| `.git/hooks/pre-commit` | Create — calls `scripts/pre-commit-pii-scan.sh` |
| `.git/hooks/pre-push` | Create — blocks all pushes |
| `.pii-allowlist` | Create — known false positives |
| `CLAUDE.md` | Modify — add Git Workflow section |
| `agents/install.sh` | Modify — install hooks during setup |

## Testing

- Commit a file containing a fake SSN → should be blocked
- Commit a `.pdf` file → should be blocked
- Commit clean code → should pass both layers
- Attempt `git push` → should be blocked by pre-push hook
- Allowlisted patterns → should not trigger
