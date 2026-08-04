# Multi-Agent Portability — Codex / Cursor / Kimi / Copilot Backlog

Source: product-owner brainstorm 2026-08-02 (user decisions: external cloners; core-pipeline
parity with graceful degradation; AGENTS.md split; Copilot added as fourth target same day).
Design approved same day —
`docs/superpowers/specs/2026-08-02-multi-agent-portability-design.md` (Approach A,
"convergent standard"). Research verified against official vendor docs 2026-08-02:
Codex CLI 0.146.0, Cursor 2.4+/CLI, Kimi Code v0.31.1 (legacy Python Kimi CLI is wound
down — not a target), GitHub Copilot (CLI GA 2026-02-25 v1.0.77, cloud/coding agent,
VS Code 1.109+ / JetBrains — 3-surface research + adversarial verification, every
load-bearing claim verbatim-confirmed on docs.github.com / code.visualstudio.com).
Repo coupling inventoried: 20 skills (9 CLEAN / 4 LIGHT / 7 HEAVY);
`agents/lib` already fully agent-agnostic.

**The one-paragraph pitch:** all four target agents natively read `AGENTS.md` and scan
`.agents/skills/<name>/SKILL.md` (Copilot included — CLI, cloud agent, and VS Code/
JetBrains all confirmed). So portability is a *move + distill + rephrase*, not a
port: relocate the skills to the convergent path (zero-install for all four),
distill an AGENTS.md under Codex's 32 KiB silent-truncation cap (security rules FIRST —
Copilot CLI has an open ~160-line truncation bug), and neutralize
Claude-tool phrasing with explicit degrade paths. The Python engine ships unchanged.

## Epic Q — Multi-agent portability (v1)

| # | Story | Spec § | Effort | Notes |
|---|-------|--------|--------|-------|
| Q1 | **Skill relocation**: `agents/skills/tax-*.md` → `.agents/skills/<name>/SKILL.md` (verbatim move, frontmatter intact); tracked RELATIVE `.claude/skills/<name>` symlinks (fixes the 7 absolute-path tracked-symlink defect); update every in-repo path reference | §3 | M | The load-bearing move; `test_tie_out.py` doc-sync path included |
| Q2 | **install.sh**: retarget Claude global symlinks, add `--copy` (Windows), fix stale 15-of-20 listing, print the zero-install note for other agents | §3 | S | Hooks/gitignore parts unchanged (agent-neutral) |
| Q3 | **AGENTS.md distillation + CLAUDE.md thinning**: agent-neutral truth → AGENTS.md (≤28 KB guard); CLAUDE.md → `@AGENTS.md` import + Claude-only extras (≤150 lines) | §4 | M | The Codex 32 KiB SILENT truncation is the forcing constraint |
| Q4 | **Neutralization — web/search** (8 files): live-results-required phrasing + paste-figures fallback; Codex `web_search="live"` hazard note | §5.1 | S | Tax-law freshness is the product risk here |
| Q5 | **Neutralization — delegation** (10 files): agent-neutral parallel dispatch phrasing; explicit sequential fallbacks in the 3 structurally-parallel skills (parse, verify, parse-crypto) | §5.2 | M | All four agents have subagents — fallback is for headless/simple modes |
| Q6 | **Neutralization — interviews** (8 files): "ask the user, one topic at a time" + headless answers-file degrade with hard-stop on missing REQUIRED answers | §5.3 | S | Never guess — bias rule |
| Q7 | **Shell/identity sweep**: chained one-liners / `.venv/bin/python` absolutes (Codex fresh-shell); bare "Claude"→"the agent"; `/tax-x`→"the tax-x skill"; tax-setup→rollover auto-invoke fallback; `tax_law_fetcher.py` cosmetic strings | §5.4–5.6 | S | No Python behavior change |
| Q8 | **`docs/contributing/agents-compat.md`**: per-agent sharp edges only — Codex (.git RO, network off, cached search hazard), Cursor (**cloud-agents = PII hazard, local-only warning**, hook approvals, pypi allowlist), Kimi (no sandbox, WebSearch wiring, Bash timeouts), Claude (unchanged) | §6 | S | Cursor cloud-agent warning duplicated into AGENTS.md security section |
| Q9 | **`tests/test_portability.py`**: frontmatter validity; ≥20 dirs; relative-symlink integrity; AGENTS.md size + CLAUDE.md import; neutralization denylist lint (allowlist pattern per PII-scan precedent) | §7 | M | Makes the symlink defect class impossible to reintroduce |
| Q10 | **README refresh + wrap-up**: multi-agent quickstart (per-agent 3-line "clone → open → run tax-setup", Copilot included), de-brand from Claude-only, changelog epic-Q, CLAUDE.md/AGENTS.md test-count | §1 | S | README currently has 14 Claude mentions / 230 lines |
| Q11 | **Copilot enablement**: checked-in `.vscode/settings.json` (`chat.agentSkillsLocations: {".agents/skills": true}` — the VS Code setting-default gap + Windows-symlink fallback); Copilot section in agents-compat.md (CLI freshness/truncation, cloud agent = CONTRIBUTOR-ONLY surface, VS Code/JetBrains notes); the two GitHub-side privacy MUSTs into AGENTS.md security (no taxpayer data in issues/PRs; individual-plan training default-ON 2026-04-24 → opt out) | §2, §3, §6, §8 | S | AGENTS.md security-first ORDERING itself lands in Q3; Q9's tests cover the `.vscode` file + first-100-lines security check |

**Build order:** Q1→Q2 (layout) → Q3 (docs split) → Q4-Q7 (body passes, parallelizable) →
Q8-Q9-Q11 (compat + tests + Copilot) → Q10. Single PR is feasible (mostly moves + prose);
if the diff gets unwieldy, split after Q3.

**Sequencing constraint:** starts after PR #65 (FL module) merges — the skill move
collides with #65's skill edits. This branch (`feat/multi-agent-portability`) holds
docs-only commits until then.

## v2 parking lot (explicit non-goals now)

- MCP server packaging (one `.mcp.json` already serves Claude+Kimi natively; Codex/Cursor
  config documented then).
- Plugin/marketplace packaging: Codex plugins (`.codex-plugin/plugin.json` + repo
  marketplace), Cursor plugin (public marketplace blocked — repo is private),
  `kimi.plugin.json` (`/plugins install <github-url>`).
- Per-agent CI matrix (no vendor CLI licenses in CI); a monthly "agent-surface drift"
  re-verification checklist instead.
- Windows symlink automation; superpowers workflow docs stay Claude-only.

## Bias-direction ledger (house taxonomy)

| Risk | Direction | Treatment |
|---|---|---|
| Codex cached web search returns stale tax figures | WRONG numbers acted on | Skills demand live results or hard-stop + paste-figures fallback (Q4); AGENTS.md + compat doc call out `web_search="live"` |
| Copilot CLI web_search has NO documented freshness contract; VS Code `#fetch` caches ~5 min | WRONG numbers acted on | Same Q4 live-results-required phrasing covers it (agent-neutral by design); compat doc names the Copilot specifics (Q11) |
| Cursor cloud agents copy taxpayer PII to vendor VMs | Data exposure | Loud MUST-NOT in AGENTS.md security + compat doc (Q8) |
| Taxpayer data pasted into GitHub issues/PRs feeds Copilot cloud agent; individual-plan training default-ON since 2026-04-24 | Data exposure | MUST-NOT + opt-out instruction in AGENTS.md security + compat doc (Q11); cloud agent framed CONTRIBUTOR-ONLY (structurally cannot see gitignored taxpayer files) |
| Headless runs guessing interview answers | Silent wrong return | Hard-stop on missing REQUIRED answers (Q6) |
| Kimi fail-open hooks / no sandbox | PII scan bypass | Git pre-commit scan remains host-side backstop; documented (Q8) |
| AGENTS.md silently truncated by Codex at 32 KiB; Copilot CLI open truncation bug (#2111, ~160 lines observed) | Rules silently dropped | ≤28 KB test guard (Q9) + security-rules-FIRST ordering (Q3) with a first-100-lines test (Q9) |
