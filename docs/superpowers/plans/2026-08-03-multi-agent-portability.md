# Multi-Agent Portability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the toolkit usable zero-install by Codex / Cursor / Kimi Code / GitHub Copilot users who clone this repo, while keeping Claude Code fully working — by moving skills to the convergent `.agents/skills/<name>/SKILL.md` layout, splitting project truth into an agent-neutral `AGENTS.md`, and neutralizing Claude-specific tool names in skill bodies.

**Architecture:** No Python behavior changes. Three structural moves: (1) skills relocate to `.agents/skills/<name>/SKILL.md` with tracked RELATIVE `.claude/skills/<name>` directory symlinks for Claude/Cursor double-coverage; (2) `AGENTS.md` (new, agent-neutral, security-first ordering) + thinned `CLAUDE.md` that `@AGENTS.md`-imports it, with deep reference content relocated to `docs/contributing/` rather than inlined; (3) a body-only rewrite pass over 20 skills replacing Claude tool names with capability descriptions plus explicit degrade paths. A new `tests/test_portability.py` doc-sync suite makes every structural invariant machine-checked.

**Tech Stack:** Python 3.10+ stdlib, pytest, Bash (install.sh), Markdown (skills/docs), JSON (VS Code settings).

**Spec:** `docs/superpowers/specs/2026-08-02-multi-agent-portability-design.md` (approved 2026-08-02; §-references below point into it).
**Backlog:** `docs/roadmap/2026-multi-agent-portability.md` (Epic Q).

## Baseline (verified 2026-08-03 against `origin/main` = `90b69aa`, post-PR-#65)

- 20 skills at `agents/skills/tax-*.md`; suite **2077 passing**.
- `.claude/skills/` holds **7 git-tracked symlinks** pointing at ABSOLUTE machine
  paths (`/Users/user/Downloads/...`) — broken on every other clone — and in the
  legacy FLAT `<name>.md` shape rather than the documented `<name>/SKILL.md` project
  layout. Both defects die with this change.
- `CLAUDE.md` = **430 lines / 65 KB**. `AGENTS.md`, `.agents/`, `.vscode/` do not exist.
- **12 doc-sync assertions across 3 test files** read skill markdown by path
  (`test_capabilities.py` ×3 → tax-setup; `test_tax_computations.py` ×7 → tax-setup /
  tax-compute / tax-parse; `test_tie_out.py` ×2 → tax-parse). All break on the move.
- Neutralization targets: WebSearch/WebFetch in 7 skills (23 hits), AskUserQuestion in
  8 skills (12 hits), subagent/Task in 3 skills (12 hits), bare "Claude" 7 lines / 4
  files (tax-calendar 2, tax-parse 2, tax-ptet 1, tax-retirement 2).

## Spec corrections adopted by this plan

Three spec claims did not survive verification against the current tree. Each
correction is binding on the task that touches it:

1. **`.vscode/` is gitignored** (`.gitignore:44`), so spec §3's checked-in
   `.vscode/settings.json` cannot be committed as written. Git cannot re-include a
   file under an excluded *directory*, so a bare `!.vscode/settings.json` silently
   fails. Fix uses the idiom already at `.gitignore:7-8`: change the rule to
   `.vscode/*` (contents, not directory) then negate the one file. **Task 2.**
2. **There is NO `.mcp.json` in this repo.** Spec §6 ("the SAME `.mcp.json` that
   already serves Claude + Kimi") and §9 are wrong. The compat page MUST NOT claim
   the file exists; it documents where each agent *would* look, flagged as
   not-yet-shipped. **Task 5.**
3. **`README.md:185` references the old skills path** and is absent from spec §3's
   update list. Added to the Task 1 reference sweep.

## Global Constraints

Every task's requirements implicitly include this section.

- **No attribution, ever.** No commit message, PR title/body, doc, or code comment may
  mention Claude, Claude Code, or any AI assistant as an author. (Naming Claude Code as
  a *supported target agent* in user-facing compatibility docs is required and correct —
  that is product content, not attribution.)
- **No Python behavior change** (spec §9). The only Python edits allowed are the
  `tax_law_fetcher.py` cosmetic docstring/output strings in Task 4 and the test-path
  updates in Task 1.
- **Suite stays green.** Baseline 2077; every task ends with the full suite passing.
  Run as: `source /path/to/tax-prep-skills/.venv/bin/activate && python3 -m pytest tests/ -q`
- **NEVER `pip install`** — the pip index is unreachable. The existing `.venv`
  has everything needed.
- **Feature branch only.** `feat/multi-agent-portability`. Never commit to `main`.
  Never `git push` without `--no-verify` (pre-push hook blocks otherwise).
- **`AGENTS.md` ≤ 28 KB**, hard-guarded by test. Codex silently truncates the combined
  instruction chain at 32 KiB; Copilot CLI has an open truncation bug (#2111, ~160
  lines observed).
- **Security-first ordering** in `AGENTS.md`: the PII/SSN/chmod-600 block and the
  tax-law-freshness contract must appear within the first 100 lines, machine-checked.
- **`CLAUDE.md` stays additive-only** relative to `AGENTS.md` — Copilot ingests BOTH
  and neither suppresses the other, so nothing in CLAUDE.md may contradict or restate
  AGENTS.md.
- **Preserve skill frontmatter verbatim.** `name` must equal the containing directory
  name; `description` must stay non-empty (all four target agents key discovery on it).
- **Conventional commits**, one commit per task group.

---

### Task 1: Relocate skills to `.agents/skills/<name>/SKILL.md` + fix symlinks + sweep references

The load-bearing move. Nothing else in the plan can land first: Tasks 3–5 all read or
describe the new paths. This task changes **paths only** — not one byte of skill prose
(that is Task 4). Keeping the move content-free is what makes its 12 doc-sync
assertions a real regression check rather than a rewrite.

**Files:**
- Move (20, `git mv`): `agents/skills/tax-<x>.md` → `.agents/skills/tax-<x>/SKILL.md`
  for: amend, archive, calendar, compute, extend, fbar, generate, harvest, parse,
  parse-crypto, project, ptet, retirement, review, rollover, roth-convert, scenario,
  setup, verify, withholding
- Delete: the 7 tracked absolute symlinks `.claude/skills/tax-{compute,fbar,generate,parse,review,setup,verify}.md`
- Create: 20 relative symlinks `.claude/skills/<name>` → `../../.agents/skills/<name>`
- Create: `tests/test_portability.py` (frontmatter + symlink groups only; later tasks append)
- Modify: `agents/install.sh` (source dir, glob, stale 15-of-20 listing, `--copy` flag, other-agents note)
- Modify: `tests/test_capabilities.py` (3 paths), `tests/test_tax_computations.py` (7 paths), `tests/test_tie_out.py` (2 paths)
- Modify: `docs/contributing/state-module-playbook.md` (4 refs), `README.md:185`, `CLAUDE.md` (directory map + install section)

**Interfaces:**
- Produces: the canonical path `.agents/skills/<name>/SKILL.md` — every later task
  references skills by this path. Produces `tests/test_portability.py` with module-level
  constants `REPO`, `SKILLS_DIR = REPO / ".agents/skills"`, `CLAUDE_SKILLS = REPO / ".claude/skills"`;
  Tasks 2–4 append test classes to this file and reuse those constants.
- Consumes: nothing.

- [ ] **Step 1: Write the failing structural tests**

Create `tests/test_portability.py`:

```python
"""Portability doc-sync suite — machine-checks the multi-agent layout invariants.

Every assertion here guards a claim made to users of a NON-Claude agent, where we
have no CI coverage (no vendor CLI licenses in CI, spec §9). A silently-broken
symlink or a dropped frontmatter field would make the toolkit undiscoverable on
that agent with no other signal.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO / ".agents" / "skills"
CLAUDE_SKILLS = REPO / ".claude" / "skills"

EXPECTED_SKILLS = {
    "tax-amend", "tax-archive", "tax-calendar", "tax-compute", "tax-extend",
    "tax-fbar", "tax-generate", "tax-harvest", "tax-parse", "tax-parse-crypto",
    "tax-project", "tax-ptet", "tax-retirement", "tax-review", "tax-rollover",
    "tax-roth-convert", "tax-scenario", "tax-setup", "tax-verify", "tax-withholding",
}


def _skill_dirs():
    return sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir())


def _frontmatter(skill_md: Path) -> dict:
    """Parse the leading `---` YAML block without a yaml dependency.

    Only flat `key: value` pairs are used by any target agent's skill loader, so a
    hand parser is sufficient AND keeps the test dependency-free.
    """
    text = skill_md.read_text()
    assert text.startswith("---\n"), f"{skill_md} has no frontmatter block"
    _, block, _ = text.split("---\n", 2)
    out = {}
    for line in block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class TestSkillLayout:
    def test_all_expected_skills_present(self):
        assert {p.name for p in _skill_dirs()} == EXPECTED_SKILLS

    def test_every_skill_has_skill_md(self):
        for d in _skill_dirs():
            assert (d / "SKILL.md").is_file(), f"{d.name} missing SKILL.md"

    def test_frontmatter_name_matches_directory(self):
        for d in _skill_dirs():
            fm = _frontmatter(d / "SKILL.md")
            assert fm.get("name") == d.name, (
                f"{d.name}/SKILL.md frontmatter name={fm.get('name')!r} must equal "
                "its directory name — all four target agents key discovery on it")

    def test_frontmatter_description_non_empty(self):
        for d in _skill_dirs():
            fm = _frontmatter(d / "SKILL.md")
            assert fm.get("description"), f"{d.name} has empty description"

    def test_old_flat_location_is_gone(self):
        assert not (REPO / "agents" / "skills").exists(), (
            "agents/skills/ must not survive the move — a stale copy would drift "
            "silently from the canonical .agents/skills/ tree")


class TestClaudeSymlinks:
    def test_one_symlink_per_skill(self):
        assert {p.name for p in CLAUDE_SKILLS.iterdir()} == EXPECTED_SKILLS

    def test_symlinks_are_relative_and_resolve_in_repo(self):
        for link in sorted(CLAUDE_SKILLS.iterdir()):
            assert link.is_symlink(), f"{link} must be a symlink"
            target = Path(link.readlink())
            assert not target.is_absolute(), (
                f"{link.name} points at an absolute path ({target}) — that breaks on "
                "every clone but the machine it was created on")
            resolved = link.resolve()
            assert resolved == (SKILLS_DIR / link.name).resolve(), (
                f"{link.name} resolves to {resolved}, not its .agents/skills twin")
            assert (resolved / "SKILL.md").is_file()
```

- [ ] **Step 2: Run to verify it fails**

Run: `source /path/to/tax-prep-skills/.venv/bin/activate && python3 -m pytest tests/test_portability.py -q`
Expected: FAIL — `.agents/skills` does not exist (collection/attribute errors are fine at this stage).

- [ ] **Step 3: Move the 20 skills**

```bash
mkdir -p .agents/skills
for f in agents/skills/tax-*.md; do
  n=$(basename "$f" .md)
  mkdir -p ".agents/skills/$n"
  git mv "$f" ".agents/skills/$n/SKILL.md"
done
rmdir agents/skills
```

Verify content is byte-identical to the pre-move blobs (no accidental rewrite):

```bash
git diff --cached --stat -M | tail -3   # expect 20 renames, 0 content changes
```

- [ ] **Step 4: Replace the broken symlinks with relative directory symlinks**

```bash
git rm -q .claude/skills/*.md
for n in $(ls .agents/skills); do
  ln -s "../../.agents/skills/$n" ".claude/skills/$n"
done
git add .claude/skills
```

Verify none are absolute: `find .claude/skills -type l -exec readlink {} \; | grep '^/' && echo BROKEN || echo OK`

- [ ] **Step 5: Update `agents/install.sh`**

Four changes:
1. `SOURCE_DIR="$SCRIPT_DIR/skills"` → `SOURCE_DIR="$WORKSPACE_ROOT/.agents/skills"`
   (note `WORKSPACE_ROOT` is computed *after* `SOURCE_DIR` today — reorder so the
   assignment is valid).
2. Glob `"$SOURCE_DIR"/tax-*.md` → the per-directory form; link
   `$SKILLS_DIR/$name/SKILL.md` → `$SOURCE_DIR/$name/SKILL.md`.
3. Add `--copy` flag: when passed, `cp` the SKILL.md instead of `ln -sf` (Windows
   clones without developer mode cannot create symlinks — spec §3).
4. Replace the hardcoded 15-skill trailing `echo` with a generated list from the
   discovered directories, and append:
   `echo "Other agents (Codex / Cursor / Kimi / Copilot): no install needed — they discover .agents/skills/ automatically."`

- [ ] **Step 6: Sweep the 12 doc-sync test paths**

In `tests/test_capabilities.py`, `tests/test_tax_computations.py`, `tests/test_tie_out.py`
replace every `"agents/skills/tax-<x>.md"` with `".agents/skills/tax-<x>/SKILL.md"`.
Update the assertion *messages* that name the old path too — a message quoting a path
that no longer exists sends the next reader to a dead file.

- [ ] **Step 7: Sweep the live doc references**

- `docs/contributing/state-module-playbook.md` — 4 refs (lines ~97, 139, 154, 155)
- `README.md:185` — directory map
- `CLAUDE.md` — directory map (~line 234) and the Installation paragraph (~line 288)

Historical `docs/superpowers/specs/*` and `docs/superpowers/plans/*` are point-in-time
records and are **NOT** rewritten — they correctly describe the layout as it was when
written. (This plan and the portability spec are the exception: they describe the new
layout by design.)

- [ ] **Step 8: Run the full suite**

Run: `source /path/to/tax-prep-skills/.venv/bin/activate && python3 -m pytest tests/ -q`
Expected: PASS, 2077 + 7 new = 2084.

- [ ] **Step 9: Verify install.sh actually works**

```bash
bash agents/install.sh 2>&1 | tail -5
ls -la ~/.claude/skills/tax-setup/SKILL.md
```
Expected: 20 skills linked; the symlink resolves to `.agents/skills/tax-setup/SKILL.md`.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor(skills): relocate to .agents/skills/<name>/SKILL.md for multi-agent discovery

Codex, Cursor, Kimi Code and Copilot all natively scan .agents/skills/<name>/SKILL.md;
Claude Code reaches the same files through tracked RELATIVE .claude/skills symlinks.
Replaces 7 absolute-path tracked symlinks that resolved only on their author's machine
and covered a legacy 7-of-20 subset."
```

---

### Task 2: Checked-in VS Code settings + gitignore negation

Small and independent, but it must exist before the compat page (Task 5) can point at
it. Copilot's VS Code skills loader lists `.agents/skills/` in its docs while the
`chat.agentSkillsLocations` *setting default* omits it — the workspace setting removes
the ambiguity and is also the only discovery path on Windows clones where the
`.claude/skills` symlinks do not materialize.

**Files:**
- Create: `.vscode/settings.json`
- Modify: `.gitignore` (line 44)
- Modify: `tests/test_portability.py` (append `TestVSCodeSettings`)

**Interfaces:**
- Consumes: `SKILLS_DIR` / `REPO` constants from Task 1.
- Produces: nothing consumed by later code; Task 5's compat page *describes* this file.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_portability.py`:

```python
class TestVSCodeSettings:
    """Copilot in VS Code reads .agents/skills only if the workspace says so.

    The skills doc and loader both list the path, but the SETTING's default object
    omits it (verified 2026-08) — so without this checked-in file, a Copilot user
    silently sees zero skills.
    """

    def test_settings_file_is_tracked_and_maps_agents_skills(self):
        import json
        import subprocess

        settings = REPO / ".vscode" / "settings.json"
        assert settings.is_file(), ".vscode/settings.json must be checked in"
        cfg = json.loads(settings.read_text())
        locations = cfg.get("chat.agentSkillsLocations", {})
        assert locations.get(".agents/skills") is True, (
            "chat.agentSkillsLocations must map '.agents/skills' to true")
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".vscode/settings.json"],
            cwd=REPO, capture_output=True, text=True)
        assert tracked.returncode == 0, (
            ".vscode/settings.json exists but is NOT git-tracked — .gitignore's "
            ".vscode rule must exclude directory CONTENTS (.vscode/*) so the "
            "negation can re-include this one file")
```

- [ ] **Step 2: Run to verify it fails**

Run: `source /path/to/tax-prep-skills/.venv/bin/activate && python3 -m pytest tests/test_portability.py::TestVSCodeSettings -q`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Fix the gitignore rule**

Replace `.gitignore` line 44 (`.vscode/`) with:

```
.vscode/*
!.vscode/settings.json
```

The trailing-slash form excludes the directory itself, so git never descends into it
and a negation can never match. Excluding the *contents* keeps the directory visible.
This mirrors the `documents/*` + `!documents/.gitkeep` pair already at lines 7-8.

- [ ] **Step 4: Create the settings file**

```json
{
  "chat.agentSkillsLocations": {
    ".agents/skills": true
  }
}
```

- [ ] **Step 5: Verify tracked, then run tests**

```bash
git add -f .vscode/settings.json 2>/dev/null; git add .gitignore .vscode/settings.json
git check-ignore -v .vscode/settings.json && echo "STILL IGNORED - FIX" || echo OK
source /path/to/tax-prep-skills/.venv/bin/activate && python3 -m pytest tests/test_portability.py -q
```
Expected: `OK`, tests PASS.

- [ ] **Step 6: Commit**

```bash
git commit -m "build(vscode): check in chat.agentSkillsLocations for Copilot skill discovery

VS Code's skills loader documents .agents/skills but the setting default omits it, so
Copilot users see no skills without a workspace setting. Narrows the .vscode gitignore
rule to directory contents so this one file can be tracked."
```

---

### Task 3: `AGENTS.md` + thinned `CLAUDE.md`

The judgment-heavy task. `CLAUDE.md` is 430 lines / 65 KB of which roughly 91% is
agent-neutral, and `AGENTS.md` has a hard 28 KB ceiling. Compression therefore requires
*relocating* the deep reference blocks (the per-invariant testing inventory and the
per-skill design narratives) into `docs/contributing/`, not deleting them. Losing a
load-bearing tax invariant here would be the single most damaging outcome in this plan;
the review gate for this task is explicitly "no load-bearing content lost, only moved."

**Files:**
- Create: `AGENTS.md`
- Create: `docs/contributing/test-invariants.md` (receives the CLAUDE.md "Testing" inventory verbatim)
- Create: `docs/contributing/skill-catalog.md` (receives the long per-skill design narratives verbatim)
- Modify: `CLAUDE.md` (thin to ≤ 150 lines, `@AGENTS.md` first content line)
- Modify: `tests/test_portability.py` (append `TestInstructionFiles`)

**Interfaces:**
- Consumes: Task 1's `.agents/skills/` paths (AGENTS.md's directory map must show the new layout).
- Produces: `AGENTS.md` as the agent-neutral source of truth that Task 5's compat page links back to.

**AGENTS.md required section order** (security first — Codex truncates the combined
chain silently at 32 KiB and Copilot CLI truncates at ~160 lines, so anything that
must survive truncation goes first):

1. What this is + the hard security rules (never store full SSNs — last 4 only;
   `chmod 600` every PII/financial artifact; runtime files are gitignored and
   local-only; **never run a return on a vendor cloud VM** — Cursor Cloud/Background
   Agents snapshot for 90 days; never paste taxpayer data into GitHub issues or PR
   text because that text is agent input for Copilot's cloud agent; individual-plan
   Copilot contributors must opt out of model training, default-ON since 2026-04-24)
2. The tax-law freshness contract (always fetch current law; never trust cached or
   stale web results; Codex users must set `web_search = "live"`; if live web access
   is unavailable, STOP and ask the user to paste current figures with a source URL)
3. Pipeline diagram + how to invoke a skill on each agent
4. Directory map + runtime files
5. Tooling / venv / test commands
6. Key design principles (bias-direction taxonomy, capability gate, tie-out) — pointers
7. Pointers to deep docs (changelog, playbook, roadmap, test-invariants, skill-catalog)

**CLAUDE.md keeps ONLY:** the `@AGENTS.md` import line, the superpowers spec/plan
workflow paths, Codex-PR-review conventions, and Claude-session git rules (never push;
print the command). Everything else moves. It must remain additive — no restatement of
AGENTS.md content, since Copilot reads both files together.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_portability.py`:

```python
class TestInstructionFiles:
    AGENTS_MAX_BYTES = 28 * 1024   # Codex truncates the COMBINED chain at 32 KiB

    def test_agents_md_exists_and_under_size_cap(self):
        agents = REPO / "AGENTS.md"
        assert agents.is_file()
        size = agents.stat().st_size
        assert size <= self.AGENTS_MAX_BYTES, (
            f"AGENTS.md is {size} bytes, over the {self.AGENTS_MAX_BYTES} cap — Codex "
            "silently truncates the combined instruction chain at 32 KiB, so content "
            "past the cap is invisible with no error")

    def test_claude_md_imports_agents_md_as_first_content_line(self):
        lines = [ln.strip() for ln in (REPO / "CLAUDE.md").read_text().splitlines()]
        content = [ln for ln in lines if ln and not ln.startswith("#")]
        assert content and content[0] == "@AGENTS.md", (
            "CLAUDE.md's first content line must be the @AGENTS.md import")

    def test_claude_md_is_thin(self):
        n = len((REPO / "CLAUDE.md").read_text().splitlines())
        assert n <= 150, (
            f"CLAUDE.md is {n} lines — it must hold only Claude-specific content; "
            "agent-neutral project truth belongs in AGENTS.md")

    def test_security_rules_are_front_loaded(self):
        """Truncation must degrade gracefully: security survives, conveniences die."""
        head = "\n".join((REPO / "AGENTS.md").read_text().splitlines()[:100]).lower()
        for needle in ("ssn", "chmod 600", "never"):
            assert needle in head, (
                f"{needle!r} missing from AGENTS.md's first 100 lines — Copilot CLI "
                "truncates instruction files around 160 lines (bug #2111), so the "
                "security block must come first")

    def test_tax_law_freshness_contract_present(self):
        head = "\n".join((REPO / "AGENTS.md").read_text().splitlines()[:100]).lower()
        assert "web_search" in head or "live" in head, (
            "the tax-law freshness contract must be in the first screenful — a "
            "cached-web-result default silently produces prior-year figures")
```

- [ ] **Step 2: Run to verify it fails**

Run: `source /path/to/tax-prep-skills/.venv/bin/activate && python3 -m pytest tests/test_portability.py::TestInstructionFiles -q`
Expected: FAIL — `AGENTS.md` does not exist.

- [ ] **Step 3: Relocate the deep reference blocks verbatim**

Move (do not rewrite) the CLAUDE.md "### Testing" invariants inventory into
`docs/contributing/test-invariants.md`, and the long per-skill design narratives from
the "## Architecture" section into `docs/contributing/skill-catalog.md`. Each new file
opens with a one-line statement of what it holds and where it came from. Verbatim move
first, in its own commit, so the later distillation diff is reviewable.

- [ ] **Step 4: Write AGENTS.md in the mandated section order**

Distill from CLAUDE.md. Deep content is POINTED at, never inlined. Keep under 28 KB.

- [ ] **Step 5: Thin CLAUDE.md to ≤150 lines with `@AGENTS.md` first**

- [ ] **Step 6: Verify no load-bearing content was lost**

For every heading present in the pre-change CLAUDE.md, confirm the content now lives in
exactly one of: AGENTS.md, test-invariants.md, skill-catalog.md, or an existing doc that
AGENTS.md points to. Record the mapping in the task report. Anything unaccounted for is
a defect, not a simplification.

- [ ] **Step 7: Run the full suite**

Expected: PASS (2084 + 5 = 2089).

- [ ] **Step 8: Commit**

```bash
git commit -m "docs: split project truth into agent-neutral AGENTS.md + thin CLAUDE.md

All four target agents read AGENTS.md natively. Security rules and the tax-law
freshness contract lead the file so that Codex's silent 32 KiB truncation and Copilot
CLI's ~160-line truncation drop conveniences rather than safety rules. Deep reference
content moves to docs/contributing/ and is linked, not inlined."
```

---

### Task 4: Skill neutralization pass

Body-only rewrite of all 20 skills so a non-Claude agent can execute them. Paths are
already correct after Task 1; this task changes **prose only**. The danger is the 12
doc-sync assertions from Task 1 plus the capability-flag and tie-out-category greps:
they assert on literal identifiers inside skill text, so a careless reflow can turn a
green test red — or worse, keep it green while deleting the sentence a user needed.
Run the full suite, not just `test_portability.py`.

**Files:**
- Modify: all 20 `.agents/skills/*/SKILL.md`
- Modify: `agents/lib/tax_law_fetcher.py` (docstring/output strings only, no behavior)
- Modify: `tests/test_portability.py` (append `TestNeutralizationLint`)

**Interfaces:**
- Consumes: Task 1's paths.
- Produces: skill bodies free of Claude-only tool names — Task 5's compat page assumes
  the skills already carry generic phrasing plus degrade paths.

**The six rewrite rules (spec §5), applied verbatim:**

1. **Web:** "use WebSearch" → "search the web (live results required — tax figures
   change annually; if your agent's web access is unavailable or cached-only, STOP and
   ask the user to paste the current figures with their source URL)". WebFetch follows
   the same pattern, phrased without a tool name (Kimi calls it FetchURL).
   *Affects: tax-compute (6), tax-fbar (4), tax-parse (4), tax-verify (4), tax-review (3), tax-setup (1), tax-parse-crypto (1).*
2. **Delegation:** subagent/Task instructions → "dispatch parallel sub-tasks if your
   agent supports them (Codex agents, Cursor subagents, Kimi AgentSwarm, Claude Task);
   otherwise process sequentially in this session". The no-network / no-write-outside-
   `summaries/` constraints are restated as constraints on WHOEVER executes, not on a
   named tool. The 3 structurally-parallel skills (tax-parse, tax-verify big-PDF path,
   tax-parse-crypto drift fallback) each gain an explicit sequential-fallback paragraph.
   *Affects: tax-parse (6), tax-parse-crypto (4), tax-verify (2).*
3. **Interviews:** AskUserQuestion → "ask the user, one topic at a time". The 4
   interview-heavy skills (setup, rollover, compute, fbar) additionally gain a headless
   degrade path: "in non-interactive runs, read answers from `answers.md` and hard-stop
   on missing REQUIRED answers — never guess." *(Guessing a filing status or a residency
   flag silently produces a wrong return — this is the under-statement bias class.)*
   *Affects: tax-setup (2), tax-rollover (2), tax-parse (2), tax-fbar (2), tax-compute (1), tax-retirement (1), tax-review (1), tax-verify (1).*
4. **Shell:** keep command examples as chained one-liners (`source .venv/bin/activate && …`)
   — required anyway by Codex's fresh-shell-per-command model. Where activation is
   assumed to persist across steps, switch to absolute `.venv/bin/python` invocation.
5. **Identity:** the 7 bare "Claude" mentions (tax-calendar 2, tax-parse 2, tax-ptet 1,
   tax-retirement 2) → "the agent". `/tax-x` cross-references → "the tax-x skill (invoke
   it per your agent's skill syntax)". The one true auto-invocation
   (tax-setup → tax-rollover) gains inline fallback text for agents that cannot chain.
6. **`tax_law_fetcher.py`:** cosmetic docstring/output strings mentioning "WebSearch" /
   "Claude Code" → neutral wording. **No behavior change** — verify with
   `python3 agents/lib/tax_law_fetcher.py` self-test.

- [ ] **Step 1: Write the failing lint test**

Append to `tests/test_portability.py`:

```python
class TestNeutralizationLint:
    """No skill body may instruct the reader to use a Claude-only tool by name.

    Denylist entries are the LOAD-BEARING invocation phrasings — a bare mention
    inside a per-agent note ("Claude Task") is legitimate and stays allowed, which
    is why the patterns include the imperative verb.
    """

    DENY = (
        "use WebSearch",
        "use the WebSearch",
        "AskUserQuestion tool",
        "dispatch a Task",
        "the Task tool",
        "using WebFetch",
    )

    def test_no_claude_only_invocations(self):
        offenders = []
        for d in _skill_dirs():
            text = (d / "SKILL.md").read_text()
            for pat in self.DENY:
                if pat.lower() in text.lower():
                    offenders.append(f"{d.name}: {pat!r}")
        assert not offenders, (
            "skill bodies still name Claude-only tools imperatively — a Codex, "
            "Cursor, Kimi or Copilot user cannot follow these instructions:\n  "
            + "\n  ".join(offenders))

    def test_parallel_skills_document_a_sequential_fallback(self):
        for name in ("tax-parse", "tax-verify", "tax-parse-crypto"):
            text = (SKILLS_DIR / name / "SKILL.md").read_text().lower()
            assert "sequential" in text, (
                f"{name} relies on parallel dispatch but documents no sequential "
                "fallback — agents without subagents would stall")

    def test_interview_skills_document_a_headless_degrade(self):
        for name in ("tax-setup", "tax-rollover", "tax-compute", "tax-fbar"):
            text = (SKILLS_DIR / name / "SKILL.md").read_text().lower()
            assert "answers.md" in text, (
                f"{name} interviews the user but names no non-interactive source; "
                "headless runs (codex exec, Copilot cloud) would guess")
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL, listing the current offenders.

- [ ] **Step 3: Apply rules 1-5 skill by skill**

One skill at a time. Skills are independent files — this step parallelizes safely, but
each rewrite must preserve every literal identifier the doc-sync tests grep
(tie-out category names in backticks, capability-rule flag names, `mfs_spouse_itemizes`,
`63(c)(6)(A)`, "mandatory itemization"). Never reflow a line that carries one.

- [ ] **Step 4: Apply rule 6 to `tax_law_fetcher.py`**

Run: `source /path/to/tax-prep-skills/.venv/bin/activate && python3 agents/lib/tax_law_fetcher.py`
Expected: self-test passes, output identical except the reworded strings.

- [ ] **Step 5: Run the full suite**

Expected: PASS (2089 + 3 = 2092). A failure in `test_capabilities` /
`test_tax_computations` / `test_tie_out` means a rewrite ate an identifier — restore
the literal, do not weaken the test.

- [ ] **Step 6: Commit**

```bash
git commit -m "docs(skills): neutralize agent-specific tool names in skill bodies

Replaces named-tool imperatives with capability descriptions plus explicit degrade
paths: sequential fallback where parallel dispatch was assumed, and an answers.md
non-interactive path for the four interview-driven skills so a headless run stops
instead of guessing."
```

---

### Task 5: `docs/contributing/agents-compat.md`

One page, one section per agent, covering ONLY the sharp edges — the things that
silently produce a wrong return or a privacy incident. **Every claim must be
verifiable**; the spec's `.mcp.json` claim already failed that test (see "Spec
corrections" above) and must not be repeated.

**Files:**
- Create: `docs/contributing/agents-compat.md`
- Modify: `AGENTS.md` (add the pointer), `README.md` (one line under setup)

**Interfaces:**
- Consumes: Task 2's `.vscode/settings.json` (described), Task 3's AGENTS.md (linked),
  Task 4's degrade paths (referenced as the fallback the sharp edges rely on).

Required per-agent content (spec §6), with the corrections applied:

- **Codex:** `.git` / `.agents` / `.codex` are READ-ONLY under workspace-write (git
  commit needs approval or `.codex/rules`); network off by default; fresh shell per
  command; **`web_search="cached"` default is a tax-law hazard → set `"live"`**;
  headless `codex exec` cannot interview → the answers.md path from Task 4.
- **Cursor:** git-hook install triggers approval; pypi.org allowlisted so the venv
  works; **Cloud/Background Agents run on vendor VMs with 90-day snapshots — NEVER use
  them with real taxpayer documents; local agent + sandbox only**; `hooks.json` is the
  native analog of our git hooks.
- **Kimi Code:** no OS sandbox (approval model only); web search needs OAuth/search
  wiring — absent it, the paste-figures fallback applies; Bash 60 s default timeout, so
  long pytest runs need an explicit timeout or background execution;
  `/import-from-cc-codex` exists but is unnecessary with this layout.
- **Copilot CLI:** tools approval-gated including `web_fetch`; `web_search` exists but
  carries NO documented freshness contract → verify fetched figures name the current
  tax year, else use the paste-figures fallback; open truncation bug #2111 (~160 lines)
  is why AGENTS.md is ordered security-first; MCP config would live at
  `~/.copilot/mcp-config.json` or a repo `.mcp.json` — **this repo ships neither today**
  (v2, spec §9).
- **Copilot cloud/coding agent — CONTRIBUTOR SURFACE ONLY:** runs on an ephemeral
  Actions VM seeing committed repo content + issue/PR text. Taxpayer runtime files are
  gitignored and local-only, so a return structurally cannot run there. Two rules:
  never paste taxpayer data into issues or PR descriptions (that text IS agent input);
  individual plans (Free/Pro/Pro+/Max) have model training on inputs DEFAULT-ON since
  2026-04-24 — opt out in Copilot settings (Business/Enterprise contractually excluded).
- **Copilot VS Code / JetBrains:** skills GA and default-on (VS Code 1.109, JetBrains
  2026-06-02); the checked-in `.vscode/settings.json` covers the setting-default gap;
  `#fetch` caches ~5 minutes (re-fetch when a figure matters); `#websearch` needs the
  ms-vscode websearchforcopilot extension + Tavily key, absent which the paste-figures
  fallback applies; Copilot code review reads `.github/skills` + copilot-instructions,
  NOT the AGENTS.md/`.agents/skills` pair — a documented upstream asymmetry.
- **Claude Code:** unchanged; `bash agents/install.sh` for global skills, or the
  project `.claude/skills` symlinks work out of the box.
- **All agents:** git hooks are the host-side enforcement backstop and are
  agent-independent; the PII pre-commit scan still fires on a plain `git commit` from
  any agent that can commit. Note Codex's read-only `.git` and Kimi's fail-open hooks.

- [ ] **Step 1: Write the page**

- [ ] **Step 2: Verify every factual claim**

For each claim, either cite the primary source inline or drop the claim. Explicitly
re-check that nothing asserts the existence of a file this repo does not ship.

- [ ] **Step 3: Link it from AGENTS.md and README.md**

- [ ] **Step 4: Run the full suite**

Expected: PASS (2092).

- [ ] **Step 5: Commit**

```bash
git commit -m "docs: per-agent compatibility notes for Codex/Cursor/Kimi/Copilot

Covers only the sharp edges that silently corrupt a return or leak taxpayer data:
cached-web-search defaults, vendor cloud VM snapshots, instruction-file truncation,
and the surfaces that cannot interview."
```

---

## Definition of done

- [ ] 20 skills at `.agents/skills/<name>/SKILL.md`; `agents/skills/` gone
- [ ] 20 relative `.claude/skills/<name>` symlinks, none absolute, all resolving in-repo
- [ ] `AGENTS.md` ≤ 28 KB with security + freshness in the first 100 lines
- [ ] `CLAUDE.md` ≤ 150 lines, `@AGENTS.md` as its first content line, additive-only
- [ ] `.vscode/settings.json` tracked (gitignore narrowed to `.vscode/*` + negation)
- [ ] All 20 skill bodies free of Claude-only imperatives; parallel + interview skills
      carry explicit degrade paths
- [ ] `docs/contributing/agents-compat.md` published, every claim source-verified
- [ ] Full suite green (~2092), `install.sh` verified working, no Python behavior change
- [ ] PR opened; Codex review loop run to a clean pass on an unchanged HEAD
