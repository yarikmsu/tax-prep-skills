# Tax Prep Skills for Claude Code

Reusable, taxpayer-agnostic tax preparation toolkit for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Provides `/tax-*` slash commands that guide Claude through the full US federal + state return pipeline — from document parsing through IRS-perspective review.

Works for any filing status and any tax year. **State support is California-first:** the CA Form 540 is computed in depth (Schedule CA adjustments, exemption credits, mental-health surcharge); other states get a bracket-based estimate only, and NYC, part-year-resident, and nonresident returns are not modeled. The skills are state-parameterized, so adding another state is a well-scoped contribution — see the [state-module playbook](docs/contributing/state-module-playbook.md) for the pattern (fetched-law block, result section, compute functions, registrations, tests). PRs welcome.

> **This toolkit handles sensitive financial data.** All taxpayer information stays local on your machine. Full SSNs are never stored — only the last 4 digits. Pre-commit hooks block accidental commits of sensitive files. See [Security & Privacy](#security--privacy) for details.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI installed and authenticated
- Python 3.10+ with `pip`
- Git (for version control and PII protection hooks)
- `gh` CLI (optional — for creating pull requests)

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/yarikmsu/tax-prep-skills.git
cd tax-prep-skills

# 2. Set up Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Install skills and hooks
bash agents/install.sh

# 4. Start Claude Code and begin
claude
```

Once inside Claude Code:

```
/tax-setup 2025
```

This starts the taxpayer interview and creates your workspace.

## Workflow Overview

The toolkit follows a 7-step pipeline. Each step is a separate skill that can be run independently.

```
/tax-setup    →  Interview taxpayer, fetch current tax law, create workspace
      ↓
/tax-parse    →  Parse source documents (PDFs, XLSX, CSV) via parallel subagents
      ↓
/tax-compute  →  Compute all form values from parsed data
      ↓
/tax-generate →  Generate PDF summary from computed results
      ↓
/tax-verify   →  Cross-reference against tax software return
      ↓
/tax-review   →  EA + IRS officer perspective review
      ↓
/tax-fbar     →  Prepare FBAR filing document (if applicable)
```

**You don't have to run all steps.** Each skill checks its prerequisites and tells you what to run first if something is missing.

## Skill Reference

| Command | What It Does | Prerequisites |
|---------|-------------|---------------|
| `/tax-setup <year>` | Interview taxpayer, fetch tax brackets/rates from IRS.gov, create `taxpayer_profile.json` and `tax_law_<year>.json` | None |
| `/tax-parse` | Parse all source documents (W-2, 1099s, bank statements) into structured `summaries/*.md` files | `/tax-setup`, source docs in workspace |
| `/tax-compute` | Compute federal 1040 + state return line values, write to `result.json` | `/tax-setup`, `/tax-parse` |
| `/tax-generate` | Generate a formatted PDF summary of the return | `/tax-compute` |
| `/tax-verify <software.pdf>` | Compare computed values against a tax software return (FreeTaxUSA, TurboTax, etc.) line by line | `/tax-compute`, software PDF |
| `/tax-review` | Dual-perspective review: Enrolled Agent (missed deductions, optimization) + IRS officer (audit risk, red flags) | `/tax-compute` (recommended: `/tax-verify` first) |
| `/tax-fbar` | Prepare FBAR (FinCEN 114) filing document for foreign bank accounts | `/tax-compute`, foreign account data |

Beyond the core pipeline, the toolkit ships lifecycle and planning skills:
`/tax-archive` and `/tax-rollover` (year-over-year carryforwards),
`/tax-extend` (Form 4868) and `/tax-amend` (1040-X), `/tax-calendar`
(quarterly reminder `.ics`), and the read-only planners `/tax-project`,
`/tax-withholding`, `/tax-scenario`, `/tax-roth-convert`, `/tax-harvest`,
`/tax-retirement`, and `/tax-ptet`, plus `/tax-parse-crypto` for exchange CSVs.
See `CLAUDE.md` for the full architecture.

### Useful Options

These are natural-language instructions interpreted by Claude, not parsed CLI flags. You can also express the same intent conversationally (e.g., "only parse the foreign documents").

```
/tax-parse --group foreign     # Parse only foreign-language documents
/tax-compute --refresh         # Re-fetch tax law data before computing
/tax-review --perspective ea   # Enrolled Agent review only
/tax-review --perspective irs  # IRS officer review only
```

## What You'll Need

Gather these documents before starting `/tax-parse`:

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
- Tax software return PDF (for `/tax-verify`)

**Supported formats:** PDF, XLSX, CSV. Place files in the `documents/` directory.

## Security & Privacy

This toolkit is designed to handle sensitive tax data safely.

### What's Protected

**Data handling:**
- Full SSNs are never stored — only last 4 digits (`XXX-XX-1234`)
- All taxpayer data files are gitignored (`taxpayer_profile.json`, `result.json`, `answers.md`, `summaries/`)
- Output files are set to owner-only permissions (`chmod 600`)

**Pre-commit hooks (installed by `agents/install.sh`):**

Three layers run on every commit:

1. **Branch guard** — blocks direct commits to `main`/`master`. Hard block — commit is rejected with instructions to create a feature branch.

2. **File type blocker** (`scripts/pre-commit-pii-scan.sh`) — blocks commits containing sensitive file types (PDF, XLSX, CSV, images, archives) and sensitive filenames (`taxpayer_profile.json`, `result.json`, `tax_law_*.json`, `answers.md`, `FBAR_*.md`). Hard block — commit is rejected.

3. **Regex content scanner** (`.claude/hooks.json` PreCommit hook) — scans staged diffs for SSN patterns, email addresses, phone numbers, API keys, and secrets. Advisory — output is shown to Claude, who warns you if PII is detected. Allowlist (`.pii-allowlist`) suppresses known false positives.

**Pre-push hook:**
- Blocks all automated pushes — Claude prints the push command for you to run manually after reviewing

**CI (GitHub Actions):**
- Runs pytest, PII file scan, and pip-audit on every PR to `main`
- Dependabot monitors dependencies weekly for known vulnerabilities

### Recommendations

- Enable full-disk encryption (FileVault on macOS, BitLocker on Windows)
- Work in a private repository
- Delete taxpayer data files after filing season
- Review commits before pushing — the pre-push hook reminds you

## Contributing

### Setup

```bash
git clone https://github.com/yarikmsu/tax-prep-skills.git
cd tax-prep-skills
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
bash agents/install.sh
```

### Running Tests

```bash
source .venv/bin/activate
python3 -m pytest tests/ -v
```

The suite (1,000+ tests — see CLAUDE.md for the current count) covers the tax computation functions with hand-computed expected values from IRS worksheets, plus the archive/rollover, projection, scenario, harvesting, calendar, retirement, PTET, and crypto-importer modules.

### Project Structure

```
agents/
├── skills/           # Skill markdown files (symlinked to .claude/skills/)
├── templates/        # JSON/MD templates for workspace initialization
├── lib/              # Shared Python utilities
│   ├── parse_utils.py        # PDF/Excel/CSV extraction
│   ├── tax_law_fetcher.py    # Tax law cache management
│   └── tax_computations.py   # Parameterized tax math
└── install.sh        # Install skills, hooks, and gitignore
scripts/
└── pre-commit-pii-scan.sh   # File type blocker (called by pre-commit hook)
tests/
├── test_tax_computations.py     # tax math functions (the largest suite)
└── test_*.py                    # per-module suites (rollover, projection, ptet, …)
.claude/
└── hooks.json        # Claude Code PreCommit hook (regex PII scanner)
.github/
├── workflows/ci.yml  # CI pipeline (pytest + PII scan + pip-audit)
└── dependabot.yml    # Weekly pip dependency monitoring
.pii-allowlist        # False positive suppression for PII scanning
```

### Key Design Principles

- **No hardcoded personal data** — taxpayer info lives in `taxpayer_profile.json`, never in skill code
- **No hardcoded tax law** — skills fetch current rates/brackets from IRS.gov at runtime, cached in `tax_law_<year>.json`
- **Modular** — each skill works independently; inapplicable sections are skipped
- **Parallel** — document parsing dispatches subagents grouped by institution/language

### Git Workflow

1. Create a feature branch
2. Make changes and commit (pre-commit hooks run automatically)
3. Push manually: `git push --no-verify -u origin <branch>` (`--no-verify` bypasses the pre-push hook, which intentionally blocks all pushes to force manual review)
4. Open a PR — CI runs pytest + PII scan + pip-audit
5. All checks must pass before merging

### Modifying Computation Functions

All functions in `agents/lib/tax_computations.py` take rates/brackets as parameters — never hardcode values. Run the self-test after changes:

```bash
python3 agents/lib/tax_computations.py
```

## Disclaimer

This toolkit is a computational aid, not tax advice. It does not replace a qualified tax professional. Always verify results against licensed tax software and consult a CPA or Enrolled Agent for complex situations. The authors are not responsible for errors in tax filings prepared using this toolkit.
