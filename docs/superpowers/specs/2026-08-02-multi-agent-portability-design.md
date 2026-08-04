# 2026-08-02 — Multi-agent portability (Codex / Cursor / Kimi / Copilot) — design spec

User decisions (2026-08-02): audience = external users cloning the repo; parity bar =
core pipeline parity with graceful degradation; instruction files = split (AGENTS.md
agent-neutral + thin CLAUDE.md). Approach A ("convergent standard") approved over a
generator build step (rejected: unnecessary duplication + drift risk) and a docs-only
guide (rejected by the parity decision). GitHub Copilot added as a fourth target per
user direction (2026-08-02, after initial approval).

Research basis (2026-08-02, official sources, reports in session scratchpad):
Codex CLI 0.146.0, Cursor 2.4+/CLI, Kimi Code CLI v0.31.1 (NOT the wound-down Python
"Kimi CLI"), plus a full repo-coupling inventory (20 skills graded 9 CLEAN / 4 LIGHT /
7 HEAVY; agents/lib fully agent-agnostic — 21 modules, zero Claude imports).
Copilot research (2026-08-02, three surfaces + adversarial verification against
docs.github.com / code.visualstudio.com / github.blog): Copilot CLI GA 2026-02-25
(v1.0.77), Copilot cloud (né coding) agent, VS Code 1.109+ (skills GA default-on) /
JetBrains (skills GA 2026-06-02). Every load-bearing claim below is verbatim-confirmed
on a primary page.

## 1. Scope

Make the toolkit usable, zero-install, by users of OpenAI Codex, Cursor, Kimi Code,
and GitHub Copilot (CLI + VS Code/JetBrains agent mode; the cloud/coding agent is a
CONTRIBUTOR surface only — see §6) who clone this repo — while keeping Claude Code
fully working. The full return pipeline (setup → parse → compute → generate → verify →
review → fbar) plus all planning skills must run on all five agents; Claude-specific
mechanics degrade gracefully rather than block. The Python layer needs NO changes
(already agent-agnostic CLIs).

## 2. The convergence this design exploits

All four target agents natively read the SAME two artifacts:

| Surface | Codex | Cursor | Kimi Code | Copilot | Claude Code |
|---|---|---|---|---|---|
| `AGENTS.md` (repo root) | native (32 KiB combined cap, SILENT truncation) | native | native | native on CLI + cloud agent + VS Code 1.104+/JetBrains; COMBINES with CLAUDE.md + copilot-instructions.md (no suppression); CLI has an OPEN ~160-line truncation bug (#2111) | via CLAUDE.md `@AGENTS.md` import |
| `.agents/skills/<name>/SKILL.md` | native scan (cwd→repo root) | native (agentskills.io) + also loads `.claude/skills/` | native scan | native scan on CLI + cloud agent + VS Code 1.109+ GA/JetBrains (VS Code `chat.agentSkillsLocations` DEFAULT omits it — §3 workspace setting; `.claude/skills/` also read = double coverage) | via symlinks (see §3) |
| Subagents | native `[agents]` | custom subagents | Agent/AgentSwarm (≤128) | CLI parallel agents + `.github/agents/*.agent.md`; VS Code parallel subagents | Task tool |
| Interview tool | request_user_input (mode-gated) | ask-clarifying + Plan mode | AskUserQuestion (same name) | CLI/VS Code interactive chat; cloud agent CANNOT interview (issue-driven) | AskUserQuestion |
| Web search | built-in — but default `web_search="cached"` (STALE risk) | built-in | WebSearch — absent unless wired | CLI `web_search` (GitHub-MCP-backed) + `web_fetch`, approval-gated, NO documented freshness contract (STALE risk); VS Code `#fetch` (~5-min cache) / `#websearch` extension-only; cloud agent firewalled, none by default | WebSearch |

## 3. Layout & discovery (the move)

- **Canonical skill home moves** from flat `agents/skills/tax-*.md` to
  `.agents/skills/<name>/SKILL.md` (folder-per-skill, `name`+`description` YAML
  frontmatter — the one format all four agents accept). Existing frontmatter is
  already compatible; each file is otherwise moved verbatim in this step (content
  changes are §5's separate pass).
- **Claude Code project-local + Cursor double-coverage:** tracked RELATIVE symlinks
  `.claude/skills/<name>` → `../../.agents/skills/<name>`. This also FIXES the existing
  defect: 7 git-tracked symlinks in `.claude/skills/` currently point to ABSOLUTE
  machine paths (broken on any other clone) and cover only a legacy 7-of-20 subset.
- **`agents/skills/` is removed** (not kept as compat symlinks — nothing external
  depends on the path; in-repo references are updated: `install.sh`, CLAUDE.md,
  playbook, `tests/test_tie_out.py` doc-sync test, any docs).
- **`install.sh`** keeps its Claude global install (symlink targets change to
  `.agents/skills/*/SKILL.md`), keeps gitignore + git-hooks setup (agent-neutral),
  fixes its stale 15-of-20 skill listing, and prints a short "other agents: no install
  needed — Codex/Cursor/Kimi/Copilot discover .agents/skills/ automatically" note.
- **Copilot belt-and-braces:** ship a checked-in `.vscode/settings.json` with
  `"chat.agentSkillsLocations": { ".agents/skills": true }`. VS Code's skills doc and
  loader source both list `.agents/skills/`, but the SETTING's default object omits it
  (verified 2026-08) — the workspace setting removes the ambiguity, and also keeps
  VS Code working on Windows clones where the `.claude/skills` symlinks don't
  materialize. Copilot reading BOTH `.agents/skills/` and the `.claude/skills/`
  symlinks is benign double-discovery: the entries resolve to the identical SKILL.md
  (dedupe behavior is undocumented upstream; worst case is a duplicate listing of the
  same content, never divergent copies).
- Windows: symlinks may not materialize without developer mode — documented caveat
  (Cursor reads `.agents/skills` directly, so Windows Cursor users are unaffected;
  Windows Claude Code users rely on `install.sh`'s global copy mode — a `--copy` flag
  is added for that case).

## 4. AGENTS.md split

- **`AGENTS.md` (new, ≤ 28 KB hard test guard — Codex truncates the COMBINED chain at
  32 KiB silently):** distilled agent-neutral project truth — what the toolkit is, the
  pipeline diagram, directory map, runtime files, security rules (PII/SSN/chmod 600),
  tax-law-freshness contract ("always fetch current law; never trust cached/stale web
  results — Codex users must set `web_search = "live"`"), how to invoke skills, lib CLI
  conventions, testing commands. Deep content stays where it lives (docs/changelog,
  playbook, roadmap) and is POINTED to, not inlined.
- **`CLAUDE.md` (thinned):** `@AGENTS.md` import at top + only Claude-specific content
  (superpowers/spec-plan workflow paths, Codex-PR-review conventions, Claude-session
  git rules). Target ≤ 150 lines. The ~91% agent-neutral bulk moves/distills into
  AGENTS.md or existing docs.
- **Ordering rule: security first.** AGENTS.md content is ordered by criticality —
  PII/SSN/chmod-600 rules and the tax-law-freshness contract in the FIRST screenful,
  workflow conveniences last. Rationale: Codex truncates the combined chain at 32 KiB
  silently, and Copilot CLI has an open instruction-truncation bug (#2111, observed
  ~160 lines) with NO documented cap on any Copilot surface — front-loading makes any
  truncation degrade gracefully instead of dropping the security rules.
- **Copilot cross-reads both files:** every Copilot surface ingests CLAUDE.md AND
  AGENTS.md together (they combine; neither suppresses the other), so the thin
  CLAUDE.md must stay additive-only — nothing in it may contradict or re-state
  AGENTS.md. Copilot CLI expands `@`-imports and removes duplicate copies; VS Code and
  the cloud agent read the files independently, which is equivalent as long as the
  additive-only rule holds.
- No generation step in either direction; the doc-sync test guards size and the
  import line's presence.

## 5. Skill neutralization pass (bodies only; one commit per group)

Rewrite rules applied across the 20 skills:
1. **Web:** "use WebSearch" → "search the web (live results required — tax figures
   change annually; if your agent's web access is unavailable or cached-only, STOP and
   ask the user to paste the current figures with their source URL)". WebFetch
   mentions follow the same pattern (Kimi calls it FetchURL — name-free phrasing).
2. **Delegation:** subagent/Task instructions → "dispatch parallel sub-tasks if your
   agent supports them (Codex agents, Cursor subagents, Kimi AgentSwarm, Claude Task);
   otherwise process sequentially in this session" — with the no-network/no-write
   constraints restated as constraints on WHOEVER executes, not on a named tool. The 3
   structurally-parallel skills (tax-parse, tax-verify big-PDF path, tax-parse-crypto
   drift fallback) each gain an explicit sequential-fallback paragraph.
3. **Interviews:** AskUserQuestion mentions → "ask the user, one topic at a time" +
   a headless degrade path ("in non-interactive runs, read answers from answers.md and
   hard-stop on missing REQUIRED answers — never guess") for the 4 interview-heavy
   skills (setup, rollover, compute, fbar).
4. **Shell:** command examples stay chained one-liners (`source .venv/bin/activate &&
   …`) — required anyway by Codex's fresh-shell-per-command model; where activation is
   assumed across steps, switch to `.venv/bin/python` absolute invocation.
5. **Identity:** bare "Claude" mentions (7 lines / 4 files) → "the agent"; `/tax-x`
   cross-references → "the tax-x skill (invoke it per your agent's skill syntax)";
   the one true auto-invocation (tax-setup → tax-rollover) gets inline fallback text.
6. `tax_law_fetcher.py` cosmetic docstring/output strings ("WebSearch", "Claude Code")
   → neutral wording (no behavior change).

## 6. Per-agent compatibility notes — `docs/contributing/agents-compat.md`

One page, one section per agent, covering ONLY the sharp edges (verified 2026-08):
- **Codex:** `.git`/`.agents`/`.codex` are READ-ONLY in workspace-write (git commit
  needs approval or `.codex/rules`); network off by default; fresh shell per command;
  `web_search="cached"` default is a TAX-LAW HAZARD → set `"live"`; headless
  `codex exec` cannot interview.
- **Cursor:** git-hook installation triggers approval; pypi.org is allowlisted (venv
  works); **Cloud/Background Agents run on vendor VMs with 90-day snapshots — NEVER
  use them with real taxpayer documents; local agent + sandbox only** (this warning
  also goes in AGENTS.md's security section); hooks.json is the native analog of our
  git hooks.
- **Kimi Code:** no OS sandbox (approval model only — review commands before
  approving); WebSearch requires OAuth/search wiring — else the skills' paste-figures
  fallback applies; Bash 60s default timeout (long pytest runs need explicit timeout
  or background); `/import-from-cc-codex` exists but is NOT needed with this layout.
- **Copilot — CLI (local, can run returns):** tools are approval-gated (`web_fetch` /
  URL access included); `web_search` exists (GitHub-MCP-backed) but has NO documented
  cache/freshness contract → same tax-law rule as Codex: verify fetched figures carry
  the current tax year or use the paste-figures fallback. Open truncation bug (#2111,
  ~160 lines observed on instruction files) → AGENTS.md is ordered security-first
  (§4) and kept short. MCP config: `~/.copilot/mcp-config.json` or repo `.mcp.json`
  (the SAME `.mcp.json` that already serves Claude + Kimi — v2 packaging gets a third
  consumer for free).
- **Copilot — cloud/coding agent (CONTRIBUTOR surface ONLY):** runs on an ephemeral
  GitHub Actions VM that sees committed repo content + issue/PR text — taxpayer
  runtime files are gitignored/local-only, so a return structurally CANNOT run there
  (and the default-on firewall + no built-in web search mean it can't fetch tax law
  either). Two rules for contributors: (1) NEVER paste taxpayer data into issues/PR
  descriptions — that text IS agent input; (2) individual Copilot plans (Free/Pro/
  Pro+/Max) have model-training on inputs DEFAULT-ON since 2026-04-24 — opt out in
  Copilot settings (Business/Enterprise are contractually excluded).
- **Copilot — VS Code / JetBrains (local, can run returns):** skills GA + default-on
  (VS Code 1.109, JetBrains 2026-06-02); the checked-in `.vscode/settings.json` (§3)
  covers the skills-location setting-default gap; `#fetch` caches pages ~5 minutes
  (minor staleness — re-fetch when a figure matters); `#websearch` requires the
  ms-vscode websearchforcopilot extension + Tavily key — absent it, the paste-figures
  fallback applies. Copilot code review reads `.github/skills` + copilot-instructions
  (NOT the AGENTS.md/`.agents/skills` pair) — a known upstream asymmetry, documented
  not worked around.
- **Claude Code:** unchanged behavior; `install.sh` for global skills, or project
  `.claude/skills` symlinks work out of the box.

## 7. Testing

New `tests/test_portability.py` (doc-sync suite):
1. Every `.agents/skills/*/SKILL.md` has valid YAML frontmatter with non-empty
   `name` (matching its directory) and `description`.
2. At least 20 skill dirs; every `.claude/skills/<name>` entry is a RELATIVE symlink
   resolving inside the repo to the matching `.agents/skills/<name>` (kills the
   absolute-path defect class permanently).
3. `AGENTS.md` exists, < 28 KB, and CLAUDE.md's first content line imports it; the
   PII/security block appears within AGENTS.md's first 100 lines (the §4
   security-first ordering, made testable).
4. `.vscode/settings.json` exists and its `chat.agentSkillsLocations` object maps
   `".agents/skills"` to `true`.
5. Neutralization lint: skill bodies contain no load-bearing bare tool invocations
   from a denylist (`use WebSearch`, `AskUserQuestion tool`, `dispatch a Task`) —
   allowlisted where a phrase legitimately survives (e.g. Claude-specific appendix
   notes), mirroring the PII-scan allowlist pattern.
6. Existing `test_tie_out.py` doc-sync test updated to the new tax-parse path.

## 8. Security

- PII rules move verbatim into AGENTS.md (all agents see them) — and FIRST in the
  file (§4 ordering rule).
- The Cursor cloud-agent warning is a MUST (real-taxpayer-data toolkit).
- Copilot additions (same MUST class): never put taxpayer data in GitHub issues / PR
  text (cloud-agent input); individual-plan contributors disable Copilot model
  training (default-ON since 2026-04-24). Both go in AGENTS.md's security section AND
  the compat page.
- Git hooks remain the enforcement backstop on every agent (they run host-side and
  are agent-independent); Kimi's fail-open hooks and Codex's read-only `.git` are
  documented, with the PII pre-commit scan called out as the last line that still
  fires on plain `git commit` from any agent that CAN commit.

## 9. Explicit non-goals (v1)

MCP server packaging (clean v2 path: one `.mcp.json` already serves Claude+Kimi;
Codex/Cursor configs documented then); plugin/marketplace packaging (Codex plugins,
Cursor plugin/marketplace, kimi.plugin.json — v2, and the repo is private which blocks
Cursor's public marketplace anyway); per-agent CI matrix (no vendor CLI licenses in
CI); Windows-native symlink automation; porting the superpowers workflow docs (they
stay Claude-only in CLAUDE.md); any Python behavior change.

## 10. Sequencing

Implementation branch `feat/multi-agent-portability` (this branch, off origin/main)
starts AFTER PR #65 (FL module) merges, because the skill-file move collides with
#65's skill edits; docs (this spec + backlog) commit now. If #65 stalls, rebase this
branch onto it and proceed stacked (user pre-approved the wait-first default).
