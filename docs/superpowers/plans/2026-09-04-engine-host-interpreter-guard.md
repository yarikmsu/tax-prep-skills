# Engine Host-Interpreter Guard — Implementation Plan

> **For agentic workers:** small enough for a single session, no spec — this file is
> the spec. Steps use checkbox (`- [x]`) syntax for tracking. Library change first,
> consumers second, docs third; each commit green on its own.

**Goal:** Make the "every engine call goes through `sh agents/lib/run.sh`" rule
(AGENTS.md §4) enforced by the ENGINE rather than by a documentation audit — so a
bare `python3 -m agents.lib.X`, `python3 agents/lib/X.py`, `python3 -c "from
agents.lib import …"` or a heredoc fed to a stock interpreter fails loudly at import
time whenever a workspace `.venv` exists, in every spelling bash allows, including the
ones no audit has thought of yet.

**Why now:** the launcher audit in `tests/test_preflight.py` is a hand-written bash
parser. On `ops/validate-review-subagents` it grew 3518 → 9509 lines (170 → 212 tests)
across ~20 review rounds, and each round found one more corner of bash grammar the
approximation did not cover — because bash grammar is effectively unbounded relative
to a regex, and every fix added parser surface for the next round to find bugs in.
The last round's prescribed fix (join implicit `|` continuations) would itself have
fail-opened on 69 markdown table rows and a YAML `run: |` in the fenced corpus. The
loop is net-negative on that file. This plan closes the CLASS in ~20 lines of engine
code, then freezes the parser.

**Architecture:** one function in `agents/lib/__init__.py`, called at package import.
It re-states the core of `preflight._venv_error` — `<engine root>/.venv` exists AND
`sys.prefix` is not that directory → block with exit 2 — anchored the way preflight
and `run.sh` anchor, on the file's own location. Preflight is exempt (it must be able
to REPORT this defect with its full remedy, and under `-m` the package imports before
preflight's own code runs). Everything else that imports the package inherits the
guard for free: 20 of the 22 engine modules already `from agents.lib import …` after
their `sys.path.insert`, so the script-path form is covered too; `secure_fs.py` is the
one leaf that gets a two-line import.

**Tech stack:** Python 3.10+ stdlib only (`sys.orig_argv` is 3.10+, which is
`MIN_PYTHON`). No new dependencies, no change to `run.sh`.

**Threat model (write it down, it is what ends the review loop):** an HONEST
contributor or agent running the engine off the launcher by mistake. Not an adversary:
anyone who wants to bypass the guard can delete `.venv`, and the invariant is about the
wrong interpreter running a return, not about access control.

## Baseline (verified 2026-09-04 on `ops/validate-review-subagents` = `502fff5`)

- `agents/lib/__init__.py` is a one-line comment (34 bytes).
- `preflight._venv_error(workspace=None)` (`agents/lib/preflight.py:1042`) holds the
  rule: `os.lstat(<root>/.venv)` ENOENT → `_NO_VENV`, clean; exists and
  `os.path.samefile(sys.prefix, venv)` → clean; exists and not the same, or any
  OSError comparing/inspecting it → blocks. Constants `_NO_VENV`, `_UNCOMPARABLE`
  at `:387-396`. `_ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent`.
- `preflight._load` (`:902`) catches `SystemExit` alongside `Exception` and reports it
  as a module failure — so an unexempted guard would not crash preflight's path form,
  but would print the same failure 21 times. Under `-m agents.lib.preflight` the
  package imports FIRST, so without an exemption preflight never starts at all.
- Under `python3 -m pkg.mod`, `sys.argv[0]` is still `"-m"` while `pkg/__init__` runs
  (runpy imports the parent package before rewriting argv). `sys.orig_argv` is set
  before anything runs and holds `['python3', '-m', 'agents.lib.preflight', ...]` or
  `['python3', 'agents/lib/preflight.py', ...]`. That is the only reliable identity
  available at `__init__` time.
- Package-import coverage today (`grep -c "^from agents\.lib\|^import agents\.lib"`):
  every module ≥ 1 except `__init__.py`, `preflight.py` (uses `importlib` inside
  `_load`) and `secure_fs.py` (0 imports, 0 `sys.path.insert` — a true leaf, and the
  one AGENTS.md §1 tells every agent to run).
- CI (`.github/workflows/ci.yml:37-41`) installs into setup-python's interpreter with
  NO `.venv`, so the guard is silent there by the `_NO_VENV` branch. The same branch
  covers a fresh clone before `tax-setup` Step 6 creates the venv.
- `tests/test_preflight.py:2618-2650` carries `needs_workspace_venv`, a skip for four
  tests that only pass on the workspace venv. `tests/conftest.py` does not exist.
- The `.claude/hooks/skill-invariants.sh` hook and `agents/install.sh` invoke the
  `.venv` interpreter explicitly; unaffected.
- Suite: 625 passed / 2 platform skips across the four modules the branch touches.

## Design decisions (settled — do not reopen in review)

1. **Guard in `__init__.py`, not in each `_main`.** `_main` covers CLI forms only; the
   `-c` and heredoc forms (`from agents.lib import tax_computations; …`) are exactly
   the ones four shipped skill steps use through the launcher and the ones a bare
   interpreter would silently compute with. Package import is the one chokepoint
   every form passes.
2. **No `.venv` ⇒ no opinion.** Identical to preflight's `_NO_VENV`. A guard that
   demanded a venv unconditionally breaks CI and every pre-`tax-setup` clone — a gate
   that fires on a correct state teaches its reader to reason past it.
3. **Uncomparable ⇒ block.** A `.venv` that exists but cannot be compared (permission,
   dangling symlink, not a directory) blocks, as in preflight. Fail-closed is the
   §4 bias direction for a guard whose only cost is a contributor re-running through
   the launcher.
4. **Preflight is exempt by `sys.orig_argv`,** matching either `-m agents.lib.preflight`
   or a script path whose basename is `preflight.py`. Nothing else is exempt. No
   environment variable escape hatch: `run.sh` resets `TAXPY` precisely because an
   exported variable from the caller's shell would otherwise choose the interpreter,
   and an env opt-out here is the same hole.
5. **The rule is re-stated, not imported.** `__init__` importing `preflight` would pull
   `importlib.metadata`, `dataclasses` and 2,400 lines into every engine process and
   invert the dependency direction (preflight must stay runnable when the guard is
   wrong). The two copies are pinned equal by test over the three states that matter,
   the way `.gitignore` and `IGNORE_PATTERNS` are pinned as sets.
6. **The message names NO path.** `run.sh` drops `$0` from its own error for the reason
   AGENTS.md §1 gives: a workspace folder may carry a taxpayer's name or SSN, and
   `sys.prefix` is a path under it. The message says what failed and what to run —
   `sh agents/lib/run.sh …`, and `sh agents/lib/run.sh agents/lib/preflight.py` for
   the full diagnosis — and nothing about where.
7. **Exit code 2**, preflight's "blocked" status, so a skill branching on the status
   reads it the same way.
8. **The parser is frozen after this ships.** Its threat model goes into its module
   docstring; a finding outside that model is refuted by citation, not fixed.

## Tasks

### Task 1 — Guard + tests (one commit; library change only)

- [x] `tests/test_host_guard.py` (new). Test the FUNCTION with explicit arguments, not
      by monkeypatching `sys` — the module is already imported by the test process.
  - [x] `host_interpreter_defect(root, prefix, base_prefix, orig_argv)` returns `""`
        when `root/.venv` does not exist.
  - [x] Returns `""` when `prefix` is `root/.venv` (create the dir; pass it as prefix).
  - [x] Returns a non-empty string when `root/.venv` exists and `prefix == base_prefix`
        (no venv at all) — the stock-macOS `/usr/bin/python3` case.
  - [x] Returns non-empty when `prefix` is a DIFFERENT directory (another venv).
  - [x] Returns non-empty when `root/.venv` is a dangling symlink, and when it is a
        regular file — the `_UNCOMPARABLE`/not-a-directory branches, fail-closed.
  - [x] Returns `""` — exempt — for `orig_argv` of `[..., "-m", "agents.lib.preflight"]`
        and of `[..., "<anything>/preflight.py"]`, in the otherwise-blocking state.
        NOT exempt for `-m agents.lib.preflight_extra` or `not_preflight.py`
        (anchored match, not substring).
  - [x] The message contains `agents/lib/run.sh` and `preflight` and does NOT contain
        `str(prefix)` or `str(root)` — the no-path rule, asserted on the blocking
        message for every blocking state above.
  - [x] Agreement with preflight: for the three states (no venv / inside / outside),
        `bool(host_interpreter_defect(...))` equals `bool(P._venv_error(root)[0])`
        with `sys.prefix`/`sys.base_prefix` monkeypatched the way
        `test_preflight.py`'s `_pretend` does. Import `_pretend`'s approach, not
        the helper — keep the two files independent.
  - [x] Wiring, end to end, without touching the real tree: copy
        `agents/lib/__init__.py` to `tmp/agents/lib/__init__.py`, `mkdir tmp/.venv`,
        run `[sys.executable, "-c", "import sys; sys.path.insert(0, tmp);
        import agents.lib"]` → returncode 2, stderr holds the message, stdout empty.
        Same with `tmp/.venv` absent → returncode 0. This is the test that proves
        the module-level call exists; the function tests cannot.
  - [x] Coverage of the script-path form: every `agents/lib/*.py` except
        `__init__.py` and `preflight.py` contains a line matching
        `^(from agents\.lib import|import agents\.lib)` — static, so a new leaf
        module fails the day it is written. Exempt list is closed and named in the
        assertion message.
- [x] `agents/lib/__init__.py`:
  - [x] `host_interpreter_defect(root=None, prefix=None, base_prefix=None,
        orig_argv=None) -> str`, defaults read from `Path(__file__)` and `sys`.
        Order: exemption check → `os.lstat(root/".venv")` (ENOENT → `""`) →
        not a directory → `""`? NO: block (`design 3`) → `samefile` in `try/except
        OSError` → block on mismatch or error.
  - [x] Module bottom: `if _defect := host_interpreter_defect():
        print(_defect, file=sys.stderr); sys.exit(2)`. Docstring/comment states
        decisions 1, 2, 4, 5, 6 in the repo's voice — WHY, and the trap each one
        closes; the WHAT is the code.
  - [x] Keep the existing one-line description as the module's first line.
- [x] `agents/lib/secure_fs.py`: after the stdlib imports, the same two lines the other
      modules carry (`sys.path.insert(0, str(Path(__file__).resolve().parent.parent
      .parent))` then `import agents.lib  # noqa: F401 — host-interpreter guard`).
      Verify `sh agents/lib/run.sh agents/lib/secure_fs.py` with no arguments still
      self-tests (AGENTS.md §5) and `python3 -m agents.lib.secure_fs` still parses.
- [x] Run: `source .venv/bin/activate && python3 -m pytest tests/test_host_guard.py
      tests/test_preflight.py -q`. Then the whole suite once.
- [x] Commit: `feat: engine refuses a host interpreter at import when a workspace
      venv exists`. Body leads with why (the audit cannot enumerate bash), then the
      symptom it prevents (a return computed on an unpinned interpreter behind a green
      preflight), then the bullets.

### Task 2 — One message under pytest, and the dead skip (one commit; consumers)

- [x] `tests/conftest.py` (new): at import,
      `try: import agents.lib` / `except SystemExit as e: pytest.exit(str(e) or
      "engine host-interpreter guard", returncode=2)`. Conftest loads before any
      test module, so a suite run off the venv with a `.venv` present ends in ONE
      message instead of ~40 collection errors each carrying it. Comment says so.
- [x] Delete `needs_workspace_venv`, `_running_on_workspace_venv`,
      `_OUTSIDE_WORKSPACE_VENV` and `_WORKSPACE_VENV` from `tests/test_preflight.py`
      (`:2618-2650`) and the four `@needs_workspace_venv` decorators. They exist to
      make an off-venv failure legible; the conftest now makes it impossible. Check
      the `test_claude_hooks.py` watched-set scan (the comment at `:2618-2624`
      explains why the venv path was spelled through the library) is unaffected —
      run `tests/test_claude_hooks.py`.
- [x] Manual verification, recorded in the commit body: with `.venv` present, run
      `/usr/bin/python3 -m pytest tests/ -q` (or any non-venv interpreter that has
      pytest) → exactly one message, exit 2, no test collected. And
      `/usr/bin/python3 -m agents.lib.tax_computations` → exit 2, message, no
      self-test output. And `/usr/bin/python3 agents/lib/preflight.py` → preflight's
      OWN venv verdict, not the guard's (exemption works by path). And
      `/usr/bin/python3 -m agents.lib.preflight` → the same (exemption works by `-m`).
- [x] Commit: `test: exit once under pytest when the engine refuses the interpreter`.

### Task 3 — Docs, the threat model, and the freeze (one commit)

- [x] `AGENTS.md` §4, bullet "A skill without its engine must not compute": append one
      sentence — the engine also refuses to import on a host interpreter whenever a
      workspace `.venv` exists (`agents/lib/__init__.py`, exit 2), so a bare `python3`
      that slips past every audit still fails loudly; preflight alone is exempt so it
      can report the defect. `CLAUDE.md` imports AGENTS.md; nothing to mirror.
- [x] `docs/contributing/agents-compat.md` §"Reaching the engine: `agents/lib/run.sh`"
      (`:203`): one sentence on the guard and the no-`.venv` silence, so a reader on
      CI or a fresh clone is not surprised in either direction.
- [x] `docs/contributing/test-invariants.md`: a bullet for `test_host_guard.py`
      (what it pins: the three states, the preflight agreement, the wiring, the
      package-import coverage, the no-path message). Amend the `:132` bullet ("A
      shell construct is read as the shell reads it…") with one sentence: the launcher
      audit is now the SECOND line, and it is frozen — see the threat model in its
      module docstring.
- [x] `tests/test_preflight.py` module docstring: after P2, add a scope paragraph. In
      substance:
      > The launcher audits (`_bare_engine_units` and everything it calls) model an
      > HONEST author writing a bare interpreter call in a shape this corpus plausibly
      > contains. They are a documentation lint behind an engine that refuses a host
      > interpreter at import (`agents/lib/__init__.py`), not a security boundary, and
      > bash grammar is larger than any approximation of it here will be. A miss on a
      > shape no document in the tree writes is ACCEPTED and recorded below, not fixed;
      > a fix is taken only when the shape exists in the corpus or an author would
      > write it, the miss is silent, and the fix does not widen the parser. Known,
      > accepted: an implicit continuation after a trailing `|`; a `SEPARATOR` inside a
      > shell comment splitting a span before `_blank_comments` runs; `_piped_engine`
      > reading its downstream head without `_shell_unescape`, so `| \` + newline +
      > `python3 -` clears. Each is zero-for-zero against the corpus as of 2026-09-04.
- [x] Commit: `docs: state the engine guard and freeze the launcher audit`.

### Task 4 — Ship

- [x] Branch off `main` AFTER `ops/validate-review-subagents` merges (this plan's
      baseline is that branch; the docstring edits land on the 9509-line file).
- [x] Print the push command; open the PR with `gh pr create`; post `@codex review`.
- [x] Run the review loop UNDER THE STOPPING RULE: a finding is fixed only if it meets
      all three conditions in the threat model above; otherwise reply with a
      refutation citing this file's "Design decisions" section by number. Stop after
      two consecutive rounds in which every finding is refuted. Findings against the
      frozen parser are refuted by pointing at the docstring paragraph.
- [x] Merge (squash). Sync `main` with `git reset --hard origin/main`.

## Verification

- `source .venv/bin/activate && python3 -m pytest tests/ -q` — green on the venv.
- The four manual checks in Task 2, from a non-venv interpreter.
- CI green on 3.12 and 3.14 (no `.venv` on the runner — the guard must be silent).
- `sh agents/lib/run.sh agents/lib/preflight.py` in a workspace → unchanged verdict.
- `grep -rn "Claude\|Co-Authored" <every file touched>` → nothing.

## Out of scope (deliberately)

- Replacing the bash parser with a real one (tree-sitter-bash or similar). Worth it
  only if the audit ever needs to be more than advisory again; after this plan it does
  not.
- The two open review findings on the parser and the `_shell_unescape` asymmetry —
  recorded as accepted in the threat-model paragraph, not fixed.
- Any change to `run.sh`, `preflight.py`'s verdicts, or the skill texts.
- An environment-variable opt-out (decision 4).


## What actually happened (2026-09-04, PR #82)

Every task landed. Five things the plan did not foresee, recorded because the next
plan of this shape will meet them again.

1. **The pre-3.10 `sys.argv` fallback broke the exemption.** `sys.orig_argv` puts the
   interpreter at index 0 and `sys.argv` does not, so the walk skipped the program name
   and preflight blocked ITSELF on the 3.9 it exists to diagnose. Caught by
   `test_preflight.py::TestTheDiagnosisSurvivesAnOldInterpreter`, which is the
   end-to-end half of a case the new module's function tests could not see.
2. **Two spellings are knowingly unexempted**, both fail-closed with a working remedy
   in the message: preflight under an option that takes a separate value (`-X dev`),
   and — below MIN_PYTHON only — `-m agents.lib.preflight`, because the `sys.argv`
   fallback holds just `-m` while the package imports. Pinned by test as decisions.
3. **The guard found twelve real defects the plan was not looking for.**
   `test_loss_harvest.py` and `test_rollover.py` spawned the engine as a bare
   `"python3"` off PATH, so the suite's result depended on whether the shell had been
   activated: green under `source .venv/bin/activate`, ten failures under
   `.venv/bin/python -m pytest`. They take `sys.executable` now, and an AST sweep in
   `test_host_guard.py` fails the next one.
4. **The hook needed a seventh module row.** Not in the plan at all: a new test module
   goes in `select_modules_for` and through all seven check-name lists, and adding one
   moves `pytest tests/`'s collected count, which forces a full re-measurement of
   MEASURED COSTS. That sitting is most of the work of Task 2 and should be planned
   for next time.
5. **AGENTS.md is at its 28 KiB truncation cap** — 13 bytes of headroom. The §4
   sentence is paid for by three restatements removed, each still stated elsewhere: an
   illustrative parenthetical, a rule §2 and §8 both give, and a clause explaining an
   ordering the sentence above it already governs.

**One commit, not four.** The plan's per-task commits could not each be green: adding
the test module turns the hook's counts test red until MEASURED COSTS is re-measured,
and that measurement is only valid on the final tree. Four commits red on one assertion
would have been worse than one honest commit, and the branch squash-merges regardless.

### Review round 1 (Codex, P2, accepted and fixed)

**"Make preflight require the guard module."** An incomplete engine copy that
omits or zero-truncates `agents/lib/__init__.py` leaves `agents.lib` importable
as a NAMESPACE package: every sibling still loads, every `ENGINE_MODULES` entry
still passes, and the guard is gone — on exactly the partial copy preflight
exists to detect. Reproduced end to end; the sibling computed a bracket on a
host interpreter with no refusal.

Inside the threat model and fixed, against the stopping rule's three tests: the
shape is the one preflight was written for, the miss is silent, and the fix
widens no parser. `preflight._guard_error` now certifies the initialiser and
answers the guard on a known input in both directions, because a guard reduced
to `return ""` passes every existence check. The roster's two exclusions were
one line and are not alike — preflight cannot report its own absence, and
`__init__.py` can — so that line now says where the second one is checked.

### Review round 2 (Codex, P2, accepted and fixed)

**"Restrict the preflight exemption to the shipped script."** Decision 4 above
says the exemption is for preflight; the code said `os.path.basename(token) ==
"preflight.py"`, which grants it to any script anywhere of that name. With a
workspace `.venv` present, such a script could import the engine and compute on
the host interpreter with no refusal. An exemption whose predicate is wider
than its stated scope is a hole by construction, and the gap between the two
was invisible because both readings describe the same file in the only tree
anyone tests in.

Fixed by comparing identity — `os.path.samefile` against this package's own
`preflight.py`, the same rule `_venv_error` uses and for the same reason. A
symlink, a relative spelling and a differently-cased path still match; a
stranger's file never does; a path that cannot be stat'd is not exempt, because
unverifiable is not verified. Reproduced both ways before and after. Decision 4
should be read as amended: **by identity, not by basename.**

### Review round 3 (Codex, three P2, all accepted and fixed)

The last round: the loop was stopped here by instruction, not by a clean pass,
so these three were triaged on their merits and the review was not re-triggered.
All three are inside the threat model and all three meet the stopping rule's
three tests. Two are holes in this branch's own new enforcement, and the third
is this branch violating a rule it did not write.

**1. "Verify the guard's import-time wiring."** Truncate `agents/lib/__init__.py`
after its last `return` and every check round 1 added still passes:
`host_interpreter_defect` is defined, and answers both directions of the
known-answer probe correctly. What the cut removes is the module-level call —
the only thing that enforces anything. The package then imports cleanly on any
interpreter, behind a green preflight. This is round 1's own argument ("present
and certifying nothing") one level out, and it is the shape a partial copy
actually takes.

Fixed by `preflight._wiring_error`, which answers it the way `_probe` answers
the math: EXECUTE the initialiser — a copy of the real file, in a temp root
that has a `.venv` this interpreter is not, with `sys.orig_argv` set to a `-c`
argv so the preflight exemption does not make the probe vacuous — and require
`SystemExit(2)`. A source scan for the call was the cheaper option and is what
`_cli_dispatch_error` does for `__main__`; it is not enough here, because the
whole claim being tested is that present is not wired. Verified against four
states: truncated, `if False and …`, `sys.exit(1)`, and intact.

**2. "Consume option operands before identifying preflight."** `-X opt` and
`-W arg` spend the token that follows them. The walk skipped the option and
read that token as the program, so `python -X <this preflight> -c '<engine
call>'` put preflight's own path where a script was expected, took the
exemption, and imported the engine on the host with no refusal — reproduced end
to end. Round 2's `samefile` narrowing does nothing about it: the path really is
this preflight, in the wrong position. The docstring already promised this
behaviour ("an option that takes a separate value ends the walk unexempted");
the code did not implement it.

Fixed by failing closed on anything that can carry an operand: every long
option, and every short cluster whose LAST letter is one of `c m W X` — short
options bundle, so `-BX dev` spends the next token exactly as `-X dev` does.
Modelling CPython's option table was the alternative and was rejected: it would
have to stay complete against future interpreters to stay safe, and this
direction is safe while incomplete. The cost is that preflight run under such a
flag blocks itself; the launcher passes no options, and the refusal names the
script form, which is exempt everywhere.

**3. "Watch the suite-wide conftest."** `tests/conftest.py` is added by this
branch, runs before every module the hook selects, and can end a run before one
test executes. It was in neither `WATCHED_ROOTS` nor `select_modules_for`, and
there is no `tests` root — the modules are listed one by one — so an edit to it
reached `nothing_selected` and exited silently. CLAUDE.md states this rule ("a
new source file behind an assertion ALWAYS goes in `select_modules_for`, and in
`WATCHED_ROOTS` only where no entry already contains it"); the branch that added
the file did not follow it.

Fixed with an arm calling `select_every_module` — the honest selection for a
file whose blast radius is the whole table — and seven rows in
`WATCHED_SOURCE_PATHS`, one per module, so the claim is pinned rather than
asserted. The cost is the existing cold-pass row in MEASURED COSTS; no new row.
