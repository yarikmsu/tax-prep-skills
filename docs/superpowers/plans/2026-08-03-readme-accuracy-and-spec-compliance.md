# README Accuracy + Agent Skills Spec Compliance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every claim in `README.md` true, make first-run setup correct for
non-Claude agents, and bring the 20 skills into compliance with the Agent Skills
specification.

**Architecture:** Three independent workstreams. (1) A **real** commit-time PII
content scan replaces a documented-but-dead one, implemented host-side in
`scripts/pre-commit-pii-scan.sh` so it protects every agent, not just Claude
Code. (2) Documentation is corrected against verified vendor behaviour and
against the repo's own tree. (3) Skill frontmatter and bodies are brought to the
published spec — trigger clauses in every `description`, and progressive
disclosure for the three `SKILL.md` files that exceed the 500-line limit.

**Tech Stack:** Bash (git hooks, CI), Python 3.10+ / pytest (invariant tests),
Markdown (skills and docs), GitHub Actions.

---

## Baseline (verified 2026-08-03, at `81646ec` on `main`)

Facts this plan depends on. Re-verify any that look stale before relying on them.

| Fact | Value |
|---|---|
| Test suite | **2100 passing** (`docs/contributing/test-invariants.md` says 2099 — off by one) |
| `.claude/hooks.json` | Tracked since PR #3; declares a `PreCommit` event that **does not exist** in Claude Code, in a file that is **not a hook config location**. Has never executed. |
| `scripts/pre-commit-pii-scan.sh` | 78 lines, **filename/extension only** — no content scanning |
| `.github/workflows/ci.yml` PII job | Same logic, also filename-only |
| Skills over the 500-line limit | `tax-compute` 1603, `tax-setup` 938, `tax-review` 546 |
| Skills with a "when to use" trigger in `description` | **0 of 20** |
| Skills with `references/`, `scripts/`, or `assets/` | **0 of 20** |
| `name` matches directory, lowercase-hyphen, ≤64 chars | 20 of 20 ✅ |
| `description` non-empty, ≤1024 chars | 20 of 20 ✅ (longest 281) |
| LICENSE file | None |
| GitHub repo description | `"Reusable tax preparation toolkit for Claude Code — /tax-* skills pipeline"` (stale) |

### Verified vendor behaviour (do not re-litigate these in review)

- **All five agents scan `.agents/skills/`.** Codex walks `.agents/skills` from
  CWD up to the repo root; Cursor scans `.agents/skills/` + `.cursor/skills/`
  with `.claude/skills/` as legacy; Kimi scans `.agents/skills/` (generic group);
  VS Code Copilot lists `.github/skills/`, `.claude/skills/` **and
  `.agents/skills/`** as defaults; Claude Code resolves the tracked symlinks.
- **`.agents/skills` IS a VS Code default.** `docs/contributing/agents-compat.md`
  currently claims the opposite. `.vscode/settings.json` stays — its *other*
  justification (Windows clones without symlinks) still holds.
- **Invocation syntax differs per agent:** Claude Code `/tax-setup`; Cursor `/`
  then search; Codex `$tax-setup` (`/skills` lists them); Kimi `/skill:tax-setup`;
  VS Code by name or automatic.
- **Codex live search** is a top-level key in `~/.codex/config.toml`:
  `web_search = "live"` (default `"cached"`). The `--search` flag is equivalent
  per-invocation.

---

## Global Constraints

Every task's requirements implicitly include this section.

- **Never credit an AI assistant.** No commit message, PR title or body, or
  committed file may carry a generated-with note, a co-author trailer, or a
  session link.
- **Never commit to `main`.** Work on a feature branch. The pre-commit hook
  enforces this.
- **Never run `git push` yourself.** Print `git push --no-verify -u origin <branch>`
  and let the user run it. Open the PR with `gh pr create` only after the user
  confirms the push.
- **PII scan rules are duplicated** between `scripts/pre-commit-pii-scan.sh` and
  `.github/workflows/ci.yml`. Change both in the same commit or the gate diverges.
- **The scanner must never print a matched value.** Report file name and match
  count only. Echoing the match would violate the "never log, echo, or display
  sensitive values" rule in `AGENTS.md` §1 — the dead `hooks.json` regex piped
  `grep -n` output straight to the terminal, and that is part of why it is being
  replaced rather than repaired.
- **Hard gates stay in `SKILL.md`.** `tax-compute` Step 0.5 (freshness), Step 0.7
  (capability gate) and Step 12.7 (tie-out) must remain in the always-loaded body,
  never in a `references/` file. A gate the agent might not read is a gate that
  silently over-states completeness.
- **Skill bodies stay agent-neutral.** `tests/test_portability.py::TestNeutralizationLint`
  enforces this; do not name a Claude-only tool in any skill body.
- **No hardcoded tax law.** Nothing in this plan touches computation, but the rule
  still binds any incidental edit.
- **Frontmatter limits:** `name` ≤64 chars, lowercase alphanumerics and hyphens
  only, no leading/trailing/consecutive hyphens, equal to the directory name.
  `description` non-empty, ≤1024 chars.
- **Run the suite before every commit:**
  `source .venv/bin/activate && python3 -m pytest tests/ -q`
- **Update the test count** on line 9 of `docs/contributing/test-invariants.md`
  in the same commit that adds tests.
- **Never `pip install`.** Use the existing `.venv`.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `tests/test_pii_content_scan.py` | Behavioural tests for the content scanner: blocks a real SSN, passes the masked and allowlisted forms, never prints a match, scans fixtures, ignores unmodified pre-existing content |
| `LICENSE` | Repository license (Task 6 — decision required) |
| `.agents/skills/tax-compute/references/*.md` | The five computation sections lifted out of `tax-compute/SKILL.md` |
| `.agents/skills/tax-setup/references/*.md` | The two long sections lifted out of `tax-setup/SKILL.md` |
| `.agents/skills/tax-review/references/report-templates.md` | The five output-template sections lifted out of `tax-review/SKILL.md` |

**Modified:**

| Path | Change |
|---|---|
| `scripts/pre-commit-pii-scan.sh` | Add the content scan after the existing filename scan |
| `.github/workflows/ci.yml` | Mirror the content scan into the `pii-scan` job |
| `README.md` | Security layer 3, invocation table, Quick Start, workspace note, Project Structure |
| `agents/install.sh` | `--hooks-only` flag; suppress Claude-specific output for other agents |
| `docs/contributing/agents-compat.md` | Correct the VS Code default-locations claim |
| `docs/contributing/test-invariants.md` | Test count, plus one line per new invariant block |
| `tests/test_portability.py` | Spec-limit assertions; description trigger clause; oversize allowlist |
| `.agents/skills/*/SKILL.md` (20) | Append a "Use when …" trigger clause to each `description` |

**Deleted:**

| Path | Reason |
|---|---|
| `.claude/hooks.json` | Declares a nonexistent hook event in a file Claude Code does not read. Its function moves to `scripts/pre-commit-pii-scan.sh`, which is host-side and therefore agent-independent. |

---

## PR boundaries

- **PR 1 — Tasks 1–5** (`fix/readme-accuracy-and-onboarding`): correctness and
  first-run experience. No skill-body restructuring. Ship this first.
- **PR 2 — Tasks 6–8** (`feat/agent-skills-spec-compliance`): licensing, spec
  limit enforcement, progressive disclosure.

---

### Task 1: Replace the dead PII hook with a real content scan

**Files:**
- Modify: `scripts/pre-commit-pii-scan.sh` (append after line 76, before `exit 0`)
- Modify: `.github/workflows/ci.yml:33-98`
- Modify: `README.md:154-160`, `README.md:217-218`
- Delete: `.claude/hooks.json`
- Test: `tests/test_pii_content_scan.py` (create)

**Interfaces:**
- Consumes: `.pii-allowlist` (one literal pattern per line; `#` comments and
  blank lines are stripped before use)
- Produces: exit code 1 with a `BLOCKED CONTENT (<n> match(es)): <file>` line per
  offending file. Later tasks rely on nothing from this one.

**Why not repair `.claude/hooks.json`:** a Claude-settings hook protects only
Claude Code sessions. This repository's stated security posture is that the git
hooks are "the host-side enforcement backstop [that] work the same on every
agent" (`AGENTS.md` §1). Codex, Cursor and Kimi all commit through the same git
hook; none of them read Claude settings.

**Scope note — what the scanner does and does not catch.** It blocks full SSNs
and three high-confidence credential prefixes. It deliberately does **not** match
email addresses or phone numbers: `[0-9]{3}[-.]?[0-9]{3}[-.]?[0-9]{4}` matches
account numbers, form line references and identifiers throughout this repo, and a
gate that cries wolf gets bypassed with `--no-verify` as a reflex. The README
must describe what exists, not what the dead hook advertised.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pii_content_scan.py`:

```python
"""Content-level PII scan — the commit-time guard for full SSNs.

The filename/extension scan blocks the usual PII carriers (PDF, XLSX, CSV,
taxpayer_profile.json, answers.md). This covers the residual case: a full SSN
typed into an otherwise-innocent tracked file, which no other gate sees.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCAN = REPO / "scripts" / "pre-commit-pii-scan.sh"

# Assembled at runtime, never written as a literal.
#
# A test for a PII scanner cannot contain PII-shaped literals: this file would
# trip the very scan it exercises, and the obvious workaround — adding the value
# to .pii-allowlist — would make the scanner skip it everywhere AND make
# test_full_ssn_in_an_added_line_blocks pass vacuously, because these tests copy
# the real allowlist into the throwaway repo. Splitting the value keeps the
# scanner at full strength and the test honest. The same applies to this plan
# document, which the scanner blocked on its first commit attempt.
_SSN_AREA, _SSN_GROUP, _SSN_SERIAL = "123", "45", "678" + "9"
FAKE_SSN = f"{_SSN_AREA}-{_SSN_GROUP}-{_SSN_SERIAL}"
FAKE_AWS_KEY = "AKIA" + "IOSFODNN7" + "EXAMPLE"


def _make_repo(tmp_path: Path) -> Path:
    """A throwaway git repo carrying a copy of the scanner and the allowlist."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"],
                   cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "scripts").mkdir()
    dest = tmp_path / "scripts" / "pre-commit-pii-scan.sh"
    shutil.copy(SCAN, dest)
    os.chmod(dest, 0o755)
    shutil.copy(REPO / ".pii-allowlist", tmp_path / ".pii-allowlist")
    return tmp_path


def _stage(repo: Path, name: str, content: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    subprocess.run(["git", "add", name], cwd=repo, check=True)


def _run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh"],
                          cwd=repo, capture_output=True, text=True)


def test_full_ssn_in_an_added_line_blocks(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md", f"Taxpayer SSN: {FAKE_SSN}\n")
    assert _run(repo).returncode == 1


def test_block_message_never_prints_the_matched_value(tmp_path):
    """Printing the match would leak the SSN into terminal scrollback and CI logs."""
    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md", f"Taxpayer SSN: {FAKE_SSN}\n")
    result = _run(repo)
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert FAKE_SSN not in combined, "scanner echoed the value it was guarding"
    assert "notes.md" in combined, "scanner must name the offending file"


def test_masked_last_four_format_passes(tmp_path):
    """XXX-XX-1234 is the ALLOWED storage format and must never block."""
    repo = _make_repo(tmp_path)
    _stage(repo, "profile.md", 'ssn_last4: "XXX-XX-6789"\n')
    assert _run(repo).returncode == 0


def test_allowlisted_example_ssn_passes(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "docs.md", "Example only: 000-00-0000\n")
    assert _run(repo).returncode == 0


def test_ssn_under_tests_fixtures_still_blocks(tmp_path):
    """tests/fixtures/ is exempt from the FILENAME scan, not the content scan —
    a real SSN in a synthetic fixture is exactly the mistake this catches."""
    repo = _make_repo(tmp_path)
    _stage(repo, "tests/fixtures/result.json", f'{{"ssn": "{FAKE_SSN}"}}\n')
    assert _run(repo).returncode == 1


def test_aws_access_key_blocks(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "config.py", f'KEY = "{FAKE_AWS_KEY}"\n')
    assert _run(repo).returncode == 1


def test_preexisting_content_in_an_unmodified_region_does_not_block(tmp_path):
    """Only ADDED lines are scanned, so editing a file elsewhere stays committable."""
    repo = _make_repo(tmp_path)
    _stage(repo, "legacy.md", f"old: {FAKE_SSN}\n")
    subprocess.run(["git", "commit", "-q", "--no-verify", "-m", "seed"],
                   cwd=repo, check=True)
    (repo / "legacy.md").write_text(f"old: {FAKE_SSN}\nnew innocent line\n")
    subprocess.run(["git", "add", "legacy.md"], cwd=repo, check=True)
    assert _run(repo).returncode == 0


def test_clean_diff_passes(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "readme.md", "Nothing sensitive here.\n")
    assert _run(repo).returncode == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
source .venv/bin/activate && python3 -m pytest tests/test_pii_content_scan.py -v
```

Expected: 6 FAIL (the block cases return 0 — no content scan exists yet), 2 PASS
(`test_masked_last_four_format_passes`, `test_clean_diff_passes`, which pass
vacuously today).

- [ ] **Step 3: Add the content scan to the shell script**

In `scripts/pre-commit-pii-scan.sh`, insert this block immediately after the
existing `if [ ${#BLOCKED[@]} -gt 0 ]; then … fi` block (currently ending at
line 76) and before the final `exit 0`:

```bash
# ---------------------------------------------------------------------------
# Content scan — full SSNs and high-confidence credentials.
#
# Scans ADDED lines only (-U0 plus a leading '+'), so editing a file elsewhere
# does not re-flag content that was already committed.
#
# Deliberately does NOT exempt tests/fixtures/, unlike the filename scan above:
# a real SSN in a synthetic fixture is exactly the mistake this exists to catch.
# Suppress a known-synthetic value by adding it to .pii-allowlist.
#
# Deliberately narrow: emails and phone numbers are not matched, because their
# patterns collide with account numbers and form line references throughout this
# repository, and a gate that cries wolf gets bypassed reflexively.
#
# Keep in sync with .github/workflows/ci.yml.
# ---------------------------------------------------------------------------
SSN_PATTERN='[0-9]{3}-[0-9]{2}-[0-9]{4}'
SECRET_PATTERN='(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36})'

# `grep -vFf` treats EVERY line of the allowlist as a literal pattern, so a
# comment line would suppress any diff line containing that comment's text.
# Strip comments and blanks first.
ALLOW=$(mktemp)
if [ -f .pii-allowlist ]; then
  grep -vE '^[[:space:]]*(#|$)' .pii-allowlist > "$ALLOW" || true
fi

CONTENT_BLOCKED=()
while IFS= read -r file; do
  [ -n "$file" ] || continue
  added=$(git diff --cached -U0 --diff-filter=ACMR -- "$file" \
            | grep -E '^\+' | grep -vE '^\+\+\+ ' || true)
  [ -n "$added" ] || continue
  # The trailing `|| true` is load-bearing: `set -o pipefail` is active, and a
  # grep that matches nothing exits 1, which fails the whole pipeline, which
  # fails the assignment, which aborts the script under `set -e`. Without it the
  # scanner exits non-zero on every CLEAN commit — a block with no message.
  hits=$(printf '%s\n' "$added" \
           | grep -E "$SSN_PATTERN|$SECRET_PATTERN" \
           | grep -vFf "$ALLOW" \
           | wc -l | tr -d '[:space:]' || true)
  if [ "${hits:-0}" -gt 0 ]; then
    # Report the FILE and a COUNT only. Never echo the match — that would put
    # the value into terminal scrollback and CI logs.
    CONTENT_BLOCKED+=("  BLOCKED CONTENT ($hits match(es)): $file")
  fi
done <<< "$STAGED_FILES"

rm -f "$ALLOW"

if [ ${#CONTENT_BLOCKED[@]} -gt 0 ]; then
  echo ""
  echo "=========================================="
  echo "  COMMIT BLOCKED — PII pattern in content"
  echo "=========================================="
  echo ""
  for line in "${CONTENT_BLOCKED[@]}"; do
    echo "$line"
  done
  echo ""
  echo "A full SSN or credential appears in the staged changes."
  echo "Store only the last four digits, as XXX-XX-1234."
  echo "If the value is synthetic, add it to .pii-allowlist."
  echo "If this is intentional, use: git commit --no-verify"
  exit 1
fi
```

**Implementer note — this block was prototyped and all eight test cases above
pass against it, but only after one fix worth understanding.** `set -euo pipefail`
is active in this script. Every `grep` that finds nothing exits 1; with `pipefail`
that fails the entire pipeline, which fails the `hits=$(...)` assignment, which
aborts the script under `set -e`. An aborted scanner exits non-zero and is
indistinguishable from a block — so the first draft silently rejected **every**
clean commit while printing nothing. Both the `added=` and `hits=` pipelines
therefore end in `|| true`. If you refactor either one, re-run the clean-file case
first; it is the case that catches this and the one most likely to be skipped.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
source .venv/bin/activate && python3 -m pytest tests/test_pii_content_scan.py -v
```

Expected: 8 PASS.

- [ ] **Step 5: Mirror the scan into CI**

In `.github/workflows/ci.yml`, add a second step to the `pii-scan` job, after the
existing `Check for blocked file types` step:

```yaml
      - name: Check for PII patterns in content
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
        run: |
          # Keep in sync with scripts/pre-commit-pii-scan.sh.
          SSN_PATTERN='[0-9]{3}-[0-9]{2}-[0-9]{4}'
          SECRET_PATTERN='(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36})'

          ALLOW=$(mktemp)
          grep -vE '^[[:space:]]*(#|$)' .pii-allowlist > "$ALLOW" || true

          VIOLATIONS_FILE=$(mktemp)
          git diff --name-only --diff-filter=ACMR "${BASE_SHA}...HEAD" \
            | while IFS= read -r file; do
                [ -n "$file" ] || continue
                added=$(git diff -U0 --diff-filter=ACMR "${BASE_SHA}...HEAD" -- "$file" \
                          | grep -E '^\+' | grep -vE '^\+\+\+ ' || true)
                [ -n "$added" ] || continue
                # `|| true` required — see the note in Step 3. A non-matching
                # grep otherwise fails the pipeline and aborts the step.
                hits=$(printf '%s\n' "$added" \
                         | grep -E "$SSN_PATTERN|$SECRET_PATTERN" \
                         | grep -vFf "$ALLOW" \
                         | wc -l | tr -d '[:space:]' || true)
                if [ "${hits:-0}" -gt 0 ]; then
                  # File and count only — never echo the match into the CI log.
                  echo "  BLOCKED CONTENT ($hits match(es)): $file" >> "$VIOLATIONS_FILE"
                fi
              done

          rm -f "$ALLOW"

          if [ -s "$VIOLATIONS_FILE" ]; then
            echo ""
            echo "=========================================="
            echo "  CI BLOCKED — PII pattern in content"
            echo "=========================================="
            echo ""
            cat "$VIOLATIONS_FILE"
            rm -f "$VIOLATIONS_FILE"
            exit 1
          fi

          rm -f "$VIOLATIONS_FILE"
          echo "No PII content patterns found."
```

- [ ] **Step 6: Delete the dead hook file**

```bash
git rm .claude/hooks.json
```

- [ ] **Step 7: Correct the README security section**

Replace `README.md` lines 152-163 with:

```markdown
**Pre-commit hooks (installed by `agents/install.sh`):**

Three layers run on every commit, host-side — so they fire from any agent that
can commit at all, not just the one that installed them:

1. **Branch guard** — blocks direct commits to `main`/`master`. Hard block.

2. **File type blocker** (`scripts/pre-commit-pii-scan.sh`) — blocks sensitive
   file types (PDF, XLSX, CSV, images, archives) and sensitive filenames
   (`taxpayer_profile.json`, `result.json`, `tax_law_*.json`, `answers.md`,
   `FBAR_*.md`, `reviews/`). Hard block.

3. **Content scanner** (same script) — scans the added lines of the staged diff
   for full SSNs and high-confidence credentials, and blocks the commit if it
   finds one. It reports the file name and a match count, never the value.
   `.pii-allowlist` suppresses known-synthetic values such as `000-00-0000`.
   Hard block.

**Pre-push hook:**
- Blocks every push, including your own, so that nothing leaves the machine
  without a deliberate review. It prints the command to use when you are ready:
  `git push --no-verify -u origin <branch>`.
```

Then update `README.md:217-218` — the `.claude/` entry in Project Structure now
reads:

```
.claude/
└── skills/           # Relative symlinks -> ../../.agents/skills/<name>
                      # (project-local discovery for Claude Code and Cursor)
```

- [ ] **Step 8: Run the full suite**

```bash
source .venv/bin/activate && python3 -m pytest tests/ -q
```

Expected: 2108 passed (2100 + 8 new).

- [ ] **Step 9: Update the test count**

`docs/contributing/test-invariants.md` line 9: `2099` → `2108`. Add to the
invariant inventory:

```markdown
- `test_pii_content_scan.py` — the commit-time content guard. Pins that a full
  SSN blocks, that the masked `XXX-XX-` form and allowlisted synthetic values do
  not, that `tests/fixtures/` is scanned for content even though it is exempt
  from the filename scan, that only added lines are considered, and that the
  block message never echoes the matched value.
```

- [ ] **Step 10: Commit**

```bash
git checkout -b fix/readme-accuracy-and-onboarding
git add scripts/pre-commit-pii-scan.sh .github/workflows/ci.yml \
        tests/test_pii_content_scan.py README.md \
        docs/contributing/test-invariants.md
git rm --cached .claude/hooks.json 2>/dev/null || true
git commit -m "fix: replace the never-executing PII hook with a real content scan

.claude/hooks.json declared a PreCommit event that does not exist, in a file
Claude Code does not read as hook config, so the SSN content scan the README
advertised as security layer 3 had never run. The filename scan covers the usual
PII carriers, leaving a full SSN typed into a tracked .md or .py catchable by
nothing.

Implemented in the git pre-commit script instead of Claude settings so it
protects commits from every agent, and mirrored into CI. Reports file and match
count only — the old regex piped grep -n output straight to the terminal, which
would have printed the SSN it was guarding."
```

---

### Task 2: Per-agent invocation table and an install verification step

**Files:**
- Modify: `README.md:39-46` (post-Quick-Start invocation), `README.md:82-100`
- Test: `tests/test_portability.py` (add `TestReadmeOnboarding`)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `TestReadmeOnboarding`, which Task 3 extends with two more assertions.

**Why this matters more than it looks:** the README shows `/tax-setup 2025`
throughout. That syntax is correct for exactly one of the five supported agents.
A Codex user types `/tax-setup`, gets nothing, and concludes the install failed —
with no error message to search for.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_portability.py`:

```python
class TestReadmeOnboarding:
    """The README is the only onboarding surface for four of the five agents.

    Each entry is a (label, required substring) pair. The registry is explicit
    rather than derived from the README's own text: a check that selects its own
    scope from the document it inspects can always be silenced by editing that
    document.
    """

    INVOCATION_SYNTAX = [
        ("Claude Code", "/tax-setup"),
        ("Codex", "$tax-setup"),
        ("Kimi", "/skill:tax-setup"),
        ("Cursor", "Cursor"),
        ("VS Code", "VS Code"),
    ]

    def test_readme_documents_per_agent_invocation_syntax(self):
        readme = (REPO / "README.md").read_text()
        missing = [label for label, token in self.INVOCATION_SYNTAX
                   if token not in readme]
        assert not missing, (
            f"README does not show invocation syntax for: {missing}. "
            "Only Claude Code uses a bare /skill-name; a user of any other "
            "agent gets silence, not an error.")

    def test_readme_tells_the_user_how_to_verify_discovery(self):
        readme = (REPO / "README.md").read_text().lower()
        assert "/skills" in readme, (
            "README must show how to confirm the skills were discovered; "
            "a silent failure is otherwise indistinguishable from success.")
```

- [ ] **Step 2: Run to verify it fails**

```bash
source .venv/bin/activate && python3 -m pytest tests/test_portability.py -k Onboarding -v
```

Expected: 2 FAIL.

- [ ] **Step 3: Replace README lines 39-46**

```markdown
Then ask your agent to run the setup skill. **How you invoke a skill differs by
agent** — only Claude Code uses a bare slash command:

| Agent | Invoke it with | List available skills |
|---|---|---|
| Claude Code | `/tax-setup 2025` | type `/` |
| Cursor | type `/`, search `tax-setup` | type `/` |
| Codex | `$tax-setup` for 2025 | `/skills` |
| Kimi Code | `/skill:tax-setup 2025` | `/skill:` |
| GitHub Copilot (VS Code) | "follow the tax-setup skill for 2025" | agent picks it up automatically |

If your agent has no skill mechanism at all, open
`.agents/skills/tax-setup/SKILL.md` and tell the agent to follow it — the files
are plain Markdown instructions and are written to be executable that way.

**Verify the skills were found before you start.** All five agents fail silently
when discovery does not work: you get no skill and no error. List them (`/skills`
in Codex, `/` in Claude Code or Cursor) and confirm you see 20 entries beginning
with `tax-`.

This starts the taxpayer interview and creates your workspace.
```

Then, in the Skill Reference table header at line 84, change the `Command` column
heading to `Skill` and the cell values from `/tax-setup <year>` to `tax-setup <year>`
(and likewise for the other six rows), so the table names skills rather than
implying one agent's syntax.

- [ ] **Step 4: Run to verify it passes**

```bash
source .venv/bin/activate && python3 -m pytest tests/test_portability.py -k Onboarding -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_portability.py
git commit -m "docs: document per-agent invocation syntax and a discovery check

The README showed /tax-setup throughout, which is correct for one of the five
supported agents. Codex uses \$tax-setup and Kimi /skill:tax-setup; a user of
either gets silence rather than an error, which is indistinguishable from a
failed install. Adds the syntax table and a verification step."
```

---

### Task 3: Promote live web search, explain the workspace, de-Claude the installer

**Files:**
- Modify: `README.md:19-37` (Quick Start), `README.md:11-17` (Prerequisites)
- Modify: `agents/install.sh:9-28`, `agents/install.sh:199-209`
- Test: `tests/test_portability.py` (extend `TestReadmeOnboarding` and `TestInstaller`)

**Interfaces:**
- Consumes: `TestReadmeOnboarding` from Task 2.
- Produces: `--hooks-only` on `install.sh`. No later task depends on it.

**Why:** `web_search = "live"` is the single highest-consequence setup step in the
whole toolkit — `AGENTS.md` §2 says a stale figure produces a wrong return that no
downstream gate can detect — and it is currently bullet 2 of 5 in a list most
people skim. Meanwhile Quick Start step 3 tells a Codex user to run a script that
installs 20 skills into `~/.claude/skills/` and then prints slash commands their
agent does not have.

- [ ] **Step 1: Write the failing tests**

Add to `TestReadmeOnboarding` in `tests/test_portability.py`:

```python
    def test_quick_start_includes_the_live_web_search_step(self):
        """A cached tax figure is the one error no downstream gate can catch,
        so the setting that prevents it belongs in the numbered steps, not in a
        prerequisites bullet."""
        readme = (REPO / "README.md").read_text()
        quick_start = readme.split("## Quick Start", 1)[1].split("\n## ", 1)[0]
        assert 'web_search = "live"' in quick_start, (
            "the live-search setting must appear inside Quick Start")

    def test_readme_states_that_the_clone_is_the_workspace(self):
        readme = (REPO / "README.md").read_text().lower()
        assert "workspace" in readme and "documents/" in readme
        assert "never push" in readme or "do not push" in readme, (
            "README must warn against pushing a clone that holds taxpayer files")
```

Add to `TestInstaller`:

```python
    def test_offers_a_hooks_only_mode(self):
        """Codex/Cursor/Kimi users need the git hooks but not a ~/.claude tree."""
        src = (REPO / "agents" / "install.sh").read_text()
        assert "--hooks-only" in src, (
            "installer must let a non-Claude user install the PII hooks without "
            "populating ~/.claude/skills")
```

- [ ] **Step 2: Run to verify they fail**

```bash
source .venv/bin/activate && python3 -m pytest tests/test_portability.py -k "Onboarding or Installer" -v
```

Expected: 3 FAIL, the rest PASS.

- [ ] **Step 3: Rewrite Quick Start (README lines 19-46)**

```markdown
## Quick Start

```bash
# 1. Clone the repo. This directory becomes your tax workspace — your documents
#    and computed results live here, gitignored and local-only.
git clone https://github.com/yarikmsu/tax-prep-skills.git
cd tax-prep-skills

# 2. Set up Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Install the PII git hooks. Every agent needs these — they run host-side on
#    `git commit`, so they protect you regardless of which agent you use.
#    Claude Code users: omit --hooks-only to also get a global skill install.
bash agents/install.sh --hooks-only
```

**4. Turn on live web search.** Tax brackets change every year, and a cached
search result silently produces a return that is wrong but entirely plausible —
no test, gate or review step in this toolkit can detect it. Codex in particular
defaults to cached results:

```toml
# ~/.codex/config.toml
web_search = "live"     # default is "cached" — this is not optional
```

Equivalent per-invocation: `codex --search`. For other agents see the
[per-agent notes](docs/contributing/agents-compat.md); if your agent cannot do
live web search at all, the skills will stop and ask you to paste the current
figures with their source URL.

**5. Start your agent in this directory.** Put your source documents in
`documents/`, then invoke the setup skill.
```

- [ ] **Step 4: Add the workspace warning after the Quick Start block**

Insert immediately before `### Upgrading from a version before the multi-agent layout`:

```markdown
> **The clone is your workspace, not just the tool.** `documents/`,
> `taxpayer_profile.json`, `result.json`, `summaries/` and the generated PDFs are
> created inside it. All of them are gitignored and the pre-commit hooks block
> them, but **never push this clone to a remote you did not create** — and never
> paste taxpayer data into a GitHub issue or pull request, where hosted coding
> agents read it and the platform retains it.
```

- [ ] **Step 5: Add `--hooks-only` to the installer**

In `agents/install.sh`, replace the usage comment and argument parsing (lines 9-28):

```bash
# Usage: bash agents/install.sh [--copy | --hooks-only]
#   --copy        Copy each SKILL.md instead of symlinking. Use on Windows clones
#                 without developer mode, where symlink creation fails.
#   --hooks-only  Install the git hooks and .gitignore entries, but skip the
#                 global Claude Code skill install. Use this on Codex, Cursor,
#                 Kimi Code or Copilot: they discover .agents/skills/ in the
#                 clone directly, so a ~/.claude/skills tree would be inert.

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
```

Guard the skill-install loop. Replace the `if [ ! -d "$SOURCE_DIR" ]` check
through the `installed` loop's closing `fi` (lines 30-76) so the whole block runs
only when installing skills:

```bash
installed=()
if [ "$INSTALL_MODE" != "hooks-only" ]; then
    if [ ! -d "$SOURCE_DIR" ]; then
        echo "Error: skills directory not found at $SOURCE_DIR"
        exit 1
    fi

    mkdir -p "$SKILLS_DIR"

    shopt -s nullglob
    skill_dirs=("$SOURCE_DIR"/*/)
    shopt -u nullglob

    if [ ${#skill_dirs[@]} -eq 0 ]; then
        echo "Error: no skill directories found in $SOURCE_DIR"
        exit 1
    fi

    for skill_dir in "${skill_dirs[@]}"; do
        # ... existing loop body unchanged ...
    done

    if [ ${#installed[@]} -eq 0 ]; then
        echo "Error: no skill directory contained a SKILL.md"
        exit 1
    fi
fi
```

Then replace the closing summary (lines 199-209):

```bash
echo ""
if [ "$INSTALL_MODE" = "hooks-only" ]; then
    echo "Git hooks installed. Skills were NOT copied to $SKILLS_DIR —"
    echo "Codex, Cursor, Kimi Code and Copilot read .agents/skills/ in this clone."
    echo "Confirm discovery with your agent's skill list (Codex: /skills)."
else
    echo "Installed ${#installed[@]} tax skills to $SKILLS_DIR"
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
```

Note for the implementer: `${#installed[@]}` on an empty array is safe under
`set -u` in Bash 3.2 (macOS), but `"${installed[@]}"` is not — which is why the
`printf` is inside the `else` branch, where the array is guaranteed non-empty.

- [ ] **Step 6: Verify all three installer modes by hand**

```bash
bash agents/install.sh --hooks-only && echo "EXIT=$?"
ls ~/.claude/skills/tax-setup/SKILL.md   # must still resolve from the earlier install
bash agents/install.sh && echo "EXIT=$?"
bash agents/install.sh --bogus; echo "EXIT=$? (expect 1)"
```

Capture full output — do not pipe through `head`, which aborts the script under
`set -o pipefail` and makes a working mode look broken.

- [ ] **Step 7: Run the suite and commit**

```bash
source .venv/bin/activate && python3 -m pytest tests/ -q
git add README.md agents/install.sh tests/test_portability.py
git commit -m "docs+installer: make live web search a setup step, add --hooks-only

The live-search setting is the one configuration error no downstream gate can
catch — a cached bracket yields a wrong-but-plausible return — and it was a
prerequisites bullet. Promoted to a numbered Quick Start step with the exact
config line.

Quick Start also told Codex/Cursor/Kimi users to run install.sh, which populated
~/.claude/skills and printed slash commands those agents do not have. --hooks-only
installs the PII guard alone. Adds the missing statement that the clone itself is
the taxpayer workspace."
```

---

### Task 4: Add "Use when …" trigger clauses to all 20 descriptions

**Files:**
- Modify: all 20 `.agents/skills/*/SKILL.md` (frontmatter `description` only)
- Test: `tests/test_portability.py` (`TestSkillLayout`)

**Interfaces:**
- Consumes: nothing.
- Produces: the `description` trigger invariant that Task 7's spec-limit tests
  sit alongside.

**Why:** the spec and Anthropic's authoring guide both require a description to
state *what the skill does* **and** *when to use it*, with trigger keywords —
because description text is the only thing loaded at startup and is what drives
automatic selection. All 20 skills here state only the what, and they compete for
the same requests behind a shared `tax-` prefix. This is the cheapest
behaviour-changing fix in the plan.

- [ ] **Step 1: Write the failing test**

Add to `TestSkillLayout` in `tests/test_portability.py`:

```python
    def test_every_description_states_when_to_use_the_skill(self):
        """The Agent Skills spec requires a description to cover what the skill
        does AND when to use it. Descriptions are the only text loaded at
        startup, so this is what drives automatic selection among 20 skills that
        all share a `tax-` prefix."""
        missing = []
        for d in _skill_dirs():
            desc = _frontmatter(d / "SKILL.md").get("description", "")
            if "use when" not in desc.lower():
                missing.append(d.name)
        assert not missing, (
            f"descriptions with no trigger clause: {missing}. Add "
            "'Use when ...' naming the situations and words a user would say.")
```

- [ ] **Step 2: Run to verify it fails**

```bash
source .venv/bin/activate && python3 -m pytest tests/test_portability.py -k when_to_use -v
```

Expected: FAIL listing all 20 skills.

- [ ] **Step 3: Rewrite each `description`**

Keep the existing text verbatim and append the trigger sentence. Each result is
well under the 1024-character limit.

| Skill | Append to the existing description |
|---|---|
| `tax-amend` | ` Use when a return has already been filed and a corrected or late document arrives, or the user mentions amending, Form 1040-X, or a corrected 1099 or W-2.` |
| `tax-archive` | ` Use at year-end cutover, when starting a new tax year, or before running tax-setup for the following year.` |
| `tax-calendar` | ` Use when the user asks about tax deadlines, quarterly estimated-payment reminders, or wants a calendar or .ics export.` |
| `tax-compute` | ` Use after documents are parsed, when the user asks to compute, calculate or finalize the return, or when result.json must be produced or refreshed.` |
| `tax-extend` | ` Use when the filing deadline is near and the return is not ready, or the user mentions an extension, Form 4868, or FTB 3519.` |
| `tax-fbar` | ` Use when foreign bank or brokerage accounts exceed the $10,000 aggregate threshold, or the user mentions FBAR, FinCEN 114, or foreign account reporting.` |
| `tax-generate` | ` Use after tax-compute, when the user asks for a PDF, a summary, a filing package, or a printable version of the return.` |
| `tax-harvest` | ` Use when the user asks about harvesting losses, offsetting capital gains, selling losing positions, or year-end portfolio tax moves.` |
| `tax-parse` | ` Use after tax-setup, when source documents are sitting in documents/ and need to become structured summaries, or the user mentions parsing W-2s, 1099s, or statements.` |
| `tax-parse-crypto` | ` Use when the user has crypto exchange CSV exports, or mentions Coinbase, Kraken, Gemini, staking rewards, or the Form 1040 digital-asset question.` |
| `tax-project` | ` Use mid-year, when the user asks what they will owe, whether to adjust withholding, or how to avoid an underpayment penalty.` |
| `tax-ptet` | ` Use when the taxpayer has K-1 pass-through income from a CA, NY or NJ entity, or the user mentions PTET, the SALT cap workaround, or an entity-level state tax election.` |
| `tax-retirement` | ` Use mid-year, when the user asks how much more they can contribute to a 401(k), IRA or HSA, or which account saves the most tax per additional dollar.` |
| `tax-review` | ` Use after tax-compute and ideally after tax-verify, when the user asks for a review, a second opinion, an audit-risk assessment, or missed deductions.` |
| `tax-rollover` | ` Use when starting a new tax year and archive/{year-1}/ exists, or the user mentions carryforwards, prior-year data, or last year's return.` |
| `tax-roth-convert` | ` Use when the user asks whether or how much to convert to a Roth, or mentions a Roth conversion ladder, filling a bracket, or IRMAA.` |
| `tax-scenario` | ` Use when the user asks a what-if question about extra income, a bonus, a conversion or a deduction change and wants the marginal tax effect.` |
| `tax-setup` | ` Use when starting a tax year from scratch, when taxpayer_profile.json or tax_law_<year>.json is missing, or the user mentions setting up, initializing, or beginning a return.` |
| `tax-verify` | ` Use after tax-compute, when the user has a return PDF from tax software and wants a line-by-line comparison against the computed values.` |
| `tax-withholding` | ` Use when the user asks whether enough tax is being withheld, wants to update a W-4, or needs an estimated-payment schedule to close a shortfall.` |

**YAML hazard — quote any description containing `: `.** `tests/test_portability.py`
parses frontmatter with a hand-rolled `partition(":")` that accepts an unquoted
colon happily. Every other agent uses a real YAML parser, where `key: text: more`
is a parse error and the skill vanishes with no message. None of the clauses above
introduces a `: `, but wrap the value in double quotes if you reword one so that
it does. Step 5 pins this.

- [ ] **Step 4: Verify the spec limits still hold**

```bash
for d in .agents/skills/*/; do
  n=$(basename "$d")
  desc=$(awk '/^description:/{sub(/^description: /,""); print; exit}' "$d/SKILL.md")
  echo "$n: ${#desc}"
done | awk -F': ' '$2 > 1024 {print "OVER LIMIT: " $0; f=1} END {if (!f) print "all under 1024 ✓"}'
```

- [ ] **Step 4b: Confirm every SKILL.md parses under a real YAML parser**

The repo's own parser is too permissive to catch the failure mode that matters.
Use the stdlib-free check below rather than adding a `pyyaml` dependency:

```bash
.venv/bin/python - <<'PY'
import re, sys
from pathlib import Path
bad = []
for p in sorted(Path(".agents/skills").glob("*/SKILL.md")):
    block = p.read_text().split("---\n", 2)[1]
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        v = value.strip()
        # An unquoted scalar containing ": " is a YAML parse error.
        if v and v[0] not in "\"'" and ": " in v:
            bad.append(f"{p.parent.name}: unquoted colon in {key.strip()!r}")
print("\n".join(bad) if bad else "all frontmatter is YAML-safe")
sys.exit(1 if bad else 0)
PY
```

- [ ] **Step 5: Run the suite and commit**

```bash
source .venv/bin/activate && python3 -m pytest tests/ -q
git add .agents/skills tests/test_portability.py
git commit -m "feat(skills): give every description a when-to-use trigger clause

Descriptions are the only skill text loaded at startup and are what an agent
matches a request against. All 20 stated what the skill does and none stated
when to use it, which is half of what the Agent Skills spec asks for — and the
half that matters when 20 skills share a tax- prefix and compete for the same
request. Adds trigger situations and the words a user would actually say."
```

---

### Task 5: Documentation truth sweep

**Files:**
- Modify: `docs/contributing/agents-compat.md:112-128`
- Modify: `README.md:198-223` (Project Structure)
- Modify: `docs/contributing/test-invariants.md:9` (count)
- External: GitHub repository description

**Interfaces:** consumes nothing, produces nothing. Pure documentation.

- [ ] **Step 1: Correct the VS Code section**

Replace `docs/contributing/agents-compat.md` lines 117-121 with:

```markdown
- `.agents/skills/` is one of VS Code's **default** project skill locations
  (alongside `.github/skills/` and `.claude/skills/`), so no configuration is
  required. The repository still ships `.vscode/settings.json` pointing
  `chat.agentSkillsLocations` at `.agents/skills`, because it is the only
  discovery path on Windows clones where the `.claude/skills` symlinks do not
  materialise. Harmless where the default already covers it.
```

- [ ] **Step 2: Refresh the README Project Structure block**

Replace `README.md` lines 200-223 with:

```
.agents/
└── skills/           # The skills, one dir per skill: <name>/SKILL.md
                      # (discovered natively by Codex/Cursor/Kimi/Copilot;
                      #  reached by Claude Code via .claude/skills/ symlinks)
agents/
├── templates/        # JSON/MD templates for workspace initialization
├── lib/              # Shared Python utilities
│   ├── parse_utils.py        # PDF/Excel/CSV extraction
│   ├── tax_law_fetcher.py    # Tax law cache management
│   └── tax_computations.py   # Parameterized tax math
└── install.sh        # Git hooks, gitignore, optional global skill install
AGENTS.md             # Project truth for every agent (architecture, security)
CLAUDE.md             # Claude Code specifics; imports AGENTS.md
docs/
├── contributing/     # agents-compat, skill-catalog, test-invariants,
│                     # tax-gotchas, state-module-playbook
├── changelog/        # Per-epic implementation history
├── roadmap/          # Product backlogs
└── superpowers/      # Feature specs and implementation plans
scripts/
└── pre-commit-pii-scan.sh   # File-type blocker + content scanner
tests/
├── test_tax_computations.py     # tax math functions (the largest suite)
└── test_*.py                    # per-module suites (rollover, projection, ptet, …)
.claude/skills/       # Relative symlinks -> ../../.agents/skills/<name>
.vscode/settings.json # Points VS Code Copilot at .agents/skills
.github/
├── workflows/ci.yml  # CI pipeline (pytest + PII scan + pip-audit)
└── dependabot.yml    # Weekly pip dependency monitoring
.pii-allowlist        # False positive suppression for the PII scanners
requirements.txt      # Pinned runtime dependencies
```

- [ ] **Step 3: Reconcile the test count**

```bash
source .venv/bin/activate && python3 -m pytest tests/ -q 2>&1 | tail -1
```

Set `docs/contributing/test-invariants.md` line 9 to the number printed.

- [ ] **Step 4: Update the GitHub repository description**

```bash
gh repo edit yarikmsu/tax-prep-skills \
  --description "Reusable US tax preparation toolkit for AI coding agents — tax-* skills for Codex, Cursor, Kimi Code, Copilot and Claude Code"
```

- [ ] **Step 5: Commit, then open PR 1**

```bash
source .venv/bin/activate && python3 -m pytest tests/ -q
git add README.md docs/contributing/agents-compat.md docs/contributing/test-invariants.md
git commit -m "docs: correct VS Code skill defaults, refresh project structure

VS Code lists .agents/skills as a default project skill location, so the claim
that its absence from the default object made the checked-in settings.json
load-bearing was wrong; the Windows-symlink justification still holds and the
file stays. Project Structure had drifted past AGENTS.md, docs/, requirements.txt
and .vscode/, and still listed the removed hooks.json."
```

Then print the push command for the user. After they confirm:

```bash
gh pr create --title "README accuracy + first-run onboarding" --body "$(cat <<'EOF'
## Summary
- Replaces a PII content scanner that never ran with one that does, host-side
- Documents per-agent invocation syntax (only 1 of 5 agents used the documented form)
- Promotes live web search to a Quick Start step
- Adds `--hooks-only` so non-Claude users are not given a `~/.claude` tree
- Gives every skill description a when-to-use trigger clause
- Corrects the VS Code compatibility note and the Project Structure block

## Security note
`.claude/hooks.json` declared a `PreCommit` event that does not exist, in a file
Claude Code does not read as hook configuration. The README described it as
security layer 3. Neither `scripts/pre-commit-pii-scan.sh` nor CI did any content
scanning, so a full SSN in a tracked `.md` or `.py` was caught by nothing. The
scan now lives in the git hook, where it applies to every agent, and reports file
names and match counts only — never the matched value.

## Test plan
- 8 new behavioural tests for the content scanner
- 5 new README/installer invariants
- Full suite green
EOF
)"
```

---

### Task 6: LICENSE and `license:` frontmatter

> **DECIDED 2026-08-03: Apache-2.0.** Rationale: tax *strategies* are unpatentable
> under America Invents Act §14, but that section explicitly carves out software
> "used solely for preparing a tax or information return" — which is exactly what
> `agents/lib/` is — so the §3 patent grant is not theoretical here. It also
> matches `anthropics/skills` and the Agent Skills spec's own frontmatter example.
>
> **Do not create a `NOTICE` file.** Apache §4(d) only binds redistributors when
> one exists; omitting it removes the sole ongoing obligation on anyone who forks
> a skill. §4(b) ("state significant changes") still applies and is accepted as
> the cost of the patent grant.

**Files:**
- Create: `LICENSE`
- Modify: all 20 `.agents/skills/*/SKILL.md` (add `license:` to frontmatter)
- Modify: `README.md` (add a License section before Disclaimer)
- Test: `tests/test_portability.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_every_skill_declares_a_license(self):
        """The spec's optional license field is the only license signal that
        travels with a skill folder someone copies out of this repo."""
        for d in _skill_dirs():
            fm = _frontmatter(d / "SKILL.md")
            assert fm.get("license"), f"{d.name} declares no license"

    def test_repository_has_a_license_file(self):
        assert (REPO / "LICENSE").is_file()
```

- [ ] **Step 2: Run to verify it fails**, then add the `LICENSE` file with the
      chosen license's standard text (copyright holder: the repository owner;
      year: 2026).

- [ ] **Step 3: Add `license: Apache-2.0` (or `MIT`) as the third frontmatter key
      in each of the 20 `SKILL.md` files**, directly under `description:`.

- [ ] **Step 4: Add a README License section** before `## Disclaimer`:

```markdown
## License

Apache-2.0. See [LICENSE](LICENSE). The disclaimer below is not modified by it.
```

- [ ] **Step 5: Run the suite and commit**

```bash
source .venv/bin/activate && python3 -m pytest tests/ -q
git checkout -b feat/agent-skills-spec-compliance
git add LICENSE README.md .agents/skills tests/test_portability.py
git commit -m "chore: add repository license and per-skill license frontmatter

The README invited contributions with no license file, and a skill folder copied
out of this repo carried no license signal at all. The Agent Skills spec provides
a license frontmatter field for exactly that case."
```

---

### Task 7: Enforce the spec's frontmatter and size limits in tests

**Files:**
- Modify: `tests/test_portability.py` (`TestSkillLayout`)
- Modify: `docs/contributing/test-invariants.md`

**Interfaces:**
- Consumes: `_skill_dirs()` and `_frontmatter()` helpers already in the file.
- Produces: `OVERSIZED` — the shrinking allowlist Task 8 empties.

**Design note — the shrinking allowlist.** Three skills exceed the 500-line limit
today. Asserting the limit unconditionally would fail the suite until Task 8
lands, and marking them `xfail` would let a *fourth* skill drift over unnoticed.
Instead the test carries an explicit exception set and asserts in **both**
directions: every skill outside the set is under the limit, and no skill inside
it is already under the limit. The second assertion is what keeps the list from
going stale — the repo's "no silent caps" principle applied to a test.

- [ ] **Step 1: Write the tests**

Add to `TestSkillLayout`:

```python
    # Skills that exceed the spec's 500-line SKILL.md limit and have not yet been
    # restructured. This set may only SHRINK — see the paired assertion below.
    OVERSIZED = {"tax-compute", "tax-setup", "tax-review"}

    MAX_SKILL_LINES = 500
    MAX_NAME_CHARS = 64
    MAX_DESCRIPTION_CHARS = 1024
    NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

    def test_frontmatter_name_satisfies_the_spec(self):
        for d in _skill_dirs():
            name = _frontmatter(d / "SKILL.md").get("name", "")
            assert len(name) <= self.MAX_NAME_CHARS, f"{d.name}: name too long"
            assert self.NAME_RE.match(name), (
                f"{d.name}: name {name!r} must be lowercase alphanumerics and "
                "single hyphens, with no leading, trailing or doubled hyphen")

    def test_frontmatter_description_within_the_spec_limit(self):
        for d in _skill_dirs():
            desc = _frontmatter(d / "SKILL.md").get("description", "")
            assert 0 < len(desc) <= self.MAX_DESCRIPTION_CHARS, (
                f"{d.name}: description is {len(desc)} chars "
                f"(limit {self.MAX_DESCRIPTION_CHARS})")

    def test_skill_bodies_are_under_the_line_limit(self):
        over = []
        for d in _skill_dirs():
            if d.name in self.OVERSIZED:
                continue
            n = len((d / "SKILL.md").read_text().splitlines())
            if n > self.MAX_SKILL_LINES:
                over.append(f"{d.name} ({n})")
        assert not over, (
            f"SKILL.md over {self.MAX_SKILL_LINES} lines: {over}. Move detail "
            "into references/ and link it one level deep from SKILL.md.")

    def test_the_oversized_allowlist_only_shrinks(self):
        """A skill that has been restructured must leave the allowlist, or the
        list silently readmits regressions."""
        stale = []
        for name in sorted(self.OVERSIZED):
            n = len((SKILLS_DIR / name / "SKILL.md").read_text().splitlines())
            if n <= self.MAX_SKILL_LINES:
                stale.append(f"{name} ({n})")
        assert not stale, (
            f"now under the limit and must be removed from OVERSIZED: {stale}")

    def test_frontmatter_is_parseable_by_a_strict_yaml_parser(self):
        """_frontmatter() splits on the first colon and accepts anything after
        it. Every other agent uses a real YAML parser, where an unquoted scalar
        containing ': ' is a parse error — and a skill that fails to parse
        disappears with no message on exactly the agents this repo has no CI for."""
        bad = []
        for d in _skill_dirs():
            block = (d / "SKILL.md").read_text().split("---\n", 2)[1]
            for line in block.splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                key, _, value = line.partition(":")
                v = value.strip()
                if v and v[0] not in "\"'" and ": " in v:
                    bad.append(f"{d.name}: unquoted colon in {key.strip()!r}")
        assert not bad, f"frontmatter would fail a strict YAML parser: {bad}"

    def test_reference_files_are_one_level_deep(self):
        """Agents partially read files reached through nested references, so
        every reference must link directly from SKILL.md."""
        for d in _skill_dirs():
            refs = d / "references"
            if not refs.is_dir():
                continue
            body = (d / "SKILL.md").read_text()
            for f in sorted(refs.glob("*.md")):
                assert f"references/{f.name}" in body, (
                    f"{d.name}/references/{f.name} is not linked from SKILL.md")
```

Add `import re` at the top of the file if it is not already imported.

- [ ] **Step 2: Run to verify the whole class passes**

```bash
source .venv/bin/activate && python3 -m pytest tests/test_portability.py -v
```

Expected: all PASS. The size test is scoped around the three known offenders and
the allowlist test confirms all three genuinely are still over.

- [ ] **Step 3: Record the invariants and commit**

Add to `docs/contributing/test-invariants.md`:

```markdown
- `test_portability.py::TestSkillLayout` also pins the Agent Skills spec limits:
  `name` ≤64 chars matching `^[a-z0-9]+(-[a-z0-9]+)*$`, `description` 1-1024
  chars containing a "Use when" trigger, `SKILL.md` ≤500 lines, and every
  `references/*.md` linked directly from `SKILL.md`. `OVERSIZED` lists the skills
  not yet restructured; a paired test fails if an entry is already under the
  limit, so the list can only shrink.
```

```bash
source .venv/bin/activate && python3 -m pytest tests/ -q
git add tests/test_portability.py docs/contributing/test-invariants.md
git commit -m "test: pin the Agent Skills spec limits on every skill

Frontmatter constraints were partly enforced (name matches directory,
description non-empty) but the spec's own limits were not. Adds them, plus the
500-line body limit with a shrink-only allowlist for the three skills that
exceed it, so a fourth cannot drift over unnoticed."
```

---

### Task 8: Progressive disclosure for the three oversized skills

**Files:**
- Modify: `.agents/skills/tax-compute/SKILL.md` (1603 → ~200 lines)
- Create: `.agents/skills/tax-compute/references/{income-and-agi,tax-and-credits,state-return,international-and-optimization,finalize}.md`
- Modify: `.agents/skills/tax-setup/SKILL.md` (938 → ~200 lines)
- Create: `.agents/skills/tax-setup/references/{interview.md,tax-law-fetch.md}`
- Modify: `.agents/skills/tax-review/SKILL.md` (546 → ~460 lines)
- Create: `.agents/skills/tax-review/references/report-templates.md`
- Modify: `tests/test_portability.py` (empty `OVERSIZED`)

**Interfaces:**
- Consumes: `OVERSIZED` and `test_reference_files_are_one_level_deep` from Task 7.
- Produces: nothing downstream.

**Hard constraint — gates never move.** `tax-compute` Step 0.5 (tax law
freshness), Step 0.7 (capability gate) and Step 12.7 (tie-out HARD GATE) stay in
`SKILL.md` verbatim, as does `tax-setup`'s freshness/STOP rule. A gate the agent
can decline to load is a gate that silently over-states completeness — the exact
bias-direction error `AGENTS.md` §4 forbids.

**Verification method — deterministic relocation audit.** Do not have an agent
"check nothing was lost." Extract every backticked identifier, `§` citation,
`Form NNNN` reference and `$N` figure from the pre-split file, then assert each
still appears somewhere in the post-split tree. This technique found three real
losses in the PR #66 relocation that an agent-based audit missed. The script is
in Step 2.

- [ ] **Step 1: Record the pre-split baseline**

```bash
mkdir -p /tmp/split-audit
for s in tax-compute tax-setup tax-review; do
  cp ".agents/skills/$s/SKILL.md" "/tmp/split-audit/$s.before.md"
done
wc -l /tmp/split-audit/*.before.md
```

- [ ] **Step 2: Write the audit script**

Create `/tmp/split-audit/audit.py`:

```python
"""Assert every atom of a relocated skill survives somewhere in its new tree."""
import re
import sys
from pathlib import Path

CATEGORIES = {
    "backticked": re.compile(r"`([^`\n]{2,60})`"),
    "statute":    re.compile(r"§+\s*[0-9A-Za-z().\-]+"),
    "form":       re.compile(r"\bForm\s+[0-9]{3,4}[A-Z\-]*\b"),
    "money":      re.compile(r"\$[0-9][0-9,]*(?:\.[0-9]{2})?"),
}


def main(before: Path, after_dir: Path) -> int:
    original = before.read_text()
    combined = "\n".join(p.read_text() for p in sorted(after_dir.rglob("*.md")))
    failed = 0
    for label, pattern in CATEGORIES.items():
        atoms = {m if isinstance(m, str) else m[0]
                 for m in pattern.findall(original)}
        missing = sorted(a for a in atoms if a not in combined)
        print(f"{label}: {len(atoms)} atoms, {len(missing)} missing")
        for a in missing:
            print(f"    MISSING: {a!r}")
        # Statutes and form numbers going to zero is the strong completeness
        # signal. Backtick/money misses are usually tokenization artifacts
        # ("$400," vs "$400") and must be eyeballed individually, not ignored.
        if label in ("statute", "form") and missing:
            failed = 1
    return failed


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
```

- [ ] **Step 3: Split `tax-compute`**

Read `.agents/skills/tax-compute/SKILL.md` in full before cutting — the H2/H3
line numbers below are from the pre-split file and one H2
(`## Optimization Opportunities Found`, line 1540) sits *inside* the Step 11.5
range, so the boundaries need eyes, not arithmetic.

`SKILL.md` keeps, in this order:

1. Frontmatter (with the Task 4 description)
2. `## Usage` and `## Prerequisites` (lines 10-21) verbatim
3. `### Step 0.5: Tax Law Freshness Verification` (26-45) verbatim
4. `### Step 0.7: Capability Gate (scope guard)` (46-69) verbatim
5. A new `## Computation Order` containing one line per step and its file:

```markdown
## Computation Order

Run the steps in order. Read each reference file when you reach its steps; do not
read them all up front.

| Steps | What they cover | Read |
|---|---|---|
| 1 – 5 | Load inputs, confidence check, applicable forms, income, AGI, taxable income (1040 lines 1-15) | [references/income-and-agi.md](references/income-and-agi.md) |
| 6 – 9.5 | Tax, AMT, credits, other taxes, payments, estimated-tax penalty (1040 lines 16-38) | [references/tax-and-credits.md](references/tax-and-credits.md) |
| 10 | State return | [references/state-return.md](references/state-return.md) |
| 11 – 11.5 | International compliance and the optimization discovery engine | [references/international-and-optimization.md](references/international-and-optimization.md) |
| 12, 13 | Write result files and produce the summary | [references/finalize.md](references/finalize.md) |

Steps 0.5, 0.7 and 12.7 are gates and are written out in full in this file. Never
skip one because a reference file did not mention it.
```

6. `### Step 12.7: Parse-to-return tie-out (HARD GATE)` (1569-1592) verbatim,
   under a `## Gates that run regardless` heading

Each reference file starts with the frontmatter-free heading and a table of
contents (required for reference files over 100 lines), then the verbatim step
text. Body content is moved, never rewritten.

- [ ] **Step 4: Audit the `tax-compute` split**

```bash
.venv/bin/python /tmp/split-audit/audit.py \
  /tmp/split-audit/tax-compute.before.md .agents/skills/tax-compute
echo "EXIT=$?  (0 required)"
```

Eyeball every reported `backticked` and `money` miss individually. Exit 1 on any
`statute` or `form` miss is a hard stop — restore the atom before continuing.

- [ ] **Step 5: Split `tax-setup` the same way**

`SKILL.md` keeps `## Usage`, `## Prerequisites`, the tax-law freshness contract
and its STOP rule, `## Notes` (934-938), and a step map pointing at:
- `references/interview.md` — the taxpayer interview questions
- `references/tax-law-fetch.md` — the fetch, cache-write and source-pinning steps

Every capability-gate flag must still be asked by the interview — a test already
enforces that the flags in `capabilities.py` are covered by `tax-setup`. Run the
suite after the split and confirm that test still passes; if the interview text
moved into a reference file the test may need its path updated, and updating the
path is correct, weakening the assertion is not.

- [ ] **Step 6: Split `tax-review`**

Move the five output-template sections (lines 459-546: IRS Officer Perspective,
EA Perspective, YoY What-Changed, PTET Election Opportunity, No-Income-Tax State
Completeness) into `references/report-templates.md`, and link it from the
`## Steps` section where the report is produced. Target ~460 lines.

- [ ] **Step 7: Audit the remaining two splits**

```bash
for s in tax-setup tax-review; do
  echo "=== $s ==="
  .venv/bin/python /tmp/split-audit/audit.py \
    "/tmp/split-audit/$s.before.md" ".agents/skills/$s"
done
```

- [ ] **Step 8: Empty the allowlist**

In `tests/test_portability.py`, change `OVERSIZED = {"tax-compute", "tax-setup", "tax-review"}`
to `OVERSIZED: set[str] = set()`.

- [ ] **Step 9: Run the suite**

```bash
source .venv/bin/activate && python3 -m pytest tests/ -q
wc -l .agents/skills/*/SKILL.md | sort -rn | head -5
```

Expected: all green, and no `SKILL.md` over 500 lines.

- [ ] **Step 10: Behavioural check before committing**

The audit proves no text was lost. It does not prove an agent can still follow
the skill. In a fresh session, run `tax-compute` against the existing workspace
and confirm the agent reads the right reference file at the right step and does
not skip Step 0.7 or 12.7. This is the only test of the thing being changed.

- [ ] **Step 11: Commit**

```bash
git add .agents/skills tests/test_portability.py
git commit -m "refactor(skills): progressive disclosure for the three oversized skills

tax-compute was 1603 lines, tax-setup 938 and tax-review 546, against a 500-line
limit that exists because the whole body loads at once when the skill activates.
Splits the step detail into references/ linked one level deep, keeping the
freshness, capability and tie-out gates in the always-loaded body — a gate the
agent can decline to load is a gate that silently over-states completeness.

Text was moved verbatim and verified with an atom-survival audit over backticked
identifiers, statute citations, form numbers and dollar figures."
```

- [ ] **Step 12: Print the push command, then open PR 2 after the user confirms**

---

## Open questions

1. **License choice** (blocks Task 6 only). Recommendation: Apache-2.0.
2. **`skills-ref validate` in CI.** The spec ships a validator. Task 7
   reimplements its checks in pytest instead, to avoid adding a dependency to a
   repo that pins everything for supply-chain safety. Revisit if the validator
   later ships as a pinned Python package.
3. **Plugin packaging** (`.claude-plugin/marketplace.json`) is deliberately out of
   scope. It would give Claude Code users a one-command install and a legitimate
   home for hooks, but the plugin cache does not carry `agents/lib/` or the
   workspace, so users would still need the clone. Worth a separate spec.
4. **Skill self-containment.** `agents/lib/*.py` are effectively the skills'
   `scripts/` but live outside the skill folders, so no single skill directory is
   portable on its own. Symlinking `scripts/` into each skill would close it.
   Out of scope here.
