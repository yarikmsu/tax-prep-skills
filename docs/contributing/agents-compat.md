# Per-agent compatibility notes

This toolkit runs on OpenAI Codex, Cursor, Kimi Code, GitHub Copilot and Claude
Code. The skills themselves are agent-neutral — this page covers only the **sharp
edges**: the places where a specific agent's defaults would silently produce a
wrong return or expose taxpayer data.

Read your agent's section before your first run. `AGENTS.md` carries the rules
that apply to everyone.

Behaviour below was verified against vendor documentation in **August 2026**.
Agents ship fast; if a claim here contradicts what your agent actually does,
trust the agent and please open a PR.

## Two hazards that cut across every agent

**1. Stale tax figures.** Tax numbers change annually, and a prior-year figure
produces a return that is wrong but entirely plausible — no test, gate or review
step downstream can detect it. Several agents default to cached web results. Set
your agent to live search, confirm every fetched figure names the tax year you
are filing, and if you cannot get live results, stop and paste the figures in by
hand with their source URL. Every skill that fetches tax law documents this
fallback.

**2. Hosted execution.** Cloud/background agent modes run on vendor
infrastructure and retain snapshots. **Never run a real return on one.** They are
contributor surfaces — fine for editing this repo, never for processing a
taxpayer's documents.

---

## OpenAI Codex

- **`web_search` defaults to `"cached"` — this is the tax-law hazard.** Set it to
  `"live"` in your Codex config before running `tax-setup`, `tax-compute`,
  `tax-review` or `tax-verify`. Cached results can silently return a prior tax
  year's brackets.
- **Network is off by default.** Enable it for the tax-law fetch steps; keep it
  off for document parsing, which must never have network access.
- **`.git`, `.agents` and `.codex` are read-only under `workspace-write`.** A
  `git commit` needs either an approval or a `.codex/rules` entry. Editing a
  skill file also requires lifting the `.agents` restriction.
- **A fresh shell runs per command**, so `source .venv/bin/activate` does not
  persist between commands. Chain it (`source .venv/bin/activate && pytest ...`)
  or call `.venv/bin/python` directly. The skills are already written this way.
- **`codex exec` (headless) cannot interview.** Interview-driven skills
  (`tax-setup`, `tax-rollover`, `tax-compute`, `tax-fbar`) read from `answers.md`
  in that mode and hard-stop on a missing required answer rather than guessing.
- **Instruction files are truncated at 32 KiB across the combined chain, with no
  warning.** `AGENTS.md` is kept well under that and ordered security-first so a
  truncation loses conveniences, not safeguards.

## Cursor

- **Never use Cloud or Background Agents for a real return.** They execute on
  Cursor's infrastructure and retain snapshots for 90 days. Use the local agent
  with the sandbox instead. This is the single most important rule on this page.
- Installing the git hooks (`bash agents/install.sh`) triggers an approval
  prompt — expected, and worth approving: the hooks are the PII backstop.
- `pypi.org` is allowlisted, so creating the virtual environment works without
  extra configuration.
- Cursor reads **both** `.agents/skills/` and `.claude/skills/`. Since the latter
  are symlinks to the former, you may see each skill listed twice; both entries
  resolve to the same `SKILL.md`, so there is no risk of divergent copies.
- `hooks.json` is Cursor's native analogue of our git hooks. The git hooks still
  run host-side regardless, which is what makes them agent-independent.

## Kimi Code

- **No OS-level sandbox** — Kimi uses an approval model only. Read commands
  before approving them, particularly anything touching `documents/` or writing
  outside `summaries/`.
- **Web search requires OAuth/search wiring.** Without it there is no web access
  at all, so the paste-the-figures fallback applies to every tax-law fetch. Do
  not let the agent proceed on remembered figures.
- **Bash commands time out at 60 seconds by default.** The full test suite and
  large PDF parses need an explicit longer timeout or background execution.
- Kimi's Agent / AgentSwarm feature covers the parallel document-parsing path
  (up to 128 agents). Without it, the skills' sequential fallback applies.
- `/import-from-cc-codex` exists but is **not needed** — Kimi scans
  `.agents/skills/` natively.

## GitHub Copilot — CLI (local; can run returns)

- **Tools are approval-gated**, including `web_fetch` and URL access.
- **`web_search` publishes no cache or freshness contract.** Treat it like
  Codex's cached mode: confirm any fetched figure explicitly names the tax year
  you are filing, or use the paste-the-figures fallback.
- **Instruction files are truncated** — an open bug (#2111) reports around 160
  lines. This is why `AGENTS.md` leads with security and the freshness contract.
- Copilot CLI reads `CLAUDE.md` **and** `AGENTS.md` together; neither suppresses
  the other, and `@`-imports are expanded with duplicates removed. `CLAUDE.md`
  is deliberately additive-only so the pair cannot contradict each other.
- **MCP:** Copilot CLI reads `~/.copilot/mcp-config.json` or a repository
  `.mcp.json`. **This repository ships neither today** — MCP packaging is
  explicitly out of scope for this version. Nothing here depends on MCP.

## GitHub Copilot — cloud coding agent (CONTRIBUTOR SURFACE ONLY)

- Runs on an ephemeral GitHub Actions VM that sees **committed repository content
  plus issue and pull-request text**. Every taxpayer runtime file is gitignored
  and local-only, so a return structurally cannot run there — and with the
  default-on firewall and no built-in web search, it could not fetch tax law
  either.
- **Never paste taxpayer data into an issue or PR description.** That text is
  agent input and is retained by the platform.
- **Individual plans (Free/Pro/Pro+/Max) have model training on inputs
  default-ON** as of 2026-04-24. Opt out in your Copilot settings if you
  contribute from an individual plan. Business and Enterprise plans are
  contractually excluded.

## GitHub Copilot — VS Code / JetBrains (local; can run returns)

- Agent skills are GA and enabled by default (VS Code 1.109+; JetBrains from
  2026-06-02).
- The repository ships `.vscode/settings.json` mapping
  `chat.agentSkillsLocations` to `.agents/skills`. VS Code's documentation and
  loader both list that path, but the setting's **default object omits it** — so
  without this checked-in file you would see no skills and no error. Keep the
  file; it is also the only discovery path on Windows clones where the
  `.claude/skills` symlinks do not materialise.
- `#fetch` caches pages for roughly five minutes. Minor, but re-fetch when a
  specific figure matters.
- `#websearch` requires the `ms-vscode` websearchforcopilot extension and a
  Tavily key. Without it, the paste-the-figures fallback applies.
- **Copilot code review** reads `.github/skills` and `copilot-instructions`, not
  the `AGENTS.md` / `.agents/skills` pair. That asymmetry is upstream; we
  document it rather than duplicating files to work around it.

## Claude Code

- Works unchanged. Either run `bash agents/install.sh` for a global install, or
  rely on the tracked `.claude/skills/` symlinks for project-local discovery.
- `CLAUDE.md` imports `AGENTS.md`, so the shared project truth loads first.

## Windows clones

Git symlinks may not materialise without developer mode enabled. Consequences:

- **Cursor and Copilot are unaffected** — they read `.agents/skills/` directly.
- **Claude Code** users should run `bash agents/install.sh --copy`, which copies
  each `SKILL.md` instead of symlinking.

## Git hooks are the shared backstop

The PII pre-commit scan and the branch guard run **host-side**, so they fire on a
plain `git commit` from any agent that can commit at all — they do not depend on
the agent cooperating. Install them with `bash agents/install.sh`.

Two caveats:

- **Codex** treats `.git` as read-only under `workspace-write`, so it cannot
  commit without an approval; the hook is not bypassed, just unreached.
- **Kimi's** own hook mechanism fails open. That is a reason to rely on the git
  hooks rather than an agent-native equivalent.

The installer resolves the real hooks directory via `git rev-parse
--git-common-dir`, so hooks install correctly inside a git worktree — where
`.git` is a file rather than a directory, and a naive path would silently skip
installation.
