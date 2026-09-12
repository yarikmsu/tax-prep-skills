# Contributing

Contributions are welcome. Please read `AGENTS.md` first — it is the operating contract
for this repository and it is ordered by criticality, security first.

**The best first contribution is adding a state.** See [Adding a state](#adding-a-state)
below; the playbook and a spec template already exist, so you are not starting from a
blank page.

## Before anything else: three rules that override everything

1. **Never commit taxpayer data, and never put it in an issue or pull request.** Not a
   W-2, not a return PDF, not a full SSN, not an account number. Use synthetic values.
   See `SECURITY.md`.
2. **Never hardcode a tax rate, bracket, cap, or threshold.** Every computation function
   takes them as parameters; they are fetched at runtime and cached. A stale figure
   produces a wrong return that looks entirely plausible, and nothing downstream catches
   it.
3. **Never credit an AI assistant as an author.** No commit message, pull-request title
   or body, or committed file may carry a generated-with note, a co-author trailer, or a
   session link.

## Setup

```bash
git clone https://github.com/yarikmsu/tax-prep-skills.git
cd tax-prep-skills
python3 -m venv .venv &&               # chained: a failed venv step must stop here,
  sh agents/lib/run.sh -m pip install -r requirements.txt &&
  sh agents/lib/run.sh -m pip install pytest
                                       # or the launcher finds no activate script
                                       # and installs into the HOST interpreter.
                                       # pytest is not a runtime dependency, so
                                        # it isn't pinned in requirements.txt — CI
                                        # installs it the same way, as a separate step
bash agents/install.sh --hooks-only    # git hooks + .gitignore entries
```

This step is not optional. It installs the pre-commit hook that blocks PII and runtime
files from reaching a commit — the gate that protects you from an accident, not a
formality.

`--hooks-only` is the right form for every agent. The skills are discovered where they
already live: Codex, Cursor, Kimi Code and Copilot read `.agents/skills/` in this clone,
and Claude Code reaches the same directories through the tracked `.claude/skills/`
symlinks. Dropping the flag additionally populates `~/.claude/skills/` — global state
outside this clone, useful only if you want the skills available in *other* directories
under Claude Code, and inert under anything else. On a Windows clone without developer
mode, where symlinks fail, that global install needs `--copy`.

**Use Python 3.12.** `.python-version` names it, the lock is compiled for it, and CI's
linters run on it. A bare `python3 -m venv` follows whatever `python3` happens to be —
on a current macOS that is 3.14, and the interpreter alone decides some results, which
is why CI runs both. With [uv](https://docs.astral.sh/uv/) installed, the pinned
interpreter and the locked tree come from two commands that read `.python-version` and
`requirements.txt` themselves:

```bash
uv venv                                # 3.12, per .python-version
uv pip sync requirements.txt           # exactly the lock, hashes verified
uv pip install pytest                  # AFTER the sync: `sync` makes the
                                       # environment exactly the lock, which is
                                       # the point of it and also why it removes
                                       # a test runner the lock does not pin
```

New here? Run `docs/QUICKSTART.md` first. It produces real figures from bundled
fixtures in about fifteen minutes and needs no personal data.

## Tests

```bash
source .venv/bin/activate && python3 -m pytest tests/ -q
```

- **Add tests for new behavior.** Expected values are hand-computed from IRS
  worksheets — when you change an assertion, update its worksheet comment to match.
- **A green local suite is not a green CI.** Your virtual environment follows whatever
  `python3` is; the lock is compiled for 3.12 and CI runs **3.12 and 3.14**, because the
  interpreter alone decides some results.
- `docs/contributing/test-invariants.md` records what each block of the suite pins down
  and which rule breaks if an assertion is weakened. Read the relevant entry before
  changing a test that looks redundant — several of them are load-bearing in
  non-obvious ways.
- Every module under `agents/lib/` exposes a `_main` CLI. Four of them
  (`tax_computations`, `secure_fs`, `irs_dates`, `crypto_importer`) run a built-in
  self-test when invoked with no arguments — `sh agents/lib/run.sh -m agents.lib.tax_computations`
  is the one to run after touching the math.
- **`-m "not slow"` is the engine-only run.** Two modules — the Claude hooks and
  preflight — execute real scripts in a subprocess once per case, and they are most of
  the suite's wall clock; the marker lets you iterate on the engine without them. Run
  the whole suite before you push: the marker skips the modules, not the risk.

## Lint and types

```bash
ruff check .          # the pyflakes-equivalent set, and it is green
mypy -p agents        # annotated code, against a named per-module baseline
```

Both are configured in `pyproject.toml` and run in CI on 3.12, with the versions pinned
there — a linter is the one dependency whose new release adds findings. `ruff`'s rule
selection is deliberately narrow (`F`): the default set reports about nine hundred
findings here, nearly all of them style opinions nobody has ruled on, and a gate that
fires nine hundred times on arrival is one contributors learn to reason past. Widen it
one rule family at a time, fixing that family in the same change. `mypy`'s baseline is a
LIST OF MODULES, not a suppressed rule set: every module not named in `pyproject.toml`
is enforced, and a module leaves the list by being cleared.

## Dependencies

**`requirements.txt` is generated — edit `requirements.in`.** The `.in` file holds the
direct dependencies; the `.txt` is a compiled lock pinning the whole transitive tree
with hashes. After editing the `.in`, regenerate with exactly these flags and commit
both files:

```bash
uv pip compile requirements.in --generate-hashes --universal \
    --python-version 3.12 -o requirements.txt
```

Changing `--python-version` or dropping `--universal` silently changes what the lock
covers.

## The PII gate

`scripts/pre-commit-pii-scan.sh` is the single place these rules live. It runs on every
commit through the hook, and again in CI over each commit in the pull request
individually.

If you need a synthetic SSN-shaped or credential-shaped value in a test or fixture, add
it to `.pii-allowlist` — and **stage that entry in the same commit as the value, or an
earlier one.** The allowlist is read from each scanned commit, so allowlisting a value
afterwards does not clear the commit that introduced it.

The emergency bypass is `git commit --no-verify`. Use it deliberately, never routinely;
CI will still scan what you push.

The gates' `validation_receipt.json` is a runtime artifact like the rest — gitignored, owner-only, and blocked by name at the gate. Its digests are not sensitive; the PATHS it names are the taxpayer's own documents, and a listing is a disclosure (AGENTS.md §1). It is named in AGENTS.md §6 like every other runtime artifact. It was left out of that list while the file sat ten bytes under its cap, which Y5 records as the wrong trade: a cap is a reason to relocate an ANSWER, never a reason to leave a runtime artifact undeclared.

## Branching and pull requests

- **Always work on a feature branch.** The pre-commit hook blocks direct commits to
  `main`.
- Push with `git push -u origin <your-branch>`. The pre-push hook scans the changed
  files of every commit the push would publish, and the branch or tag NAME, and passes a
  clean branch; it refuses a push straight to `main`. `--no-verify` on a push skips this
  hook — it is the same flag that skips the commit-time scan, spelled the same way, so
  reach for it deliberately in either place.
- Write commit messages that explain *why*, not just what.
- Open a pull request against `main`. CI runs pytest on 3.12 and 3.14, the PII scan, and
  `pip-audit`; all three must pass.
- **Automated PR review is off** (2026-09-07). A maintainer reviews the diff by hand;
  driving a change to a clean pass is still part of opening it.

## Adding a state

State support is California-first: CA Form 540 is computed in depth, Florida is modeled
as a no-income-tax regime, and other states are bracket-estimate only. Adding a state is
explicitly community territory and the path is mapped:

- `docs/contributing/state-module-playbook.md` — the procedure.
- `docs/superpowers/specs/TEMPLATE-state-module.md` — the spec template to fill in.

Start by writing the spec. State tax law has more discontinuities than people expect,
and the template exists because the ordering of those questions is what makes the
implementation tractable.

A state regime is *typed*: a state with no income tax is a positive declaration
(`type: "none"`), never an empty section. An absent section reads as "not implemented
yet" and that ambiguity is what the type removes.

## Working on skills

Skills are Markdown instructions for an AI agent, not executable code.

- **A skill is a directory**, `.agents/skills/<name>/`, holding `SKILL.md` and
  optionally `references/*.md` for step detail that is read on demand. Reference files
  are exactly one level deep and always linked from the body — never nested, never
  orphaned.
- Frontmatter `name` must equal the directory name, `description` must be non-empty, and
  `license` is `Apache-2.0`. Every agent keys discovery on the first two.
- Keep them **agent-neutral**: describe the capability needed ("search the web",
  "dispatch parallel sub-tasks if supported"), not a vendor's tool name, and always give
  the degrade path for an agent that lacks it.
- `SKILL.md` is at most 500 lines — the cap is inclusive, and one shipped skill sits
  exactly on it. Past that, split step detail into `references/`.
- Match the structure of an existing skill of the same kind, including its opening
  gates. Those gates are load-bearing and a test asserts several of them verbatim.

## The review standard

One principle governs severity here, and it will come up in review:

**Bias direction decides how bad a bug is.** When data is missing or ambiguous, a silent
fallback that *overstates* the number a taxpayer acts on is a hard error. One that
*understates* it may degrade, but only with a loud banner. Never guess a value that
changes a return, and never let a degraded path look like a normal one.

If you are unsure whether something is in scope, open an issue and ask before building.
