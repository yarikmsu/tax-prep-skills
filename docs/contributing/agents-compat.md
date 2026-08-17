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
  resolve to the same skill directory — `SKILL.md` and its `references/` alike —
  so there is no risk of divergent copies. The `.claude/skills/` entries link the
  DIRECTORY rather than the file for exactly that reason.
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
- **`.agents/skills/` is one of VS Code's default project skill locations**,
  alongside `.github/skills/` and `.claude/skills/` (personal: `~/.copilot/skills/`,
  `~/.claude/skills/`, `~/.agents/skills/`). No configuration is required. The
  repository still ships `.vscode/settings.json` pointing
  `chat.agentSkillsLocations` at `.agents/skills`, because that is the only
  discovery path on Windows clones where the `.claude/skills` symlinks do not
  materialise. Harmless everywhere the default already covers it.
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
  each skill directory — `SKILL.md` plus its `references/` — instead of
  symlinking. Copy mode is the more robust of the two here precisely because it
  depends on no link resolution at all.

**PII/financial file permissions (AGENTS.md §1) are enforced there too, not
just recorded.** `os.chmod(path, 0o600/0o700)` restricts access on POSIX only —
on Windows `Path.chmod()` toggles the read-only DOS attribute and touches
nothing about who can read or list the file. Every runtime write in
`agents/lib/` that secures a PII/financial path goes through
`agents/lib/secure_fs.py`, which on Windows shells out to PowerShell's
`Get-Acl`/`Set-Acl` (built into every Windows install, no extra dependency)
to build a *protected* DACL holding exactly one ACE — that identity, Full
Control — clearing whatever explicit grants the path already had rather than
layering onto them, which is what an `icacls /grant:r` one-liner would have left
behind, and then to take ownership. Changing only the DACL is insufficient
because the previous owner can replace it later. Those are two SEPARATE
`Set-Acl` calls, restriction first: `Set-Acl` persists every modified section at
once, so folding both into one object makes them all-or-nothing, and a path
where the process holds `WRITE_DAC` but not `WRITE_OWNER` would end up with no
restriction applied at all rather than a restriction plus a reported ownership
failure. Both are then verified against a fresh read — the protection flag, the
single ACE's identity, rights and inheritance, and the owner. Verifying only the
owner would return success on a descriptor whose DACL never changed. On a
**directory** that ACE is made inheritable
(`ContainerInherit,ObjectInherit`): a protected DACL with a non-inheritable
ACE strips the inherited grants off entries already inside — a summary file
whose only ACEs were inherited is left with an empty DACL, locking its owner
out — and leaves anything created afterwards falling back to the token
default DACL (SYSTEM and Administrators), where POSIX `0700` covers every
child by denying traversal. A leaf keeps the non-inheritable form, which is
the only valid one for a file. **Skill instructions get the same enforcement**:
every `SKILL.md` step that directly writes a PII/financial artifact runs
`sh agents/lib/run.sh -m agents.lib.secure_fs PATH [PATH ...]`; library-backed writers use
the same functions internally. This covers setup/parse state, the computed
`result.json`/`answers.md`, generated PDFs, FBAR and review workpapers,
projection/withholding reports, crypto imports, scenario reports, and the
`tax-amend` as-filed snapshot. A bare POSIX-mode command under a
POSIX-compatible shell on Windows hits the same DOS-attribute-only gap
`os.chmod` had. The command and its argument are also kept on one line: a line
break between them leaves `secure_fs` with no path, so it silently runs its own
self-test instead of securing anything. Where a step creates the directory and
another step writes the file into it, the two `secure_fs` calls sit in
**separate** fenced blocks — run as one block, the second would fire before the
file exists and exit 1.

## Why a copied skill must refuse to compute

A skill copied away from `agents/lib/` still reads as a complete, well-formed
procedure while every script it names silently does nothing — the gates do not
announce their own absence, they simply never run. That is why the preflight is
the first step of every engine-calling skill, and why a failure to RUN it is
itself the block: the script ships inside the directory whose disappearance is
the failure being detected.

## Reaching the engine: `agents/lib/run.sh`

Every engine invocation in a skill — `agents/lib/preflight.py` included — goes
through one launcher:

```bash
sh agents/lib/run.sh agents/lib/preflight.py
sh agents/lib/run.sh -m agents.lib.projection --workspace .
```

`run.sh` activates the workspace virtualenv and `exec`s the interpreter, so the
engine's exit status is the command's. ONE copy: the activation used to be
inlined at 106 call sites, and a 190-character string repeated that often
drifted — five sites chained it and then invoked a bare `python3`, which on a
Windows venv is the host interpreter. A wrapper makes that unrepresentable.

**Per call, not once at the top of a block.** Some agents open a fresh shell per
command (AGENTS.md §7), so an activation stated once does not reach the next
one, and the preflight would then certify one interpreter while the computation
ran on another — on a stock macOS, a 3.12 virtualenv certified while
`python3 -m agents.lib.projection` runs under /usr/bin/python3, a 3.9 this
toolkit does not support. `preflight.py` enforces the other half: it BLOCKS when
a workspace `.venv` exists that it is not running in.

Re-sourcing per command costs the caller's shell nothing, and for a reason that
has changed: `run.sh` runs in a CHILD shell, so the activation happens inside a
process that exits, and the calling shell's `PATH` is never touched at all. The
older argument — that CPython's `activate` calls `deactivate nondestructive`
before it prepends, so the twentieth `source` leaves what the first one did —
was about the inlined prefix this replaced, where the activation really did land
in the agent's own shell. It is no longer the mechanism in play, so do not reach
for it to justify hoisting the activation back out of the launcher: per-command
is not an inefficiency to optimise away, it is the only form that survives an
agent that opens a fresh shell per command.

**Both venv layouts, and why the form looks the way it does.** A stdlib venv
keeps its scripts in `bin/` on POSIX and `Scripts/` on Windows, so the prefix
tries each behind its own `-f` test. Two things forced that shape:

- **`[ -d .venv ] && source .venv/bin/activate` alone** silently no-ops against
  a `Scripts/` venv — the `-d` guard passes, the `source` finds nothing, and
  every engine command after it runs on the host interpreter.
- **`{ . a || . b; }` does not fix it.** POSIX requires a non-interactive shell
  to ABORT when `.` cannot read its file, so under `sh` or `dash` the `||` is
  never evaluated and the whole command — `preflight.py` included — never runs.
  Verified: `/bin/sh` exits 1 and `/bin/dash` exits 2 with no output, while
  `bash` reaches the fallback. Testing the file first sidesteps the abort
  entirely, which is why both branches carry `-f`.

`preflight.py` is the enforcement behind it: it blocks whenever a workspace
`.venv` exists that it is not running in, and its remedy names the layout that
workspace actually has.

The module also closes two related gaps. `Path.is_symlink()` is `False` for
an NTFS **junction** (a different reparse tag than a symlink, and — unlike a
symlink on older Windows — needing no elevated privilege to create), so
every symlink-refusal check in this codebase (`archive.py`'s moved entries
and `archive/{year}` destination, `tie_out.py`'s `summaries/`,
`parse_utils.py`'s validated document path and `documents/` scan, and
`tax_law_fetcher.py`'s cache read) uses `secure_fs.is_reparse_point()`,
which catches both, rather than `is_symlink()` alone. It keys on the reparse
**tag**, not on `FILE_ATTRIBUTE_REPARSE_POINT`: Windows sets that bit for a
whole family of filter-driver features whose files are ORDINARY — OneDrive
Files On-Demand placeholders, Data Deduplication, the overlay filter that
compresses installed application files, container image layers — so refusing on
the bare bit stopped `/tax-parse`, `/tax-archive` and `/tax-setup` with
"symlink or junction" on a normal document in a OneDrive-backed workspace. The
test is Windows' own `0x20000000` NAME SURROGATE flag ("the reparse point is a
surrogate for another named entity"), which every name-substituting tag carries
— symlink, junction, WSL's LX symlink, `GLOBAL_REPARSE`, and any added later —
plus `IO_REPARSE_TAG_APPEXECLINK`, which redirects without setting it. The
benign families leave the flag clear, `IO_REPARSE_TAG_NFS` included: it
represents an NFS special file, not a name substitution. A reparse point whose tag cannot be read is refused: the attribute
said "reparse point" and nothing certified otherwise.
`is_reparse_point()` uses `os.lstat()` on POSIX too:
Python 3.14's `Path.is_symlink()` converts every `OSError` to `False`, which
would turn access denied or a transient I/O failure into a false safety
certificate before a later operation follows the path. The helper therefore
fails **closed** on every platform: only `FileNotFoundError` — the path
genuinely doesn't exist — is treated as "not a reparse point"; any other
`OSError` propagates so a caller's `exists()`/`mkdir()` can't be led past an
uninspected link or junction. `secure_path()` refuses a reparse point itself,
too — the skill layer's own guard is the shell test `[ -L summaries ]`, which
is POSIX-only and reads false for a junction, so the refusal has to live at
the layer every caller passes through rather than in a prose instruction.

That refusal covers **every component the caller named**, not the leaf alone:
`os.lstat()` declines to follow only the FINAL component, so a linked `archive`
with a real `archive/{year}/as_filed` under it passed a leaf-only check and the
chmod landed outside the workspace. A relative path — what every skill passes,
run from the workspace root — is walked component by component from the current
directory down; an absolute path is walked the same way when it lies under the
current directory, and otherwise only its leaf is checked, because nothing here
can know where an unrelated tree's trust boundary is and a walk to the
filesystem root would refuse correct data (macOS puts every temporary directory
under `/var`, which is itself a symlink).

The CLI **stops at the first refusal** rather than reporting it and continuing:
the callers pass a tree outermost-first, so a refusal says the paths after it
are not what they appear to be. It prints how many were skipped, never their
names. That stop is scoped to `secure_fs.PathRefused` — a component that is a
reparse point, or that could not be inspected at all — because those say
something about the TREE. An ordinary failure (the file is gone, the chmod was
denied) says nothing about the other targets and does not stop the run: an
unmatched `summaries/*.md` arrives as a literal pattern, and the `result.json`
after it still has to be secured. `PathRefused` subclasses `OSError`, so
library callers that catch `OSError` and translate are unaffected.

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
