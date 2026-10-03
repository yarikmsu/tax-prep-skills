# Tax Prep Skills for AI Coding Agents

**A tax return is a good test of whether an AI agent can be trusted with work that must not be wrong.** Every figure is checkable, the rules change annually, being confidently wrong costs real money and IRS attention, and the input is a folder of documents nobody wants leaving their laptop.

This is a reusable, taxpayer-agnostic toolkit of `tax-*` skills that walk an agent through the full US federal + state pipeline — parsing source documents, computing every form line, producing a filing package, and reviewing the result from both an enrolled agent's and an IRS officer's perspective. It runs entirely on your machine.

The tax math is the easy half. The interesting half is the machinery that stops a *plausible* answer from passing as a correct one:

- **No tax law is hardcoded.** Every rate, bracket and threshold is fetched from IRS.gov at runtime and cached per year. If your agent's web access is cached rather than live, the skills stop and ask you — they never fall back to last year's figure. A stale bracket produces a return that is wrong and looks perfect, and no test, gate or review step downstream can detect it.
- **Every document must reach the return.** `tax-parse` records each source file it read; `tax-compute` hard-fails if a recorded document's amounts never appear in the result. That is the dropped-1099 CP2000 scenario, and it cannot pass silently.
- **The engine refuses to guess.** Situations it cannot compute — rental Schedule E, part-year residency, the premium tax credit, kiddie tax — block with the form named instead of producing a number, and stay blocked until you acknowledge the limitation explicitly. [Who this fits](docs/WHO-IT-FITS.md) measures how often that happens in the places this toolkit is built for.
- **Bias direction decides severity.** When data is missing or ambiguous, a fallback that *overstates* the number you act on is a hard error; one that understates it degrades with a loud banner. Never a silent guess in the expensive direction.
- **A skill without its engine refuses to run.** The gates above are scripts in `agents/lib/`, which does not travel when a skill is copied elsewhere. Copied alone, a skill still reads as a complete procedure while every gate silently does nothing — so each one now opens with a preflight, and failing to *run* that check is itself the block.

Those five rules are the actual subject of this repository. The [test suite](docs/contributing/test-invariants.md) exists mostly to prove they still hold.

**Start here:** [QUICKSTART](docs/QUICKSTART.md) goes from clone to a real computed number in about fifteen minutes, on bundled fixtures — no personal data, no network. Then [SECURITY.md](SECURITY.md) for what does and does not leave your machine, and [CONTRIBUTING.md](CONTRIBUTING.md) to add a state.

Runs on **OpenAI Codex, Cursor, Kimi Code, GitHub Copilot and [Claude Code](https://docs.anthropic.com/en/docs/claude-code)**. The first four discover the skills in `.agents/skills/` automatically with no install step; Claude Code works out of the box too, and `agents/install.sh` adds a global install. Each agent has sharp edges worth knowing before your first run — cached web search silently yielding last year's tax brackets, cloud agent modes that retain your documents — all documented in [per-agent compatibility notes](docs/contributing/agents-compat.md).

Works for any filing status and any tax year. **Three states are computed in depth:** California's Form 540 (Schedule CA adjustments, exemption credits, mental-health surcharge) and New York's Form IT-201 (the State tax, the New York City resident tax, the Yonkers taxes and the MCTMT). North Carolina's Form D-400 is also computed for a full-year resident under the source-verified TY2025 instructions, with other form years refused until verified. CA, NC and IT-201 require full-year residency. NY's TY2025 IT-203 variant additionally supports part-year/nonresident returns and NYC IT-360.1 with explicit residence dates and reviewed source allocations; its declared filing plan selects NY law independently of mailing address. It clears move/source blocks only when every other resident/source state has no income tax and requires no return. Other taxable-state returns and their credit worksheets still need separate preparation, and statutory second residency/foreign moves retain their gates. MFS, differing spouse residence periods, special IT-230 ratios and IT-203 Yonkers tax refuse. Its NY withholding forecasts and ordinary projections remain manual. IT-201's full-year City/Yonkers rules remain as documented in the playbook. Alaska, Florida, Nevada, South Dakota, Texas, Wyoming and Tennessee (from 2021) are treated as having no income tax, and income sourced to one of them does not stop a return; Washington’s separate LT capital-gains excise is computed for TY2025 from confirmed classifications and eligibility worksheets; separate spouses/RDPs, special federal gains and midyear domicile changes refuse. WA has no individual wage income tax, no required quarterly estimates, and files separately through My DOR. Every remaining state gets a bracket-based estimate only. The skills are state-parameterized, so adding another state is a well-scoped contribution — see the [state-module playbook](docs/contributing/state-module-playbook.md) for the pattern (fetched-law block, result section, compute functions, registrations, tests). PRs welcome.

> **This toolkit handles sensitive financial data.** All taxpayer information stays local on your machine. Full SSNs are never stored — only the last 4 digits. Pre-commit hooks block accidental commits of sensitive files. See [Security & Privacy](#security--privacy) for details.

## Prerequisites

- One of: OpenAI Codex, Cursor, Kimi Code, GitHub Copilot, or [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — see [per-agent notes](docs/contributing/agents-compat.md)
- **Live web access from your agent** — tax figures change annually, and a cached result silently produces a wrong return. Codex in particular defaults to `web_search = "cached"`; set it to `"live"`.
- Python 3.13+ with `pip`
- **Git 2.20 or newer** — the PII scan reads added lines via
  `git diff --output-indicator-new`, which older git does not have. The scan
  fails closed without it, so an older git blocks every commit rather than
  passing one unscanned.
- `gh` CLI (optional — for creating pull requests)

## Quick Start

```bash
# 1. Clone the repo. This directory becomes your tax workspace — your documents
#    and computed results live here, gitignored and local-only.
git clone https://github.com/yarikmsu/tax-prep-skills.git
cd tax-prep-skills

# 2. Set up Python environment
# `run.sh` owns the layout — it activates the workspace venv, `bin/` on POSIX or
# `Scripts/` on Windows, and execs the interpreter that venv installed. One form
# in one place: this step was written out four times in three spellings before,
# in the change that introduced the launcher to own exactly this. Chained, so a
# failed venv step stops here rather than installing the pinned tree into the
# HOST interpreter; not `|| exit 1`, which would close an interactive shell this
# was pasted into.
python3 -m venv .venv &&
  sh agents/lib/run.sh -m pip install -r requirements.txt

# 3. Install the PII git hooks. Every agent needs these: they run host-side on
#    `git commit`, so they protect you whichever agent you use.
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

Equivalent per invocation: `codex --search`. For the other agents see the
[per-agent notes](docs/contributing/agents-compat.md). If your agent cannot do
live web search at all, the skills stop and ask you to paste the current figures
with their source URL — let them.

**5. Start your agent in this directory.** Put your source documents in
`documents/`, then invoke the setup skill.

Then ask your agent to run the setup skill. **How you invoke a skill differs by
agent** — only Claude Code and Cursor use a bare slash command:

| Agent | Invoke it with | List available skills |
|---|---|---|
| Claude Code | `/tax-setup 2025` | type `/` |
| Cursor | type `/`, search `tax-setup` | type `/` |
| Codex | `$tax-setup` for 2025 | `/skills` |
| Kimi Code | `/skill:tax-setup 2025` | `/skill:` |
| GitHub Copilot (VS Code) | "follow the tax-setup skill for 2025" | picked up automatically |

If your agent has no skill mechanism at all, open
`.agents/skills/tax-setup/SKILL.md` and tell the agent to follow it — the files
are plain Markdown instructions, written to be executable that way. A skill is a
**directory**: the larger ones keep their step detail in `references/*.md`
alongside `SKILL.md`, linked from the step that needs it, so open each one as you
reach it. `SKILL.md` on its own is not the whole skill.

**Check that the skills were found before you start.** Every one of these agents
fails silently when discovery does not work: you get no skill and no error. List
them (`/skills` in Codex, `/` in Claude Code or Cursor) and confirm you see 20
entries beginning with `tax-`. Two further skills, `codex-loop` and
`state-module`, are for working on the toolkit itself and play no part in
preparing a return — ignore them here.

This starts the taxpayer interview and creates your workspace.

> **The clone is your workspace, not just the tool.** `documents/`,
> `taxpayer_profile.json`, `result.json`, `summaries/` and the generated PDFs are
> created inside it. All of them are gitignored and the pre-commit hooks block
> them, but **never push this clone** to a remote you did not create — and never
> paste taxpayer data into a GitHub issue or pull request, where hosted coding
> agents read it as input and the platform retains it.

### Upgrading an existing clone

**Always re-run the installer after pulling.** Two layout changes break a global
install in `~/.claude/skills/`, and only one of them is visible:

- **The multi-agent move.** Skills moved from `agents/skills/tax-*.md` to
  `.agents/skills/<name>/SKILL.md`, so symlinks from an older install point at
  paths that no longer exist — the skills simply disappear, with no error.
- **Progressive disclosure.** The larger skills now keep their step detail in
  `references/*.md` beside `SKILL.md`. An install predating that has only the
  body. This one does **not** announce itself: the skill still lists, still
  activates, and still reads as complete, while the steps it delegates are
  missing — for `tax-compute`, most of the return.

Re-run the installer once; it overwrites both in place:

```bash
git pull && bash agents/install.sh
```

Nothing else changes: your workspace files, `answers.md`, and any in-progress return are untouched.

## Workflow Overview

The toolkit follows a 7-step pipeline. Each step is a separate skill that can be run independently.

```
tax-setup    →  Interview taxpayer, fetch current tax law, create workspace
      ↓
tax-parse    →  Parse source documents (PDFs, XLSX, CSV), in parallel where supported
      ↓
tax-compute  →  Compute all form values from parsed data
      ↓
tax-generate →  Generate PDF summary from computed results
      ↓
tax-verify   →  Cross-reference against tax software return
      ↓
tax-review   →  EA + IRS officer perspective review
      ↓
tax-fbar     →  Prepare FBAR filing document (if applicable)
```

**You don't have to run all steps.** Each skill checks its prerequisites and tells you what to run first if something is missing.

## Skill Reference

| Skill | What It Does | Prerequisites |
|---------|-------------|---------------|
| `tax-setup <year>` | Interview taxpayer, fetch tax brackets/rates from IRS.gov, create `taxpayer_profile.json` and `tax_law_<year>.json` | None |
| `tax-parse` | Parse all source documents (W-2, 1099s, bank statements) into structured `summaries/*.md` files | `tax-setup`, source docs in workspace |
| `tax-compute` | Compute federal 1040 + state return line values, write to `result.json` | `tax-setup`, `tax-parse` |
| `tax-generate` | Generate a formatted PDF summary of the return | `tax-compute` |
| `tax-verify <software.pdf>` | Compare computed values against a tax software return (FreeTaxUSA, TurboTax, etc.) line by line | `tax-compute`, software PDF |
| `tax-review` | Dual-perspective review: Enrolled Agent (missed deductions, optimization) + IRS officer (audit risk, red flags) | `tax-compute` (recommended: `tax-verify` first) |
| `tax-fbar` | Prepare FBAR (FinCEN 114) filing document for foreign bank accounts | `tax-compute`, foreign account data |

Beyond the core pipeline, the toolkit ships lifecycle and planning skills:
`tax-archive` and `tax-rollover` (year-over-year carryforwards),
`tax-extend` (Form 4868) and `tax-amend` (1040-X), `tax-calendar`
(quarterly reminder `.ics`), and the read-only planners `tax-project`,
`tax-withholding`, `tax-scenario`, `tax-roth-convert`, `tax-harvest`,
`tax-retirement`, and `tax-ptet`, plus `tax-parse-crypto` for exchange CSVs.

`tax-safe-harbor` stands apart from all of them: it answers one question — will an
underpayment penalty attach, and what closes it — from figures off last year's
Form 1040, with no workspace, no tax-law cache and no engine. It can do that because
the prior-year prong of §6654 is the one calculation here whose parameters are
statutory and unindexed, so there is no current-year figure available to go stale. It
computes only that prong, which can overstate what you owe and never understate it;
paying above it is credited against the tax actually due, not money lost.

See [`AGENTS.md`](AGENTS.md) for the architecture, and [the skill catalog](docs/contributing/skill-catalog.md) for each skill's design notes and scope limits.

### Useful Options

These are natural-language instructions your agent interprets, not parsed CLI flags. You can also express the same intent conversationally (e.g., "only parse the foreign documents").

```
tax-parse --group foreign     # Parse only foreign-language documents
tax-compute --refresh         # Re-fetch tax law data before computing
tax-review --perspective ea   # Enrolled Agent review only
tax-review --perspective irs  # IRS officer review only
```

## What You'll Need

Gather these documents before starting `tax-parse`:

**Income:**
- W-2 (wages)
- 1099-INT / 1099-DIV (interest and dividends)
- 1099-B (brokerage — stock sales, capital gains)
- 1099-NEC / 1099-MISC (freelance, contract work)
- 1099-R (retirement distributions, rollovers)
- K-1 (partnership, S-corp, trust income)

**Deductions & Credits:**
- 1098 (mortgage interest)
- 1098-T (tuition)
- 1098-E (student loan interest)
- Charitable donation receipts
- Childcare expense receipts (for dependent care credit)

**Other:**
- Prior year tax return (for carryforward amounts)
- State tax refund info (1099-G)
- HSA contribution records (Form 5498-SA)
- Foreign bank/brokerage statements (for FBAR)
- Tax software return PDF (for `tax-verify`)

**Supported formats:** PDF, XLSX, CSV. Place files in the `documents/` directory.

## Security & Privacy

This toolkit is designed to handle sensitive tax data safely.

### What's Protected

**Data handling:**
- Full SSNs are never stored — only last 4 digits (`XXX-XX-1234`)
- All taxpayer data files are gitignored (`taxpayer_profile.json`, `result.json`, `answers.md`, `summaries/`)
- Output files are set to owner-only permissions (`chmod 600`)

**Pre-commit hooks (installed by `agents/install.sh`):**

Three layers run on every commit. They are git hooks, so they run host-side and
fire on a plain `git commit` from any agent — they do not depend on the agent
cooperating, or on being the same agent that installed them:

1. **Branch guard** — blocks direct commits to `main`/`master`. Hard block — commit is rejected with instructions to create a feature branch.

2. **File and path blocker** (`scripts/pre-commit-pii-scan.sh`) — blocks sensitive file types (PDF, XLSX, CSV, images, archives), sensitive filenames (`taxpayer_profile.json`, `result.json`, `tax_law_*.json`, `answers.md`, `FBAR_*.md`, `holdings.json`, `parse_ledger.json`, …), and anything under the runtime workspace directories (`documents/`, `summaries/`, `reviews/`, `scenarios/`, `projections/`, `archive/`). Those directories are gitignored, but `git add -f` bypasses `.gitignore` — this rule does not. Hard block — commit is rejected.

3. **Content scanner** (same script) — scans the *added* lines for full SSNs in every written form — separated by a hyphen, a run of spaces, a tab, a dot or a slash, or run together as nine contiguous digits and high-confidence credentials (AWS, OpenAI/Anthropic `sk-…`, GitHub `ghp_…` and `github_pat_…`), and rejects the commit if it finds one. It reports the file name and a match count, never the value itself. `.pii-allowlist` — read from the *staged* snapshot, so an unstaged local edit cannot widen it — suppresses known-synthetic values such as `000-00-0000`; the masked `XXX-XX-1234` storage format never matches. A file git classifies as binary is rejected rather than skipped, since its content cannot be scanned. Hard block — commit is rejected.

CI runs the same script against the pull request, inspecting **each commit individually** rather than the net diff — so a secret added in one commit and deleted in a later one is still caught, and a commit made with `--no-verify` cannot slip through. Any git command the scan cannot run is an error, never a clean result.

**Pre-push hook:**
- Runs the same scanner over every changed file in every commit the push would *publish*, not just the one you last made — so a value added in one commit and removed in a later one is still caught before it leaves the machine, and a commit made with `--no-verify` gets a second look here. A clean branch pushes normally: `git push -u origin <branch>`. It also refuses a push straight to `main`.

**CI (GitHub Actions):**
- Runs pytest, PII file scan, and pip-audit on every PR to `main`
- Dependabot monitors dependencies weekly for known vulnerabilities

### Recommendations

- Enable full-disk encryption (FileVault on macOS, BitLocker on Windows)
- Work in a private repository
- Delete taxpayer data files after filing season
- Review commits before pushing — the pre-push hook scans them, but only you can see what a figure means

## Contributing

### Setup

```bash
git clone https://github.com/yarikmsu/tax-prep-skills.git
cd tax-prep-skills
# `run.sh` owns the layout — it activates the workspace venv, `bin/` on POSIX or
# `Scripts/` on Windows, and execs the interpreter that venv installed. One form
# in one place: this step was written out four times in three spellings before,
# in the change that introduced the launcher to own exactly this. Chained, so a
# failed venv step stops here rather than installing the pinned tree into the
# HOST interpreter; not `|| exit 1`, which would close an interactive shell this
# was pasted into.
python3 -m venv .venv &&
  sh agents/lib/run.sh -m pip install -r requirements.txt &&
  sh agents/lib/run.sh -m pip install pytest
bash agents/install.sh
```

### Running Tests

```bash
# Chained: several agents open a fresh shell per command, so an activation on
# its own line does not reach the next one — pytest would run on the host
# interpreter, where the suite's dependencies are not installed.
source .venv/bin/activate && python3 -m pytest tests/ -v
```

The suite (2,000+ tests) covers the tax computation functions with hand-computed expected values from IRS worksheets, plus the archive/rollover, projection, scenario, harvesting, calendar, retirement, PTET, and crypto-importer modules. [What each block of the suite guarantees](docs/contributing/test-invariants.md) carries the exact current count.

### Project Structure

```
.agents/
└── skills/           # The skills, one dir per skill:
                      #   <name>/SKILL.md        the body — always loaded
                      #   <name>/references/*.md step detail — read on demand
                      # (discovered natively by Codex/Cursor/Kimi/Copilot;
                      #  reached by Claude Code via .claude/skills/ symlinks)
agents/
├── templates/        # JSON/MD templates for workspace initialization
├── lib/              # Shared Python utilities
│   ├── parse_utils.py        # PDF/Excel/CSV extraction
│   ├── tax_law_fetcher.py    # Tax law cache management
│   ├── preflight.py          # Engine-presence gate (run first by every skill)
│   └── tax_computations/     # Parameterized tax math (state/ per state)
└── install.sh        # Git hooks, gitignore, optional global skill install
AGENTS.md             # Project truth for every agent (security, architecture)
CLAUDE.md             # Claude Code specifics; imports AGENTS.md
docs/
├── contributing/     # agents-compat, skill-catalog, test-invariants,
│                     # tax-gotchas, state-module-playbook
├── changelog/        # Per-epic implementation history
├── roadmap/          # BACKLOG.md — the one backlog
└── superpowers/      # Feature specs and implementation plans
scripts/
└── pre-commit-pii-scan.sh   # File-type blocker + content scanner
tests/
├── test_tax_computations.py     # tax math functions (the largest suite)
└── test_*.py                    # per-module suites (rollover, projection, ptet, …)
.claude/
└── skills/           # Relative symlinks -> ../../.agents/skills/<name>
                      # (project-local discovery for Claude Code and Cursor)
.vscode/settings.json # Points VS Code Copilot at .agents/skills
.github/
├── workflows/ci.yml  # CI pipeline (pytest + PII scan + pip-audit)
└── dependabot.yml    # Weekly pip dependency monitoring
.pii-allowlist        # False-positive suppression for the PII scanners
requirements.txt      # Pinned runtime dependencies
```

### Key Design Principles

- **No hardcoded personal data** — taxpayer info lives in `taxpayer_profile.json`, never in skill code
- **No hardcoded tax law** — skills fetch current rates/brackets from IRS.gov at runtime, cached in `tax_law_<year>.json`
- **Modular** — each skill works independently; inapplicable sections are skipped
- **Parallel** — document parsing dispatches subagents grouped by institution/language
- **A skill without its engine refuses to run** — the hard gates, and every planning skill's computation, are scripts in `agents/lib/`, a sibling of `.agents/skills/` that does not travel when a skill is copied. All 20 taxpayer-facing skills that reach the engine open with `sh agents/lib/run.sh agents/lib/preflight.py`; the exception, `tax-safe-harbor`, calls no engine module at all and says so in a declaration a test checks rather than trusts; a failure to run it *is* the block, since the script cannot report its own absence. It checks every engine module and refuses to vouch for an interpreter the engine will not run on

### Git Workflow

1. Create a feature branch
2. Make changes and commit (pre-commit hooks run automatically)
3. Push: `git push -u origin <branch>` (the pre-push hook scans the outgoing commits and passes a clean branch; `--no-verify` skips the PII scan too, so use it only in an emergency)
4. Open a PR — CI runs pytest + PII scan + pip-audit
5. All checks must pass before merging

### Modifying Computation Functions

All functions in `agents/lib/tax_computations/` take rates/brackets as parameters — never hardcode values. Run the self-test after changes:

```bash
sh agents/lib/run.sh -m agents.lib.tax_computations
```

## License

Apache-2.0. See [LICENSE](LICENSE). The disclaimer below is not modified by it.

## Disclaimer

This toolkit is a computational aid, not tax advice. It does not replace a qualified tax professional. Always verify results against licensed tax software and consult a CPA or Enrolled Agent for complex situations. The authors are not responsible for errors in tax filings prepared using this toolkit.
