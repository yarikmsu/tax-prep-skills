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
# RUN_ FLAGS, which this slice adds to the list because this slice is what
# makes them reachable. `select_modules_from` accepts a module name from the
# port by asking whether `RUN_<NAME>` exists — the check that keeps the roster
# from being copied a seventh time into this file — and "does the variable
# exist" is a question an EXPORTED `RUN_NEWMODULE=0` answers yes to. Reproduced
# against selector output `SELECTION1\nNEWMODULE`: the guard passed, the flag
# was set on a module nothing declares, and the hook exited 0 having run
# nothing. The loop below is what makes that check mean what its comment says
# it means — after it, a `RUN_` variable exists only if THIS FILE declared it.
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

# The port the two selection call sites run, resolved beside THIS SCRIPT and
# ABSOLUTIZED, before the `cd` below moves away from the frame `$0` is relative
# to.
#
# The `cd` is why the absolutize is not decoration. Assigning a RELATIVE string
# ahead of a `cd` pins nothing: invoked as `bash .claude/hooks/skill-invariants.sh`
# from a sibling directory with CLAUDE_PROJECT_DIR set elsewhere, the plain
# `dirname "$0"` form leaves a relative path that resolves against the PROJECT
# after the `cd` — a different file, or none. The comment here claimed the
# capture pinned it and the capture did not; `pwd` is what does.
#
# Script-relative, where the rest of this file is PWD-relative, and the honest
# reason is worth stating because it is not the one that sounds best. In
# PRODUCTION the two coincide: `.claude/settings.json` invokes this script
# inside CLAUDE_PROJECT_DIR, so `dirname "$0"` and `.claude/hooks` are the same
# directory, and `dirname` being textual they stay the same through a symlinked
# `.claude`. Where they DIVERGE is a copy of this script run against some other
# project, and there script-relative is the worse answer — it fails closed into
# running every module forever, where CWD-relative would have found that
# project's own selector. What it buys instead is the TEST HARNESS: sixty-odd
# tests drive the real script against synthetic workspaces holding no
# `.claude/hooks/lib/`, and under CWD resolution every one of them would
# measure the fail-closed branch rather than the selection it is asserting
# about. That is a harness debt paid in production code, and it is recorded as
# one rather than dressed up as a design property.
SELECTOR="$(cd -- "$(dirname -- "$0")" >/dev/null && pwd)/lib/hook_selection.py"

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

# ---------------------------------------------------------------------------
# The watched set, and which of the seven modules each watched path can turn red.
#
# DERIVED by reading those seven modules, not guessed. A guessed set is wrong in
# the direction that is invisible: a path left out produces no local signal,
# which is indistinguishable from a path that has no invariants to begin with.
# The previous filter accepted only `*.md` under the two skill directories and
# the agent directory, so editing agents/lib/tax_law_fetcher.py — the very
# change tests/test_claude_agents.py exists to catch, since it resolves every
# schema path the auditor names against create_empty_tax_law — signalled
# nothing, and neither did AGENTS.md, which tests/test_portability.py now reads.
# Both were reachable only through the unfiltered Bash branch, by accident.
#
# What each module actually reads, and therefore what has to select it:
#
#   tests/test_portability.py   .agents/skills/** and the .claude/skills
#                               symlinks (layout, frontmatter, the line and
#                               description caps, the reference-depth rule,
#                               `bash -n` over every fenced block), plus
#                               AGENTS.md, CLAUDE.md, README.md,
#                               docs/contributing/*.md, LICENSE, .gitignore,
#                               .pii-allowlist, .vscode/settings.json,
#                               agents/install.sh and
#                               scripts/pre-commit-pii-scan.sh.
#   tests/test_capabilities.py  agents/lib/capabilities.py,
#                               agents/templates/taxpayer_profile_template.json,
#                               and the tax-setup skill through skill_text() —
#                               every capability rule's flag must exist in the
#                               template AND be asked by the interview.
#   tests/test_claude_agents.py .claude/agents/*.md and
#                               agents/lib/tax_law_fetcher.py.
#   tests/test_claude_hooks.py  .claude/hooks/*.sh, .claude/settings.json, and
#                               the sensitive-filename case of
#                               scripts/pre-commit-pii-scan.sh, which it parses
#                               and compares against the permissions hook's list.
#   tests/test_preflight.py     .agents/skills/** through skill_text() (the gate
#                               heading, its fail-closed sentence, and the
#                               chained activation on every engine invocation),
#                               and EVERY module in agents/lib/ — check()
#                               imports all twenty-one and executes one, so a
#                               SyntaxError in any of them turns this module red.
#                               The directory itself is watched too: the roster
#                               is pinned against `agents/lib/*.py`, and an ADD
#                               shows up as the directory's own timestamp moving
#                               rather than as a path beneath it.
#   tests/test_host_guard.py    agents/lib/__init__.py — the host-interpreter
#                               guard — and the directory again, for the
#                               opposite reason preflight watches it: the
#                               coverage assertion is that every module under
#                               agents/lib/ except two IMPORTS the package, so
#                               an ADD is what can turn it red, and an add is
#                               the directory's own timestamp moving.
#
# agents/lib/rollover.py is watched for a reason none of those files names
# directly: capabilities.py imports it and tax_law_fetcher.py imports
# capabilities.py, so a syntax error in rollover.py is a COLLECTION error in two
# modules. An import edge is a dependency whether or not an assertion mentions
# it. tests/skill_text.py is watched for the mirror-image reason: portability
# and capabilities both reach skill prose through it, so its glob decides
# whether reference files are seen at all.
#
# Each module also watches its own file, so editing an assertion runs it.
#
# THIS MAPPING GOES STALE, and it is worth saying so plainly rather than
# discovering it. It is a hand-maintained transcription of what four Python
# modules happen to read today, and nothing recomputes it: the day
# tests/test_claude_hooks.py starts asserting against AGENTS.md, an AGENTS.md
# edit will keep selecting tests/test_portability.py alone and the miss will be
# silent. What keeps it honest is a test that re-derives the read set from the
# modules and compares it against the arms.
#
# WATCHED_ROOTS is where the Bash branch LOOKS; `_ARMS`, in the port this
# script runs, is what a path MEANS. Both are needed and they live in separate
# files, so a path added to one and not the other is half-covered: missing from
# the roots it is invisible to a Bash call, missing from the arms it selects
# nothing when edited directly.
#
# These are `find` ROOTS, so a directory here already covers everything beneath
# it: `agents` covers `agents/lib` and `agents/templates/*.json`, `scripts`
# covers the PII scan. Five such entries had accumulated, each listing a path
# an ancestor already reached — `find` then walked and printed them twice, and
# more to the point they read as the thing a new watched file needs, when what
# it actually needs is its arm in the port's `_ARMS`. A file belongs
# here only when NO listed directory contains it (`.claude/settings.json`,
# `docs/QUICKSTART.md`). `tests` became a root when
# `TestNoTestSpawnsABareInterpreter` began sweeping the whole directory: the
# nine modules that were listed one by one are all under it, and listing them
# beside it is the double-walk this paragraph is about.
# ---------------------------------------------------------------------------
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

RUN_PORTABILITY=0
RUN_CAPABILITIES=0
RUN_AGENTS=0
RUN_HOOKS=0
RUN_HOOKSDEPS=0
RUN_PREFLIGHT=0
RUN_SECUREFS=0
RUN_HOSTGUARD=0

# The assertions in tests/test_claude_hooks.py that an edit to one of the OTHER
# six TEST modules can turn red — the ones that read those modules' source, or
# name one of them. A check in its own right, wired through every one of the
# seven name lists beside the seven module flags, because a narrowing that is not
# a check cannot be carried forward when it goes red.
#
# It replaces `RUN_HOOKS=1` on the six test-module arms, and the paragraph that
# argued for the whole module — "a partial run reported as a module's verdict is
# how a check comes to mean less than it says" — was right about the wrong
# thing. That sentence is about reporting a SUBSET as the hooks module's verdict;
# this is a subset reported as its own. The WIN is the module it replaces, which
# dominates every total in MEASURED COSTS. The subset is not free, and the
# sentence that stood here implied it was by quoting the preflight-to-hooks
# ratio — a ratio about the module the narrowing SPARES, which says nothing
# about what replaced it. THE TRIGGER WITH NO ROW, in MEASURED COSTS, is where
# the subset's own cost is now stated.
#
# WHAT IS DELIBERATELY ABSENT is `test_the_measured_costs_counts_match_live_
# collection`, and leaving it out is the same judgement the `--deselect` pair in
# `run_focused_modules` records. It goes red the instant a test is ADDED to one
# of the seven modules, and stays red until the MEASURED COSTS tables, its two
# derived rows, the ARITHMETIC sentence and the reconciliation sentences have
# all been refreshed — a hand edit in several places that can only be made
# coherently once a round's tests are final. Selected here it blocked every
# tool call in between, on correct in-progress work, and the red carry-forward
# re-blocked on each one until the refresh landed: the gates-that-cry-wolf
# pattern, arriving through a check that is factually RIGHT and firing at a
# moment nothing can be done about it. It still runs in CI, in the review, and
# on every edit that takes `RUN_HOOKS` — which is every edit to the file the
# block itself lives in.
#
# PINNED by `test_the_hooks_dependents_are_real_and_complete` as a SET and in
# both directions, the way the deselect pair beside it is pinned: every id here
# resolves to a test that exists, and the membership is exact, so neither a
# rename nor a quiet addition nor a quiet removal can move it without that test
# being edited too. A renamed id would otherwise select nothing at all and read
# exactly like an arm that never fired.
HOOKS_DEPENDENTS=(
    "tests/test_claude_hooks.py::TestSkillInvariantsHook::test_the_watched_set_still_covers_what_the_modules_read"
    "tests/test_claude_hooks.py::TestSkillInvariantsHook::test_every_watched_source_path_reaches_its_module"
    # ...and the deselect pin, which was missing while this list claimed to be
    # "the whole of what an edit to one of those modules can turn red". It
    # ast-parses tests/test_portability.py to prove both `--deselect` ids still
    # resolve, and pytest ignores a `--deselect` that matches nothing — so
    # renaming either of those two tests puts the registry pair silently back
    # inside the gate for every skill edit, with tests/test_claude_hooks.py the
    # only thing red and CI the only place it shows.
    "tests/test_claude_hooks.py::TestHookRegistration::test_only_the_registry_pair_is_deselected_and_both_ids_resolve"
    # ...and the third direction, added with it: a watched module that GROWS an
    # import of a shared test helper must be selected by that helper's arm. The
    # edit that invalidates it is an edit to one of the five — the import lands
    # in a test module, not in the helper — so without this row the assertion
    # written to end a CI-only failure would itself have had CI as its only
    # signal. It costs one file read and seven `ast.parse` calls.
    "tests/test_claude_hooks.py::TestSkillInvariantsHook::test_every_watched_module_that_reads_a_shared_helper_is_in_its_arm"
)

# Turns on the modules a set of changed paths can break. Reads them
# NUL-delimited on stdin and hands the whole set to the port in ONE process:
# `find -print0` is already what the scan produces, a repository path may
# contain a newline, and an interpreter start paid once per pass is a cost the
# same start paid once per path would not survive.
#
# W3 slice 2. What stood here was 218 lines of `case`, ported to Python in
# slice 1 and held to this script by a differential over every tracked path
# until this commit spent it. `.claude/hooks/lib/hook_selection.py` is the
# decision now.
#
# THE ARMS MOVED AND THEIR REASONING DID NOT, which an earlier draft of this
# sentence claimed the opposite of. Counted: the deleted function carried 456
# comment lines and `_ARMS` carries 19. The arms became DATA, and data does not
# hold a paragraph about why an arm selects what it selects — that is the trade
# this slice made, and it is a real cost rather than a formatting change.
# Everything above says why the arms are data; this says what the arms stopped
# being able to say. The deleted reasoning is in the history of this file at
# the commit before the flip, which is where a reader asking "why does this arm
# exist" now has to go.
#
# FAIL CLOSED, which is why this is a function with a status rather than an
# inline pipe. Every way of not knowing returns 1 — the selector is missing,
# the interpreter is gone, the port raised, it answered without its protocol
# line, it named a flag this script does not declare — and every caller
# answers a 1 by selecting EVERY module. A selector that cannot answer must
# never be able to answer "nothing", which is the same trade `scan_changed_paths`
# below already makes for `find`.
#
# THE PROTOCOL LINE IS THE HALF THE EXIT STATUS CANNOT REACH. An EMPTY file is
# valid Python: it runs clean, prints nothing, and without this check the loop
# below would run zero times and the pass would go quiet over a path that can
# turn five modules red. The hook fires on every Write, this selector included,
# so a truncated save while someone edits it is the reachable case rather than
# the exotic one. `SELECTION1` is the port's own `PROTOCOL` constant and a test
# reads both, so the two cannot drift.
select_modules_from() {
    local module name first=1 answer

    # NO PATHS ON ARGV — they go in on stdin, and that is a PRIVACY property
    # rather than a style one. This script runs with CWD inside a directory
    # that may hold `documents/`, `summaries/` and a taxpayer profile, and
    # `$relative` can name one of them; argv is world-readable through `ps`,
    # so `python "$SELECTOR" "$relative"` would publish a taxpayer's document
    # name to every user on the machine. The same rule is why this hook's own
    # messages use a generic label. `test_the_selector_is_launched_with_no_paths_on_argv`
    # pins it, because nothing else about the code says so.
    #
    # `read` in a loop, not `for module in $out`. Unquoted word splitting also
    # does PATHNAME EXPANSION, so a `*` on the selector's stdout would make
    # bash list the workspace root and bind a taxpayer's filename to `$module`.
    # It fails closed today — the charset check below refuses any such name —
    # but that is the validator carrying a hazard the loop should not create.
    #
    # CAPTURED, then walked from a HERE-STRING. A here-string is a redirection,
    # so the loop stays in THIS shell and the flags it sets survive it; piping
    # the interpreter straight into `while` would put the loop in a subshell,
    # which is the same defect the call sites avoid with a process
    # substitution. Named `answer` rather than `out`: the global `out` two
    # hundred lines down holds pytest's output and is the one thing in this
    # script that reaches the agent, and two variables of one name in one file
    # is how a dropped `local` becomes a disclosure.
    answer=$(.venv/bin/python "$SELECTOR" 2>/dev/null) || return 1
    while IFS= read -r module; do
        if [ "$first" = 1 ]; then
            first=0
            # THE PROTOCOL LINE IS THE HALF THE EXIT STATUS CANNOT REACH. An
            # EMPTY file is valid Python: it runs clean, prints nothing, and
            # without this the loop would end here and the pass would go quiet
            # over a path that can turn five modules red. This hook fires on
            # every Write, the selector included, so a truncated save while
            # someone edits it is the reachable case rather than the exotic
            # one. `SELECTION1` is the port's own `PROTOCOL` and a test reads
            # both, so the two cannot drift.
            [ "$module" = SELECTION1 ] || return 1
            continue
        fi
        # The same charset the declarations use, digits included. Narrower here
        # than there, a module named `RUN_FORM8949` would satisfy every roster
        # test and be refused at this line — fail-closed, but "closed" means
        # every hook fire runs the whole suite, silently and for good.
        case "$module" in *[!A-Z0-9_]*) return 1 ;; esac
        name=RUN_$module
        # Checked against the DECLARATIONS, so this is not one more copy of
        # the roster — and it is only that check because every inherited `RUN_`
        # variable was unset at the top of this file. Without that, this line
        # asks "does a variable of this name EXIST", which an exported
        # `RUN_NEWMODULE=0` answers for a module nothing here declares. A
        # suffix the script never declared means the port and this file
        # disagree about which modules exist, and `set -u` catches that
        # everywhere else in this script; here it has to be asked for.
        [ "${!name+set}" = set ] || return 1
        # `printf -v`, not `eval`: it assigns to a name without re-entering the
        # parser at all, so the charset check above stops being the only thing
        # between the selector's stdout and arbitrary execution.
        #
        # `builtin`, and the OUTCOME checked rather than the status. An
        # exported shell function named `printf` — inherited from a profile,
        # which this hook has no control over — shadows the builtin, and one
        # forwarding to `/usr/bin/printf` takes every other `printf` in this
        # script and rejects `-v`: reproduced on bash 3.2, assignment refused,
        # flag left at 0, and this function returning 0 over it. That is the
        # silent-green shape exactly. `builtin` removes the shadow and the
        # comparison removes the trust: the flag is read back, so any way of
        # the assignment not happening ends in the fail-closed branch.
        builtin printf -v "$name" 1
        [ "${!name}" = 1 ] || return 1
    done <<< "$answer"
    # An EMPTY answer reaches the loop as one empty line, which is not the
    # protocol line, so the branch above has already refused it. This covers
    # the other shape: an answer that never entered the loop at all.
    [ "$first" = 0 ] || return 1
    # Explicit, never the fall-through: the last command in this function is a
    # test, so without it the status would be that test's. `remember_red_checks`
    # carries the same line for the same reason, and it got it the hard way.
    return 0
}

select_every_module() {
    RUN_PORTABILITY=1
    RUN_CAPABILITIES=1
    RUN_AGENTS=1
    RUN_HOOKS=1
    RUN_HOOKSDEPS=1
    RUN_PREFLIGHT=1
    RUN_SECUREFS=1
    RUN_HOSTGUARD=1
}

nothing_selected() {
    [ "$RUN_PORTABILITY" -eq 0 ] && [ "$RUN_CAPABILITIES" -eq 0 ] \
        && [ "$RUN_AGENTS" -eq 0 ] && [ "$RUN_HOOKS" -eq 0 ] \
        && [ "$RUN_HOOKSDEPS" -eq 0 ] \
        && [ "$RUN_PREFLIGHT" -eq 0 ] && [ "$RUN_SECUREFS" -eq 0 ] \
        && [ "$RUN_HOSTGUARD" -eq 0 ]
}

# Turns the stamp's red carry-forward back into selection flags. A check named
# there is one an earlier pass FAILED, and it stays selected on every pass that
# runs at all until it comes back green. Where this is called from matters more
# than what it does — see the call site.
#
# The unrecognized token is DROPPED, silently and on purpose, and that is what
# answers "what if a red module no longer exists?". The stamp records which CHECK
# was red, never where that check's file lives, so the module path exists in
# exactly one place in this script (run_focused_modules) and a stale stamp cannot
# resurrect a renamed or deleted one. Storing the path instead would put a name
# this hook wrote days ago straight into pytest's argv, where a since-renamed
# module answers with exit 4 — a usage error, a no-verdict pass, and now that a
# no-verdict pass no longer advances the stamp, a wedge that never clears itself.
apply_red_checks() {
    local check

    [ -n "$KNOWN_RED" ] || return 0
    while IFS= read -r check; do
        case "$check" in
            portability) RUN_PORTABILITY=1 ;;
            capabilities) RUN_CAPABILITIES=1 ;;
            agents) RUN_AGENTS=1 ;;
            hooks) RUN_HOOKS=1 ;;
            hooksdeps) RUN_HOOKSDEPS=1 ;;
            preflight) RUN_PREFLIGHT=1 ;;
            securefs) RUN_SECUREFS=1 ;;
            hostguard) RUN_HOSTGUARD=1 ;;
        esac
    done <<< "$KNOWN_RED"
    return 0
}

# The mirror image, run after the verdict: which checks does this pass hand to
# the next one as still-red. Both directions read the same RUN_* flags,
# which are frozen from apply_red_checks until the process exits — so "what ran"
# and "what is recorded as red" cannot disagree without an edit that moves one of
# them across that line.
remember_red_checks() {
    NEW_RED=''
    [ "$RUN_PORTABILITY" -eq 1 ] && NEW_RED="${NEW_RED}portability"$'\n'
    [ "$RUN_CAPABILITIES" -eq 1 ] && NEW_RED="${NEW_RED}capabilities"$'\n'
    [ "$RUN_AGENTS" -eq 1 ] && NEW_RED="${NEW_RED}agents"$'\n'
    [ "$RUN_HOOKS" -eq 1 ] && NEW_RED="${NEW_RED}hooks"$'\n'
    [ "$RUN_HOOKSDEPS" -eq 1 ] && NEW_RED="${NEW_RED}hooksdeps"$'\n'
    [ "$RUN_PREFLIGHT" -eq 1 ] && NEW_RED="${NEW_RED}preflight"$'\n'
    [ "$RUN_SECUREFS" -eq 1 ] && NEW_RED="${NEW_RED}securefs"$'\n'
    [ "$RUN_HOSTGUARD" -eq 1 ] && NEW_RED="${NEW_RED}hostguard"$'\n'
    # `return 0` explicitly, never the fall-through. With the LAST flag in the
    # chain above unset — whichever it is; naming the module here went stale the
    # same day the next check was appended, twice — the function ENDS on a false
    # condition, so its status would report a failure to a caller that never
    # asked about that module at all.
    return 0
}

# A Write or Edit names its exact file, so the payload is authoritative about
# what THIS call did and the filter can short-circuit before any other work.
# That ordering is the point: without it every unrelated Python edit in the
# repository would pay for a pytest run, and a hook that slow on unrelated work
# gets deleted. A Bash payload is a command string and can rewrite anything, so
# it gets no textual filter at all — the stamp scan below is its filter.
#
# A contributor with no virtualenv gets silence rather than a session that fails
# on every edit. AGENTS.md §7 makes the venv the documented way to run the suite,
# so its absence means the checks are unavailable, not that they failed. This
# check precedes every stamp write below, so an environment that never ran the
# checks never advances the boundary that says they were run.
#
# It moved ABOVE the textual pre-filter when that filter became a Python call,
# and the move changes no observable behaviour: with no interpreter this script
# exited 0 in silence whether the filter selected something or nothing, so both
# orders answer a venv-less checkout identically. What it buys is that the
# filter may now assume the interpreter it is about to run.
[ -x .venv/bin/python ] || exit 0

# Use a generic label in every message. A runtime path such as
# documents/skills/<taxpayer>.md must never reach hook output, and neither must
# the Bash command, which can carry identity data in an argument.
if [ "$tool_name" = Bash ]; then
    display_target='a Bash tool call'
else
    case "$file" in
        "$PWD"/*) relative=${file#"$PWD"/} ;;
        # A path in another clone, or in ~/.claude/skills, would otherwise run
        # THIS repository's suite and report failures against a file those
        # assertions say nothing about.
        *) relative='' ;;
    esac
    # An empty `relative` is a file outside the project, which the `case`
    # above has already decided selects nothing. Saying so here rather than
    # asking the port costs one comparison and saves an interpreter start on
    # a branch that runs for every Write and Edit in the session.
    if [ -n "$relative" ]; then
        # A process substitution rather than a pipe: `x | select_modules_from`
        # runs the function in a SUBSHELL, where every flag it sets dies with
        # it and this branch reads back the eight zeros it started with — a
        # silent green over an edit that had just selected five modules.
        # Measured: rewritten as a pipe, thirty tests in this module go red.
        select_modules_from < <(printf '%s\0' "$relative") || select_every_module
    fi
    nothing_selected && exit 0
    case "$relative" in
        .agents/skills/*|.claude/skills/*|.claude/agents/*)
            display_target='a repository skill or agent definition' ;;
        *)
            display_target='a watched repository source file' ;;
    esac
fi

# ---------------------------------------------------------------------------
# The change boundary. Mirrors harden-runtime-perms.sh, including the two
# properties that are easy to leave out and expensive to leave out:
#
#   - The boundary is a FILE created by this pass, not the local clock rendered
#     as text. If the clock moved backwards, every new file looks older than the
#     old stamp and would be skipped forever. Backdate it by a second while it
#     still represents the pass START: a strict `-newer` predicate otherwise
#     misses a write that shares a coarse filesystem timestamp tick with it.
#
#   - The new stamp carries the pass START time, not its end. That matters far
#     more here than in the permissions hook, because the pass is a whole
#     pytest run rather than a directory walk: an end-time stamp would make
#     every file written DURING the run look older than the stamp, so the
#     change that landed while the checks were running would never be checked
#     at all.
#
# A symlinked stamp or a symlinked .claude is refused rather than followed — the
# hook must not write through a link out of the workspace — and a refusal forces
# the full run, which is correct and merely slower.
# ---------------------------------------------------------------------------
STAMP=".claude/.skill-invariants-stamp"
# Line 1 is the format version; every line after it names a CHECK that came back
# red in the pass which wrote the stamp (see "what the pass is allowed to
# remember"). The names are NOT listed here: this sentence enumerated four of
# them and stayed at four when the fifth and sixth arrived, and
# `test_the_check_name_lists_agree` parses the seven CODE lists as sets and
# cannot see a comment. A reader debugging a stamp then reads `preflight` or
# `securefs` as corruption and "repairs" it by deleting the carry-forward the
# red-remembering design depends on. `read_red_checks` below holds the real
# list, in one place. Version 1 carried the
# version line alone, so a v1 stamp read by this code would parse as "nothing is
# red": a well-formed, plausible answer that is wrong for precisely the check the
# previous pass failed on. Bumping the version makes every older stamp UNTRUSTED
# instead, which forces a full pass — slower, and the only direction that cannot
# hide a failing invariant.
STAMP_VERSION='2'
FORCE_FULL=0
STAMP_WRITABLE=1
# The checks the stamp carries forward as red, newline-terminated, one per line.
# Empty when the stamp is absent, untrusted or clean — all harmless, because an
# untrusted stamp already forces every module to run.
KNOWN_RED=''

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/skill-invariants.XXXXXX" 2>/dev/null) \
    || TEMP_DIR=''
if [ -n "$TEMP_DIR" ]; then
    CHANGED_LIST="$TEMP_DIR/changed"
    NOW_MARKER="$TEMP_DIR/now"
else
    CHANGED_LIST=''
    NOW_MARKER=''
fi

# The stamp candidate is created by mktemp INSIDE .claude/, because it has to
# land on the same filesystem as the file it atomically replaces. That makes it
# the one scratch file $TEMP_DIR's removal cannot cover, so track it separately
# and let the trap remove it if this pass never reaches its `mv`. The permissions
# hook carries the same pair for the same class of file; this script had only
# half of it, which is the half that leaves litter exactly when the pass is
# killed.
INREPO_TEMP_FILES=()
track_inrepo_temp() {
    INREPO_TEMP_FILES[${#INREPO_TEMP_FILES[@]}]=$1
}

cleanup() {
    [ -n "$TEMP_DIR" ] && rm -rf "$TEMP_DIR" 2>/dev/null || true
    # Only ever the candidate of a pass that died between mktemp and mv:
    # advance_stamp UNTRACKS the name the instant `mv` consumes it, so an
    # installed stamp is unreachable from here. Leaving it tracked and relying on
    # "mv renamed the file, so this rm finds nothing" would be relying on an
    # absence — one refactor that copies instead of renaming, or one mktemp name
    # reused after a wrap, and this trap deletes the stamp the pass just wrote.
    if [ "${#INREPO_TEMP_FILES[@]}" -gt 0 ]; then
        rm -f "${INREPO_TEMP_FILES[@]}" 2>/dev/null || true
    fi
}
trap cleanup EXIT
# An EXIT trap alone does not cover the case that motivates tracking the in-repo
# candidate at all: bash runs EXIT for a signal only when that signal is itself
# trapped, and the registered hook timeout TERMINATES this process rather than
# letting it return. That is the routine case here, not the exotic one, because
# the pass being interrupted is a whole pytest run, the one thing in this hook
# long enough for a timeout to land inside. Exit 1 — not 0, which would claim a
# clean check, and not 2, which would block a tool call over a pass that
# produced no verdict at all. SIGKILL is uncoverable by any trap, which is why
# .gitignore matching `.skill-invariants-stamp*` is the other half of the fix
# rather than a belt-and-braces duplicate of this one.
trap 'cleanup; exit 1' TERM INT HUP

# Reads the red carry-forward out of a stamp already established as trustworthy:
# right version, not in the future, not a symlink. Copying the file's lines
# through unfiltered would be the shorter form and it is wrong in the direction
# that does not announce itself — apply_red_checks would ignore the junk, so a
# stamp that had been mangled by a crashed write would read as "nothing is red"
# and look exactly like a clean pass. Recognizing the known tokens here means the
# only thing an unreadable stamp can cost is a re-run.
#
# THIS LIST IS THE THIRD COPY of the check names — `apply_red_checks` and
# `remember_red_checks` hold the other two — and it is the one that fails
# SILENTLY when they drift. Adding `preflight` to those two and not to this one
# left the hook writing `preflight` into the stamp and then declining to read it
# back: the check was recorded red, the next pass dropped the token as junk, and
# a still-red module reported clean. Add a name to all three or to none.
read_red_checks() {
    local line
    while IFS= read -r line; do
        case "$line" in
            portability|capabilities|agents|hooks|preflight|securefs|hooksdeps|hostguard)
                KNOWN_RED="${KNOWN_RED}${line}"$'\n' ;;
        esac
    done <<< "$(sed -n '2,$p' "$STAMP" 2>/dev/null)"
    return 0
}

backdate_marker() {
    touch -A -000001 "$1" 2>/dev/null \
        || touch -d '1 second ago' "$1" 2>/dev/null
}

if [ -L .claude ] || [ ! -d .claude ]; then
    # Never CREATE .claude/: in a taxpayer workspace that is infrastructure this
    # hook has no business inventing. No stamp means every pass is a full pass,
    # which is the safe direction.
    STAMP_WRITABLE=0
    FORCE_FULL=1
elif [ -L "$STAMP" ]; then
    STAMP_WRITABLE=0
    FORCE_FULL=1
elif [ -z "$NOW_MARKER" ] || ! : > "$NOW_MARKER" 2>/dev/null \
     || ! backdate_marker "$NOW_MARKER"; then
    STAMP_WRITABLE=0
    FORCE_FULL=1
elif [ ! -f "$STAMP" ]; then
    FORCE_FULL=1
elif [ "$(sed -n '1p' "$STAMP" 2>/dev/null)" != "$STAMP_VERSION" ]; then
    FORCE_FULL=1
elif [ "$STAMP" -nt "$NOW_MARKER" ]; then
    FORCE_FULL=1
else
    # Reached only when the stamp is trusted, which is also the only case where
    # the red set matters: every branch above forces all seven modules anyway.
    read_red_checks
fi

# `-newercm` compares each entry's CTIME against the stamp's mtime, so it catches
# a mode change and a metadata-only rewrite as well as a write, and unlike mtime
# it cannot be back-set by a tool that preserves timestamps.
#
# KNOWN GAP, stated rather than implied: a watched path that is DELETED outright
# is invisible here, because a vanished root is dropped from the scan instead of
# reading as a change. Deleting AGENTS.md is caught at CI, not locally. Closing
# it would mean scanning the repository root, whose mtime also moves every time
# result.json or a generated PDF is written. A root that reads as changed picks
# every module, so the trade is a rare miss against a frequent false COLD pass —
# not the cheap end of MEASURED COSTS but the expensive one, which is the worse
# side of the trade for a gate.
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
    # Guard the expansion rather than trusting the list to be non-empty: under
    # `set -u`, bash 3.2 — still /bin/bash on macOS — errors on "${roots[@]}"
    # for an empty array, and the error would land on the fallback path that is
    # supposed to be the safe one.
    [ "${#roots[@]}" -gt 0 ] || return 1
    find "${roots[@]}" -newercm "$STAMP" -print0 > "$CHANGED_LIST" 2>/dev/null
}

if [ "$FORCE_FULL" -eq 1 ]; then
    select_every_module
elif ! scan_changed_paths; then
    # find could not run the comparison, so what changed is unknown. Run
    # everything: an unnecessary cold pass is only a wait, and a skipped one
    # costs the signal the hook exists to give.
    select_every_module
else
    # `-s` FIRST, and it is the fast path rather than an optimisation. `find`
    # exits 0 having printed nothing when no watched path is newer than the
    # stamp, which is the commonest outcome in a session — every `ls`, every
    # `git status`. An empty list selects nothing whoever is asked, so asking
    # spawns an interpreter to be told so. The row this protects is the first
    # one in MEASURED COSTS.
    if [ -s "$CHANGED_LIST" ]; then
        select_modules_from < "$CHANGED_LIST" || select_every_module
    fi
fi

# Only reachable from the Bash branch — the Write/Edit branch already exited on
# an empty selection. This is the fast path that makes `ls` and `git status`
# cost the fast-path row of MEASURED COSTS instead of a pytest run. Nothing
# ran, so the stamp stays where it is; it already covers everything up to the
# last completed pass.
nothing_selected && exit 0

# ONLY NOW are the checks a previous pass left red folded in, and the ordering is
# the whole design rather than a detail. Applied above this line, a red check
# would be re-run on every `ls`, every `git status` and every pytest invocation —
# a gate that fires on every tool call, which is what teaches an agent to read
# past its output and is the defect the change boundary exists to remove. Applied
# below it, the union costs nothing until something the hook watches actually
# changes, and then it costs one extra module on that pass.
apply_red_checks

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
#   skill .md edit                1088 tests     about a seventh
#   cold pass, all seven modules  1369 tests     one
#   (for scale) `pytest tests/`   every module  about twice
#
# The agent-definition row is the agents row below — that trigger selects that
# module alone — so the two state one count, and a test holds them to it.
#
# COUNTS ARE WHAT RUNS, not what collects. Two places bite: `run_focused_modules`
# passes two `--deselect` flags, so portability collects 252 and runs 250; and
# the hooks module holds two platform skips, so it collects 189 and runs 187.
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
#   tests/test_claude_hooks.py     187 tests    the dominant row
#   tests/test_portability.py      250 tests    about a tenth of the hooks row
#   tests/test_preflight.py        383 tests    a twelfth to a twentieth of it
#   tests/test_secure_fs.py        188 tests    under a fiftieth of it
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
# ARITHMETIC, which is the half that is EXACT: 48 + 219 + 188 + 250 + 383 = 1088
# for a skill edit, plus 94 + 187 = 1369 for a cold pass, both matching the
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
# They are passed unconditionally: pytest ignores a `--deselect` whose module is
# not in the selection, so this stays one literal list rather than a second
# conditional that could disagree with the first.
#
# This function is the ONE place a module's path is written. The red
# carry-forward deliberately records a check's name rather than its file, so
# renaming a module here cannot leave a stale path behind in a stamp on disk to
# be handed back to pytest days later.
run_focused_modules() {
    local -a modules=()

    [ "$RUN_PORTABILITY" -eq 1 ] \
        && modules[${#modules[@]}]=tests/test_portability.py
    [ "$RUN_CAPABILITIES" -eq 1 ] \
        && modules[${#modules[@]}]=tests/test_capabilities.py
    [ "$RUN_AGENTS" -eq 1 ] \
        && modules[${#modules[@]}]=tests/test_claude_agents.py
    [ "$RUN_HOOKS" -eq 1 ] \
        && modules[${#modules[@]}]=tests/test_claude_hooks.py
    # ...or the assertions in it that a test-module edit can reach, when
    # nothing asked for the whole thing. The RUN_HOOKS guard comes first so a
    # pass that wants both does not hand pytest the same node ids twice.
    [ "$RUN_HOOKS" -eq 0 ] && [ "$RUN_HOOKSDEPS" -eq 1 ] \
        && modules+=("${HOOKS_DEPENDENTS[@]}")
    [ "$RUN_PREFLIGHT" -eq 1 ] \
        && modules[${#modules[@]}]=tests/test_preflight.py
    [ "$RUN_SECUREFS" -eq 1 ] \
        && modules[${#modules[@]}]=tests/test_secure_fs.py
    [ "$RUN_HOSTGUARD" -eq 1 ] \
        && modules[${#modules[@]}]=tests/test_host_guard.py
    [ "${#modules[@]}" -gt 0 ] || return 1

    .venv/bin/python -m pytest "${modules[@]}" \
        --deselect tests/test_portability.py::TestSkillLayout::test_all_expected_skills_present \
        --deselect tests/test_portability.py::TestClaudeSymlinks::test_one_symlink_per_skill \
        -q 2>&1
}

# Capture first, then branch on the command's OWN status. The tempting form,
# `pytest ... | tail -20 || exit 2`, is a pipeline, and a pipeline's status is
# its LAST command's unless `pipefail` is set — `tail` all but always succeeds,
# so without pipefail the failure branch never runs and the hook exits 0 on a red
# suite (measured, not assumed). `pipefail` is set above and would rescue it,
# which is exactly what makes the form dangerous: it would be correct only by a
# side effect of a line elsewhere. Capturing does not depend on that line.
PASS_STARTED=$SECONDS
out=$(run_focused_modules)
status=$?
PASS_SECONDS=$((SECONDS - PASS_STARTED))

# AA9: an overrun of the registered timeout CANCELS this hook, and a cancelled
# check reads to the agent exactly like a pass. The headroom test guards the half
# of that the COUNT drives; a per-test cost that climbs while the count stands
# still is MEASURED COSTS' one blind spot. So the pass times itself and says so,
# without blocking, when it used THREE QUARTERS of the timeout settings.json
# registers for it. That is a comparison against a threshold, not a recorded
# reading (ruling Q13): the seconds printed are this PYTEST pass's own — the
# selector and the scan around it are small beside it and not counted — and the timeout
# is read from the file the harness enforces, never restated here. A timeout it
# cannot read is no warning — the check is advisory, and silence there is the
# state every pass was in before it existed.
budget_warning() {
    local timeout
    # `contains`, not a regex `test`: that needs a jq built with Oniguruma, and
    # without it the warning would vanish. The TIGHTEST registered timeout,
    # numbers only, so one registration without a timeout cannot hide another.
    timeout=$(jq -r '[.hooks.PostToolUse[]?.hooks[]?
                       | select((.command // "") | tostring
                                | contains("skill-invariants.sh"))
                       | .timeout | numbers] | min // empty' \
              .claude/settings.json 2>/dev/null) || return 0
    case "$timeout" in ''|*[!0-9]*) return 0 ;; esac
    [ "$timeout" -gt 0 ] || return 0
    [ $((PASS_SECONDS * 4)) -ge $((timeout * 3)) ] || return 0
    printf '%s\n' "Timing: the skill invariants' pytest pass took ${PASS_SECONDS}s of" \
        "the ${timeout}s timeout settings.json registers for this hook — three" \
        "quarters or more. Past the timeout the hook is CANCELLED and a" \
        "cancelled check reads as a pass. Re-time a cold pass and see the" \
        "MEASURED COSTS block in .claude/hooks/skill-invariants.sh (AA9)."
}

# ---------------------------------------------------------------------------
# What the pass is allowed to remember. Three rules, and each one is the repair
# of a distinct way this hook has gone quiet:
#
#   1. A pass that produced NO VERDICT does not touch the stamp. pytest exits 1
#      for "tests ran and some failed" but 2-5 for interrupted, collection
#      error, internal error, usage error and nothing-collected — cases where
#      NOTHING was measured. Advancing the boundary there is the worst of both
#      worlds: the hook says "the checks themselves are broken", the agent
#      repairs them, and the edit that was sitting on disk throughout the outage
#      is now older than the boundary and is never checked again. This is the
#      harden-runtime-perms.sh property, held for the same reason its write_stamp
#      states — a failed pass must leave the OLD stamp in place, or the file
#      whose chmod failed looks old to every later incremental pass. It differs
#      only in what "failed" means: there, work that did not happen; here, a
#      measurement that was never taken.
#
#      What this costs is the repeat rule 2 exists to prevent, so it is worth
#      saying what is repeated: while the checks are unrunnable, every
#      following tool call retries them. That retry is not the cold pass —
#      pytest gives up before running a test body, as three runs each of a
#      renamed module (exit 4) and of an import that fails at collection
#      (exit 2) showed, so it pays start-up and collection and nothing else.
#      That, until the checks are repaired, against an edit that is otherwise
#      never checked again, is not a close trade.
#
#   2. A pass that produced a VERDICT advances the stamp, red verdict included.
#      Holding the boundary back until the suite goes green is the tempting
#      alternative and it recreates the defect this hook was rewritten to
#      remove: while the agent is midway through a repair, every following tool
#      call re-runs the suite and re-reports the same failure, landing on the
#      very `git diff` and `pytest` calls needed to diagnose it. A gate that
#      cries wolf on every call is a gate that gets reasoned past.
#
#   3. ...but a red verdict is REMEMBERED in the stamp and unioned into the next
#      pass that runs. Rule 2 alone means the boundary moves past a failure, so
#      an agent-definition edit a minute later selects tests/test_claude_agents.py
#      alone, passes, and exits 0 in silence while the portability invariant is
#      still red on disk. That is coverage the old run-everything-every-time
#      behaviour had and the change boundary took away; carrying the red set
#      forward gives it back without giving back the cry-wolf half, because the
#      union is applied only after the "nothing changed" fast path has already
#      exited.
#
# The attribution is deliberately blunt: one pytest invocation returns one
# status, so a red verdict marks EVERY check in this pass, not the one that
# actually failed. The exact alternative is to read the failing module names out
# of pytest's output, and it is worse where it counts — it makes the gate's
# memory depend on a report format that `-q` abbreviates and that no assertion
# here pins, and a parse that silently stops matching yields an EMPTY red set,
# which is this defect back again with no symptom. Over-attribution fails the
# other way: a check that was already green is re-run on the next watched change
# until the red one clears, which is wasted work and can hide nothing.
# ---------------------------------------------------------------------------
NEW_RED=''

# The red lines follow the version line, so an older reader that only ever looked
# at line 1 sees a version it does not recognize and distrusts the whole file —
# which is why the version was bumped rather than the format extended quietly.
# `if`/`fi` rather than `[ -n … ] && printf`: the latter is the last command in
# the group, so an EMPTY red set would make the redirection group exit non-zero
# and advance_stamp would treat a perfectly good stamp as a failed write.
write_stamp_body() {
    {
        printf '%s\n' "$STAMP_VERSION"
        if [ -n "$NEW_RED" ]; then
            printf '%s' "$NEW_RED"
        fi
    } > "$1" 2>/dev/null
}

advance_stamp() {
    local candidate

    [ "$STAMP_WRITABLE" -eq 1 ] || return 0
    # RE-VALIDATED HERE, not merely at entry, and the distance between the two
    # checks is the whole reason. STAMP_WRITABLE was decided before the pytest
    # pass; the pass is a whole pytest run, and `.claude` can be replaced by a
    # symlink pointing outside the workspace at any point in it.
    # Both `mktemp` and `mv` FOLLOW that link, so a pass that validated a real
    # directory would create its candidate and install its stamp under the
    # link's target — a hook write outside the clone, which is the one thing the
    # entry check exists to forbid. Constructed and observed: with this line
    # removed, an external directory substituted mid-pass gains a
    # `.skill-invariants-stamp`; with it, nothing is created there at all.
    # harden-runtime-perms.sh asserts the same three conditions immediately
    # before its own write_stamp for the same reason — the difference is only
    # that its window is a few statements wide and this one is a whole pytest
    # pass, which makes the race here the reachable one rather than the
    # theoretical one.
    #
    # It narrows the window rather than closing it; closing it needs an
    # openat/O_NOFOLLOW handle that POSIX shell does not have. Refusing costs
    # exactly one thing — the next call repeats this pass — so failing closed is
    # free, and the checks were already RUN, so nothing is skipped by refusing.
    [ -d .claude ] && [ ! -L .claude ] && [ ! -L "$STAMP" ] || return 0
    candidate=$(mktemp ".claude/.skill-invariants-stamp.XXXXXX" 2>/dev/null) \
        || return 0
    track_inrepo_temp "$candidate"
    if write_stamp_body "$candidate" \
       && touch -r "$NOW_MARKER" "$candidate" 2>/dev/null \
       && mv -f "$candidate" "$STAMP" 2>/dev/null; then
        # `mv` CONSUMED the candidate name, so untrack it: the EXIT trap must not
        # be able to reach a stamp that was successfully installed. This hook
        # creates exactly one in-repo candidate per pass, which is what makes
        # clearing the whole list precise rather than approximate.
        INREPO_TEMP_FILES=()
        return 0
    fi
    rm -f "$candidate" 2>/dev/null || true
    INREPO_TEMP_FILES=()
    # Deliberately silent, and deliberately not an error. A stamp that could not
    # be written costs one thing only: the next call repeats this pass. Turning
    # that into stderr would make a PASSING check noisy, and a hook that narrates
    # successful edits is noise the user learns to ignore — after which they miss
    # the one that matters.
    return 0
}

if [ "$status" -eq 0 ] || [ "$status" -eq 1 ]; then
    # A green verdict clears the red set by writing none, and it is entitled to:
    # every check carried as red was folded into THIS pass by apply_red_checks,
    # so exit 0 is a statement about all of them and not only about the paths
    # that changed.
    [ "$status" -eq 1 ] && remember_red_checks
    advance_stamp
fi

warning=$(budget_warning)
if [ "$status" -eq 0 ]; then
    [ -z "$warning" ] && exit 0
    # Exit 1 SURFACES it without blocking — the convention harden-runtime-
    # perms.sh uses — and the verdict above already advanced the stamp.
    printf '%s\n' "$warning" >&2
    exit 1
fi

# pytest exits 1 for "tests ran and some failed" and 2-5 for "no verdict":
# interrupted or a collection error (2), an internal error (3), a usage error
# such as a renamed module (4), nothing collected (5). Say which happened.
# Reporting a usage error as "invariants FAILED" sends the agent hunting for a
# skill bug that does not exist, which is how a gate loses its credibility even
# while firing correctly.
if [ "$status" -eq 1 ]; then
    headline="Skill invariants FAILED after ${display_target}"
else
    headline="Skill invariant checks could not RUN after ${display_target}"
    headline="${headline} (pytest exit ${status}) — the checks themselves are"
    headline="${headline} broken, not necessarily the edit"
fi
{
    echo "$headline"
    printf '%s\n' "$out" | tail -30
    if [ -n "$warning" ]; then
        printf '%s\n' "$warning"
    fi
} >&2
exit 2
