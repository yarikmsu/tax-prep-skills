#!/usr/bin/env bash
#
# Claude Code PostToolUse hook — matcher Write|Edit|Bash.
#
# Editing a skill or an agent definition is editing PROSE, and this repository
# couples prose to pytest. The neutralization lint, the 500-line body cap, the
# description length cap, the one-level-deep references rule, the capability-flag
# interview check and the auditor's schema paths all live in tests/, and NOTHING
# signals on a Markdown save — no linter, no formatter, no type checker is
# configured here. A broken invariant is therefore discovered at CI, one push and
# one review round after it was introduced.
#
# This is a local fast signal, never the enforcement backstop. The git hooks
# installed by agents/install.sh remain that, because they work for Codex, Cursor,
# Kimi and Copilot too; a Claude Code hook does not. That ranking decides every
# trade-off below: where this hook must choose between missing one signal and
# firing on work it has nothing new to say about, it misses the signal, because
# CI still has it and a gate that cries wolf gets reasoned past or removed.
#
# THE HOOK FIRES ON CHANGE, NOT ON STATE. Both of the properties that makes it
# fast are consequences of that one sentence:
#
#   - A Bash call is opaque — the payload is a command, not a path — so the hook
#     cannot ask "did this call touch a skill?". It asks the filesystem "has any
#     watched path changed since the last completed pass?" instead, via a stamp
#     file and `find -newercm`, the same mechanism harden-runtime-perms.sh uses
#     for the same reason. Answering that costs a small fraction of a full
#     pass, and MEASURED COSTS below is the one place that says how small. The
#     tempting alternative — the previous behaviour — was to run the checks
#     after EVERY Bash call, which made `ls`, `git status` and every pytest
#     invocation pay that full run and, on a loaded machine, exceed the
#     registered timeout.
#     Claude Code then CANCELS the hook and the agent sees nothing at all, which
#     is precisely the "a hook that never fires looks identical from outside"
#     failure this file exists to avoid.
#
#   - A red result is reported once per change, not once per tool call — and the
#     module that came back red is REMEMBERED, so that suppressing the repeat
#     does not also suppress the coverage. When the checks are red for something
#     the agent is midway through fixing, repeating the same failure on every
#     subsequent `git diff` and `pytest` obstructs exactly the commands needed to
#     diagnose it. The finding has been delivered; the next CHANGE to a watched
#     path re-runs and re-reports it, and re-runs the still-red module with it
#     even when that change selects a different part of the repository entirely.
#     See "what the pass is allowed to remember" below for why both halves are
#     needed and why either alone is a defect.
#
# Exit codes are the interface:
#   0  nothing watched changed, or the environment cannot run the checks — silent
#   1  a signal killed the pass before it finished. It has no verdict to report,
#      so it says nothing and must not block the tool call. ALSO (AA9): a GREEN
#      pass that used three quarters or more of its registered timeout — the
#      stamp has advanced, the warning is on stderr, and it does not block. The
#      two are told apart by stderr: empty for the signal, the warning for this
#   2  the checks failed, or could not produce a verdict; stderr is fed back to
#      the agent for a same-turn fix
# `+exP` FIRST, in the very first statement, because all three arrive from an
# exported SHELLOPTS and all three matter before another line runs. See "A GATE
# INHERITS ITS ENVIRONMENT" below for what each one does to this hook.
set +exP -uo pipefail
# Then keep out an inherited FUNCTION; see the list below.
POSIXLY_CORRECT=y
case $-:${GATE_REEXEC_PID-} in
    *p*:"$$") unset GATE_REEXEC_PID POSIXLY_CORRECT ;;
    *) GATE_REEXEC_PID=$$ exec "$BASH" -p -- "$0" "$@" ;;
esac
umask 077

# ---------------------------------------------------------------------------
# A GATE INHERITS ITS ENVIRONMENT. Y6 found this class in the PII gate, where a
# variable turned its verdict into a no-op (PR #119); this is the same class in
# the hook that guards every other change. Each one below was REPRODUCED before
# it was fixed, and each has a test that goes red when its guard is removed.
#
#   - PYTEST_ADDOPTS, which is the one that turns this gate into a rubber
#     stamp. pytest appends it to every invocation, so an exported
#     `--collect-only` makes the suite exit 0 having executed no assertion at
#     all, and `-k` narrows it to whatever the pattern keeps. The hook reads
#     exit 0, ADVANCES the change boundary and CLEARS the red carry-forward,
#     which is the worst outcome this file has: a pass that measured nothing,
#     recorded as a pass that measured everything.
#   - PYTEST_PLUGINS, which loads a plugin by module name into every pytest
#     this hook runs. A plugin is arbitrary code in the collection hooks, so
#     one implementing `pytest_collection_modifyitems` DROPS whatever it likes
#     before anything runs: reproduced with a four-line module against a suite
#     holding one failing test and one passing one, `1 failed, 1 passed`
#     became `1 passed` and the exit status went to 0. Same worst outcome as
#     PYTEST_ADDOPTS and reached a different way, which is the argument for
#     the stopping rule below rather than for a longer list.
#   - PYTHONOPTIMIZE, which deletes `assert` statements outright. pytest
#     rewrites the assertions in the tests it collects, so the suite itself is
#     covered — but several of these modules launch engine modules as
#     SUBPROCESSES and read an exit status, and nothing rewrites those. A
#     self-test whose assertion has been compiled away exits 0 and the check
#     reports a pass. Reproduced: a module asserting something false exits 1
#     normally and 0 under `PYTHONOPTIMIZE=1`.
#   - `errexit`, which bash imports from an exported SHELLOPTS and which
#     `set -u` does not turn off. The whole verdict rests on
#     `out=$(run_focused_modules)` returning so its `$?` can be read; under
#     errexit a red suite kills the script at that line instead — exit 1 with
#     an empty stderr, which this script's own interface defines as "a signal
#     killed the pass", so a genuine failure is reported as no verdict and the
#     tool call is not blocked. Reproduced.
#   - `physical`, which makes `cd` resolve symlinks. `$PWD` becomes the real
#     path while the payload's `file_path` is still the logical one, the
#     `"$PWD"/*` prefix below stops matching, and the edit is dropped as
#     "outside the project" — silently, exit 0, over a watched file. `/tmp`
#     resolving to `/private/tmp` was enough to reproduce it, and a symlinked
#     checkout is ordinary.
#   - `xtrace`, imported the same way, which is NOISE rather than a wrong
#     verdict and is turned off for the reason this file ranks above most
#     others: exit 2 feeds stderr back to the agent, and a trace of the whole
#     script buries the failing tail underneath it — 535 traced lines against
#     the thirty the report itself is, counted as lines beginning `+` so the
#     report is not counted twice. A gate whose output cannot be read is one
#     that gets reasoned past. Cleared in the first statement because bash
#     traces every line up to whatever turns it off; the one line it cannot
#     avoid is that statement, which is what the test names.
#   - CDPATH, and two different failures from it. `cd` PRINTS the directory it
#     landed in whenever CDPATH found it, so any `$(cd … && pwd)` comes back as
#     TWO lines. Worse, CDPATH is searched BEFORE the relative path, so
#     `cd "$CLAUDE_PROJECT_DIR"` with a RELATIVE value can land in a different
#     clone entirely — measured, `CDPATH=/tmp/x` sends `cd sub` to
#     `/tmp/x/sub` — after which this script reads another repository's files,
#     writes its stamp there, and reports a verdict about a tree nobody edited.
#     (A bare `.` is safe; bash special-cases it. A relative name is not.)
#   - A FUNCTION, exported into the environment (`export -f`) or defined by the
#     file BASH_ENV names. Bash imports both before this script's first line,
#     and a function runs ahead of the PATH lookup, so either stands in for any
#     command here: a stub `jq` leaves the payload unread, and the hook exits 0
#     silently over a red suite. Reproduced. Privileged mode (`-p`) imports no
#     function and reads no BASH_ENV, so the script re-runs itself under it,
#     SECOND rather than first: an inherited xtrace traces every line up to the
#     statement that clears it, and the test holds that to one. The form of
#     the re-run is part of the guard, each line closing a way past it, and
#     scripts/pre-commit-pii-scan.sh explains it once. It covers this
#     script's own commands and not the suite's, since the functions stay in
#     the environment pytest inherits. Bash 3.2, the macOS /bin/bash, still
#     reads CDPATH in that mode, so the line after this list still clears it.
#
# NOT A COMPLETE SWEEP, and the stopping rule matters more than the list. The
# environment is unbounded the way bash grammar is: every round of review finds
# one more variable, so "enumerate them all" has no end and a list that reads as
# complete is worse than one that says it is not. The threat model is AN
# ENVIRONMENT A CONTRIBUTOR'S SHELL PLAUSIBLY HAS — `set -P` in a profile,
# PYTEST_ADDOPTS exported for a debugging session, a stray RUN_ variable. A
# finding outside that is refuted by citation rather than fixed.
#
# REFUTED under it, and recorded so it is not re-found: `SHELLOPTS=noexec` makes
# bash parse this script and execute none of it, so the hook exits 0 having done
# nothing — and NO `set` here can repair that, because the repair would itself
# not execute. It is also not a state anything survives: it makes every script
# on the machine a no-op. `PYTHONPATH`, and a `pytest.ini` on a parent path, are
# likewise outside the list, and both for reasons now established rather than
# assumed.
#
# PYTHONPATH is half of a reproduction rather than none of one — it is one of
# the ways the module PYTEST_PLUGINS names becomes importable — but it produces
# no silent pass BY ITSELF, an installed package serves the same purpose, and
# unsetting it would change what the suite can import for reasons that have
# nothing to do with this gate. The bar stays where it is: reproduced as a
# silent pass on its own.
#
# A `pytest.ini` ON A PARENT PATH REPRODUCES IN GENERAL AND IS REFUTED HERE,
# which is a better answer than the "plausible, never reproduced" this line
# carried. Measured: a parent directory holding `[pytest]` with
# `addopts = --collect-only` makes a failing suite report `1 test collected`
# and exit 0 — the rubber stamp again, from a file rather than a variable, and
# nothing in this script could unset it. What stops it is that pytest's rootdir
# search halts at the FIRST inifile it finds, and this repository has one:
# `[tool.pytest.ini_options]` in pyproject.toml. With that section present the
# ancestor's file is never read; with it deleted, it is. Measured both ways.
#
# So the protection is real and it is ACCIDENTAL — that section exists to hold
# a `markers` list, and nobody moving the markers elsewhere would know they
# were removing a guard. `test_the_project_inifile_stops_an_ancestor_pytest_ini`
# is what makes it deliberate.
#
# Selection state is local Python data now; inherited RUN_* variables cannot
# declare checks. Keep the shell environment cleanup for subprocesses too.
# ---------------------------------------------------------------------------
CDPATH=''
unset PYTEST_ADDOPTS PYTEST_PLUGINS PYTHONOPTIMIZE
for _inherited in $(compgen -v 2>/dev/null); do
    case "$_inherited" in RUN_*) unset "$_inherited" ;; esac
done
unset _inherited

# No jq means no way to read the hook payload. A hook that cannot identify its
# target must not guess, and silence beats a broken session.
command -v jq >/dev/null 2>&1 || exit 0

payload=$(cat) || exit 0
tool_name=$(printf '%s' "$payload" \
    | jq -r '.tool_name // empty' 2>/dev/null) || exit 0
file=$(printf '%s' "$payload" \
    | jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0
unset payload

# Resolve beside the script before moving to the project; synthetic hook tests
# and calls from another working directory must use the same runtime.
RUNTIME="$(cd -- "$(dirname -- "$0")" >/dev/null && pwd)/lib/hook_runtime.py"
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
[ -x .venv/bin/python ] || exit 0

WATCHED_ROOTS='
.agents/skills
.claude/agents
.claude/hooks
.claude/settings.json
.claude/skills
.github/ISSUE_TEMPLATE
.github/workflows/ci.yml
.gitignore
.pii-allowlist
.vscode/settings.json
AGENTS.md
CLAUDE.md
CONTRIBUTING.md
LICENSE
README.md
agents
docs/QUICKSTART.md
docs/WHO-IT-FITS.md
docs/contributing
docs/ops
docs/roadmap
pyproject.toml
requirements.in
requirements.txt
scripts
tests
'

# Q12 bootstrap: retain the FILE boundary and the no-interpreter Bash fast path.
# The marker is the pass START, backdated one second for coarse timestamp ticks.
# CTIME against stamp MTIME catches chmod and writes that preserve mtime.
# Deleted watched roots remain the documented CI-only gap: scanning the clone
# root would turn every taxpayer runtime write into a cold contributor pass.
STAMP=".claude/.skill-invariants-stamp"
STAMP_VERSION='2'
FORCE_FULL=0
STAMP_WRITABLE=1
TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/skill-invariants.XXXXXX" 2>/dev/null) || TEMP_DIR=''
CHANGED_LIST="${TEMP_DIR:+$TEMP_DIR/changed}"
NOW_MARKER="${TEMP_DIR:+$TEMP_DIR/now}"
cleanup() {
    [ -n "$TEMP_DIR" ] && rm -rf "$TEMP_DIR" 2>/dev/null || true
}
trap cleanup EXIT
trap 'cleanup; exit 1' TERM INT HUP

backdate_marker() {
    touch -A -000001 "$1" 2>/dev/null || touch -d '1 second ago' "$1" 2>/dev/null
}

# Trust belongs to the cheap bootstrap. Raw sed + command substitution removes
# NULs but RETAINS CR: a CRLF version is untrusted, never normalized to version 2.
# Refusal forces all checks; unsafe directories/links also forbid stamp writes.
if [ -L .claude ] || [ ! -d .claude ] || [ -L "$STAMP" ]; then
    STAMP_WRITABLE=0
    FORCE_FULL=1
elif [ -z "$NOW_MARKER" ] || ! : > "$NOW_MARKER" 2>/dev/null \
     || ! backdate_marker "$NOW_MARKER"; then
    STAMP_WRITABLE=0
    FORCE_FULL=1
elif [ ! -f "$STAMP" ] \
     || [ "$(sed -n '1p' "$STAMP" 2>/dev/null)" != "$STAMP_VERSION" ] \
     || [ "$STAMP" -nt "$NOW_MARKER" ]; then
    FORCE_FULL=1
fi

scan_changed_paths() {
    local root
    local -a roots=()
    [ -n "$CHANGED_LIST" ] || return 1
    while IFS= read -r root; do
        [ -n "$root" ] || continue
        if [ -e "$root" ] || [ -L "$root" ]; then
            roots[${#roots[@]}]=$root
        fi
    done <<< "$WATCHED_ROOTS"
    [ "${#roots[@]}" -gt 0 ] || return 1
    find "${roots[@]}" -newercm "$STAMP" -print0 > "$CHANGED_LIST" 2>/dev/null
}

if [ "$FORCE_FULL" -eq 0 ] && ! scan_changed_paths; then
    FORCE_FULL=1
fi
if [ "$tool_name" = Bash ]; then
    display_target='a Bash tool call'
    relative_path=''
    if [ "$FORCE_FULL" -eq 0 ] && [ ! -s "$CHANGED_LIST" ]; then
        # No interpreter start, and red checks do not turn ls into a retry.
        exit 0
    fi

else
    case "$file" in "$PWD"/*) relative=${file#"$PWD"/} ;; *) exit 0 ;; esac
    relative_path=$relative
    case "$relative" in
        .agents/skills/*|.claude/skills/*|.claude/agents/*)
            display_target='a repository skill or agent definition' ;;
        *) display_target='a watched repository source file' ;;
    esac
fi

# No edited path (or tool command) on argv: this pipe carries NUL-framed private
# input. exec transfers both verdict and signal cleanup to the runtime, including
# ownership of TEMP_DIR. The fallback is a visible no-verdict block, never green.
# An unrelated Write/Edit exits after exact-path selection. Watched edits union
# the pending filesystem scan; without a reliable boundary they run all checks.
[ -s "$RUNTIME" ] || {
    echo 'Skill invariant checks could not RUN — the hook runtime is unavailable' >&2
    exit 2
}
shopt -s execfail
# Compile/load failures and a truncated runtime are NO VERDICT, not a green
# Python process that never called main. The entry guard contains no decisions.
HOOK_RUNTIME_SCRATCH=$TEMP_DIR exec .venv/bin/python -c '
import os, runpy, shutil, sys
try:
    sys.exit(runpy.run_path(sys.argv[1])["main"]())
except Exception:
    print("Skill invariant checks could not RUN — the hook runtime failed", file=sys.stderr)
    sys.exit(2)
finally:
    scratch = os.environ.pop("HOOK_RUNTIME_SCRATCH", "")
    if scratch:
        shutil.rmtree(scratch, ignore_errors=True)
' "$RUNTIME" < <(
    printf '%s\0' "$tool_name" "$FORCE_FULL" "$STAMP_WRITABLE" \
        "$TEMP_DIR" "$STAMP_VERSION" "$display_target" "$relative_path"
    [ "$FORCE_FULL" -eq 0 ] && cat "$CHANGED_LIST"
)
echo 'Skill invariant checks could not RUN — the hook runtime is unavailable' >&2
exit 2



# An explicit module list, in the same spirit as EXPECTED_SKILLS: additions are a
# prompt rather than busywork, and `tests/` wholesale is far too slow to pay on
# a save (MEASURED COSTS below states by how much, once).
# `test_the_hook_runs_test_files_that_exist` pins that each path resolves — a
# rename would otherwise make pytest exit 4 (usage error) and report
# "invariants FAILED" on every skill edit while `pytest tests/` was green.
#
# ---------------------------------------------------------------------------
# MEASURED COSTS. This block is the ONE owner of every runtime figure about this
# hook. Cite it by name — "skill-invariants.sh MEASURED COSTS" — rather than
# copying a count out of it into a doc, a comment or a test docstring: a
# quantity kept in three places goes stale in three places. A RATIO may be
# quoted with the name beside it, as CLAUDE.md allows, because a ratio here
# changes only when its words do.
#
# IT RECORDS COUNTS, RATIOS AND ORDERING, AND NO ABSOLUTE TIMING. That is the
# owner's ruling Q13 (docs/roadmap/BACKLOG.md, 2026-09-21), and the reason is
# the record this block used to carry. `pytest tests/` on one host read more
# than a third slower from one sitting to the next, on a suite that had gained
# six tests, with no code change to explain it. The gap between its slowest and
# fastest readings ever is wider, but the suite doubled between those two, so
# that gap is mostly growth and no evidence either way. And the precondition an
# absolute needs — every row in ONE sitting, on an otherwise-idle machine —
# failed three of the sittings on record: one to edits landing mid-sweep, one to
# host memory, and the most recent to an endpoint scanner that wakes on exactly
# what a pytest pass does, a feedback loop no time of day fixes. Nothing that
# READS this block NEEDED an absolute: every check and decision rests on a ratio
# or an ordering, and the absolutes once copied into comments elsewhere were
# stale restatements, not inputs. So the absolutes were a debt this host could
# not pay, recording weather, for no consumer. Git history holds every sitting
# and its readings. `test_the_hook_states_no_absolute_timing` holds the rule:
# every numeral in this block is one of its counts, none of the vocabulary of
# duration appears here, the ordinal included, and no number with a unit appears
# in any comment elsewhere in this file.
#
# TWO THRESHOLDS ARE COMPARED, and neither lives here, because a budget is an
# input to this block and not a copy of it: the hook's registered timeout in
# .claude/settings.json, and the floor
# `test_the_skill_hook_has_headroom_over_its_measured_runtime` holds it above —
# a per-collected-test budget times the selected modules' collected count, with
# an absolute minimum under it. That test is what enforces HEADROOM, and
# it needs no stopwatch: collection is deterministic, so the floor moves with
# the suite and not with the machine. Overrunning the timeout reports NOTHING —
# Claude Code cancels the hook, and a cancelled check reads to the agent exactly
# like one that passed — which is why the floor is derived rather than hoped.
#
# THE ONE ABSOLUTE STILL TAKEN is a cold pass, re-timed when a CONSTANT above
# changes — the registered timeout, the per-test budget or the floor's absolute
# minimum — and only then: never because an added test moved the derived
# floor. It is compared against the timeout in the change that moves it. It
# is a check tied to an event, not a row: the reading goes in that change's
# description and not here. It is also the one thing the budget cannot see —
# a per-test cost that climbs while the count stands still.
#
# A RATIO IS RE-TAKEN AS A PAIR, both invocations back to back, three runs each,
# so whatever the machine is doing lands on both sides of it. Every ratio here
# is stated "roughly" for the same reason, and a ratio lifted from here to three
# significant figures would be wrong by the next reading. The figures below come
# from the last complete sitting, taken with W3 slice two (#164, 2026-09-17); a
# ratio is re-taken when its row's membership moves far enough to change the
# WORDS, never to refresh a decimal. Timing is wall clock of the invocation —
# interpreter start and collection included — because that is what the tool call
# waits on. The fast-path row is this hook END TO END, the real script fed a
# Bash payload; every other row is its pytest invocation alone, since the hook's
# own work ahead of pytest is that fast-path row again.
#
# MEASURING THE FAST-PATH ROW HAS A TRAP, which cost two full passes to find and
# two more to a reader who had this paragraph in front of them. It needs a
# stamp NEWER than every watched file, and a bare `touch` on the stamp does not
# produce one: the trust ladder above backdates `$NOW_MARKER` and treats
# `[ "$STAMP" -nt "$NOW_MARKER" ]` as untrustworthy, so a stamp dated this
# instant forces a FULL pass. Take the runs with no touching at all, after a
# pass has already written a trustworthy stamp; a reading two orders of
# magnitude off its neighbours is this trap, not the machine.
#
#   what triggers it               what runs     cost, relative to a cold pass
#   ---------------------------------------------------------------------------
#   nothing watched changed        no pytest     under a thousandth
#   agent-definition edit           48 tests     roughly a hundred-and-fiftieth
#   skill .md edit                1099 tests     about a seventh
#   cold pass, all seven modules  1405 tests     one
#   (for scale) `pytest tests/`   every module  about twice
#
# The agent-definition row is the agents row below — that trigger selects that
# module alone — so the two state one count, and a test holds them to it.
#
# COUNTS ARE WHAT RUNS, not what collects. Two places bite: `run_focused_modules`
# passes two `--deselect` flags, so portability collects 262 and runs 260; and
# the hooks module holds two platform skips, so it collects 214 and runs 212.
#
# THE SCALE ROW STATES NO COUNT, on purpose (AA8). Its one job since ruling Q13
# is "about twice" a cold pass, which no exact count serves, and pinning the
# whole suite's count here made every change that added a test ANYWHERE edit
# this file — an edit that selects the hooks module, the dominant row below.
# Only the seven modules this hook selects are counted, and only they are
# pinned. It is the PLAIN invocation, without the two deselections.
#
# DECOMPOSITION — each module timed alone, portability carrying the same two
# deselections run_focused_modules passes. WHICH module dominates, and by how
# much, is the half of a timing that survives a change of machine.
#
#   tests/test_claude_hooks.py     212 tests    the dominant row
#   tests/test_portability.py      260 tests    about a tenth of the hooks row
#   tests/test_preflight.py        383 tests    a twelfth to a twentieth of it
#   tests/test_secure_fs.py        189 tests    under a fiftieth of it
#   tests/test_host_guard.py        94 tests    under a fiftieth of it
#   tests/test_capabilities.py     219 tests    under a fiftieth of it
#   tests/test_claude_agents.py     48 tests    under a fiftieth of it
#
# ORDERING, and only as far as the readings support one: the hooks module, then
# portability, then preflight, then the other four, among which no order is
# claimed. They sit within about a factor of three of each other, and they
# regroup between sittings: capabilities overlapped secure_fs and the host guard
# in one and came in below both in the next, with nothing touched between. An
# order that holds for one sitting is weather, and the prose here asserted one
# twice on ranges that overlapped.
#
# WHERE THE COST IS is one module. A hook can only be tested by running it — the
# hooks module spawns bash more than once per case and holds deliberate sleeps,
# and all of that waiting IS an assertion — so a cold pass is mostly that
# module, and a skill edit skips it; the trigger table states what that buys,
# once. When this gets tight the lever is that module's own runtime, not this
# list and not the timeout; parallelism is the honest lever left, and it needs
# a dependency the lock does not carry.
#
# ARITHMETIC, which is the half that is EXACT: 48 + 219 + 189 + 260 + 383 = 1099
# for a skill edit, plus 94 + 212 = 1405 for a cold pass, both matching the
# trigger table to the test. `test_the_measured_costs_counts_match_live_
# collection` collects every module and re-derives both sums, the parts
# included, so a stale count is a red suite rather than a figure a reader has to
# distrust on their own. It is left out of HOOKS_DEPENDENTS on purpose, so an
# edit to one of the other test modules does not run it: it goes red the moment
# a test is added to one of the seven, which is correct and is also a moment
# nothing can be done about, mid-round. Every edit that runs the hooks module,
# an edit to that module included, still runs it.
#
# THE TRIGGER WITH NO ROW is an edit to one of the six test modules armed with
# those ids. It selects the edited module, the host-guard module, and the four
# node ids in `HOOKS_DEPENDENTS` — two modules and the ids. An edit to the host
# guard itself makes the first two one module, and its arm adds portability to
# make two again, because `TestTheEngineDirectoryIsFlat` reads that module's
# roster by name. Never the hooks module those ids sit in. The ids cost between
# a seventh and an eighth of the hooks module they stand in for: two of them
# drive the real hook once per watched path, a subprocess each, and that is all
# of it. So a save of the host-guard module pays more than ten times its own
# row, portability being what it is attached to. No row, because what it runs is
# two modules plus ids, and a row would have to state a count that is no
# module's. Other test files are armed differently: an edit to the hooks module
# runs it and the host guard, `tests/conftest.py` runs a cold pass, the two
# shared helpers run the modules that import them, and any other file under
# tests/ runs the host guard alone.
#
# SPREAD is what justifies filtering at all: the distance from the first table's
# fast-path row to its cold-pass row, stated there and only there. The previous
# behaviour ran the checks after every Bash call, which made `ls` and
# `git status` pay the cold pass rather than the fast path.
# ---------------------------------------------------------------------------
#
# Running the union of every changed path's modules, rather than only the
# triggering path's, is what makes it sound to advance the stamp afterwards:
# every path the scan saw has had its modules run.
#
# KNOWN GAP, stated rather than implied: tests/test_tax_computations.py and
# tests/test_tie_out.py each assert against tax-parse/SKILL.md prose, and both
# are far too slow to run here. Editing that one skill still has CI as its only
# signal.
#
# WHAT THE TEST-MODULE ARMS COST, since it is a real trade and the paragraph
# here has now argued both sides of it as current policy. test_claude_hooks.py
# READS every one of the seven module files — its watched-set coverage test
# opens them and recovers every `REPO / "literal"` path they name — and NAMES
# one of them, the deselect pin resolving both `--deselect` ids against
# tests/test_portability.py's own source. So adding such a literal to any of the
# seven turns the hooks module red while that module's own tests stay green,
# which is the CI-only failure this hook exists to convert into a local one, and
# it is why all six OTHER arms select the hooks check.
#
# SIX of them select `RUN_HOOKSDEPS`, which is that dependency and nothing
# else: the assertions in that module that reach the other six. Only
# tests/test_claude_hooks.py itself takes `RUN_HOOKS`. The whole module was the
# policy for three rounds and the bill was the dominant row in the DECOMPOSITION
# above, so a save of the file this branch grows fastest paid that module on top
# of its own row for a handful of assertions. That is the fast path this hook exists to
# protect, and a gate that slow on routine work is one that gets switched off,
# which is the outcome no assertion constrains.
#
# What those ids cost INSTEAD is not nothing, and this paragraph used to
# imply it was by quoting the preflight-to-hooks ratio — which is about the
# module the narrowing spares. The figure is THE TRIGGER WITH NO ROW, above,
# stated once and not restated here. How MANY ids there are is stated there
# too, and only there: this paragraph said "three" for as long as that one did,
# both of them counting a list that had grown to four.
#
# The COUNTS check is deliberately not among them, and that is a cost decision
# in the other direction: it goes red the moment a test is ADDED anywhere in
# the suite and stays red until MEASURED COSTS is refreshed in several places,
# which is a hand edit a round can only make once its tests are final.
# Selected here it blocked every tool call in between — the gates-that-cry-wolf
# pattern, and the same judgement the `--deselect` pair below records. See
# HOOKS_DEPENDENTS.
#
# The bound is PINNED in both directions, because a narrowing goes wrong
# silently in one of them: `test_the_hooks_dependents_are_real_and_complete`
# requires every node id to resolve EXACTLY to a test that exists and the
# membership to be the pinned SET — a lower bound over two hardcoded names stood
# here first and could not see the one dependent that was missing — and
# `test_a_test_module_edit_does_not_pay_for_the_whole_hooks_module` is the upper
# bound, a lower bound being satisfied by an arm that selects everything, so
# cost is the only thing that catches an arm widened back.
#
# What was considered and declined is a filtered subset reported AS the hooks
# module's verdict, which is a different thing: a subset standing in for a
# module is how a check comes to mean less
# than it says. The selection is PINNED, in both directions, so neither reading
# of this trade can be applied to the arms by hand:
# `test_the_watched_set_still_covers_what_the_modules_read` recovers the seven
# module-name constants out of test_claude_hooks.py's own source and requires an
# edit to each of those paths to select the hooks module, and
# `test_a_template_edit_does_not_pay_for_the_hooks_module` holds the upper bound
# for the two templates that sit in the same `case` list and must NOT.
#
# The two deselected assertions are the registry pair, and they are excluded
# because they necessarily fail while a NEW skill is being written — the
# directory exists before its EXPECTED_SKILLS entry and its symlink do. Left in,
# the hook reports a failure on correct in-progress work for every edit until
# three more edits land, which is the gates-that-cry-wolf pattern that teaches an
# agent to reason past a gate's output. CI and the review still enforce both.
