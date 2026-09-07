# Epic W — Engineering hygiene and maintainability

Backlog: `docs/roadmap/2026-09-05-staff-agent-review.md` (adopted 2026-09-05).
Step 9 of that file's sequencing: W1, W4 and W5 as a single hygiene PR, takeable
at any point. Taken ahead of step 4, which is blocked on the Part 5 rulings.

## W1 + W4 + W5 — Lint, types, the slow marker and the interpreter pin — PR #90

**Defect:** no linter, no type checker, no `pyproject.toml`, and the local venv
follows whatever `python3` is — 3.14 on a current macOS, against a lock compiled
for 3.12, which AGENTS.md §7 itself warns about. Twelve pyflakes findings sat on
`main` unnoticed, one of them a `pytest.skip` in a module that never imported
`pytest`: on a machine without `jq` that test raised `NameError` instead of
skipping, and the only machines that ran the line were the ones where it was
never reached.

**Shipped:**
- `pyproject.toml` — tool configuration only, with no `[project]` table: this
  repository is not a distributable package, and `agents/lib/__init__.py`
  refuses to import outside the workspace virtualenv, which a wheel installed
  elsewhere would defeat.
- **ruff, at the pyflakes-equivalent set (`F`) and nothing wider.** The default
  selection reports about nine hundred findings here, nearly all of them style
  opinions nobody has ruled on; a gate that fires nine hundred times on the day
  it lands is the gates-that-cry-wolf shape PR #69 already paid for. The 52 `F`
  findings were cleared in the same commit that turned it on. Widening is one
  rule family at a time, with that family fixed in the same change.
- **mypy, gradual, with a baseline that is a LIST OF MODULES.** Annotated code
  is checked and unannotated code is not, so the engine does not have to be
  typed before the gate can run. Eleven modules carry `ignore_errors` with the
  count they had when the gate landed; every module NOT on that list is
  enforced, so a new annotated contract in one of them fails CI, and a module
  leaves the list by being cleared. A suppressed rule set would have hidden the
  same errors everywhere instead.
- **The `F` findings, cleared.** Eighteen unused imports, twenty-nine f-strings
  with no placeholders, four unused locals, and the undefined `pytest` above.
  The two `se_earnings` locals in the SE-tax tests became the worksheet comments
  they always were, rather than being deleted: the derivation is what AGENTS.md
  §8 asks a changed assertion to carry.
- **`slow` marker (W4)** on `tests/test_claude_hooks.py` and
  `tests/test_preflight.py`, the two modules that execute real scripts in a
  subprocess once per case. `-m "not slow"` is the engine-only run — 3,165 of
  the suite's cases, against 505 skipped. It is an iteration convenience, not a
  gate: CI runs everything, and the marker skips the modules, not the risk.
  pytest-xdist is NOT adopted here — the hook module's cases share the stamp
  file, the git worktree and the process cwd, so they have to be isolated before
  a trial means anything, which is the order W4 itself prescribes.
- **`.python-version` (3.12) + a `uv` recipe (W5).** `uv venv` reads the pin and
  `uv pip sync requirements.txt` installs exactly the lock with its hashes
  verified. CONTRIBUTING.md carries both; AGENTS.md is at the 32 KiB chain cap
  Codex truncates at, so nothing was added there.
- **CI gains a `lint` job** on 3.12, with `ruff` and `mypy` PINNED — a linter is
  the one dependency whose new release adds findings, so an unpinned one turns
  an unrelated PR red on a rule nobody here has ruled on. W6 folds them into the
  hashed dev lock that covers pytest too.

**Not in scope, deliberately:** W2 (splitting the monoliths) and W3 (the hook
state machine) are separate items; the mypy baseline names what they would have
to clear. The 131 mypy findings behind the baseline are unreviewed — some look
like real defects (`crypto_importer.py:562` assigns a float where a dict is
declared) and each deserves its own change, not a bulk fix under a hygiene PR.

**Review round (Codex, local, against `main`):** one finding, fixed. The `uv`
recipe stopped at `uv pip sync`, which makes the environment EXACTLY the lock —
the point of it, and also why it removes a test runner the lock does not pin. A
contributor following the recipe had no pytest and could not run the test
command three sections below it. `uv pip install pytest` now follows the sync,
the way the venv recipe above it already installs pytest as its own step.

