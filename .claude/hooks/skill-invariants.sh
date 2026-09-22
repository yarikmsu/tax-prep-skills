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
#     for the same reason. Answering that costs a fraction of a second against a
#     full pass three orders of magnitude slower; the seconds live in MEASURED
#     COSTS below and nowhere else, this line included. The tempting
#     alternative — the previous behaviour — was to run the checks after EVERY
#     Bash call, which made `ls`, `git status` and every pytest invocation pay
#     that full run and, on a loaded machine, exceed the registered timeout.
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
#      so it says nothing and must not block the tool call
#   2  the checks failed, or could not produce a verdict; stderr is fed back to
#      the agent for a same-turn fix
# `+exP` FIRST, in the very first statement, because all three arrive from an
# exported SHELLOPTS and all three matter before another line runs. See "A GATE
# INHERITS ITS ENVIRONMENT" below for what each one does to this hook.
set +exP -uo pipefail
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
# `run_focused_modules` records. It goes red the instant a test is ADDED to any
# of the six, and stays red until the MEASURED COSTS table, its two derived
# rows, the ARITHMETIC sentence and the counts-ahead paragraph have all been
# refreshed — a hand edit in six places that can only be made coherently once a
# round's tests are final. Selected here it blocked every tool call in between,
# on correct in-progress work, and the red carry-forward re-blocked on each one
# until the refresh landed: the gates-that-cry-wolf pattern, arriving through a
# check that is factually RIGHT and firing at a moment nothing can be done about
# it. It still runs in CI, in the review, and on every edit that takes
# `RUN_HOOKS` — which is every edit to the file the block itself lives in.
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
#     more here than in the permissions hook, because the pass is a pytest run
#     lasting seconds to a minute: an end-time stamp would make every file
#     written DURING the run look older than the stamp, so the change that
#     landed while the checks were running would never be checked at all.
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
# the pass being interrupted is a pytest run lasting seconds to a minute. Exit 1
# — not 0, which would claim a clean check, and not 2, which would block a tool
# call over a pass that produced no verdict at all. SIGKILL is uncoverable by any
# trap, which is why .gitignore matching `.skill-invariants-stamp*` is the other
# half of the fix rather than a belt-and-braces duplicate of this one.
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
    # everything: an unnecessary pass costs seconds, a skipped one costs the
    # signal the hook exists to give.
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
# cost milliseconds instead of a pytest run. Nothing ran, so the stamp stays
# where it is; it already covers everything up to the last completed pass.
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
# prompt rather than busywork, and `tests/` wholesale is roughly half again the
# cold pass for nearly four times the tests — far too slow to pay on a save
# (MEASURED COSTS below owns the seconds). `test_the_hook_runs_test_files_that_exist` pins that
# each path resolves — a rename would otherwise make pytest exit 4 (usage error)
# and report "invariants FAILED" on every skill edit while `pytest tests/` was
# green.
#
# ---------------------------------------------------------------------------
# MEASURED COSTS. This block is the ONE owner of every runtime figure about this
# hook. Cite it by name — "skill-invariants.sh MEASURED COSTS" — rather than
# copying a number out of it into a doc, a comment or a test docstring: a
# quantity kept in three places goes stale in three places. That is not a
# hypothetical here. Two successive revisions of this block each named a
# `tests/` wholesale figure (75s, then 176s) that a later measurement did not
# reproduce, and both times the copies elsewhere were left behind.
#
# RE-MEASURED END TO END, every row in ONE sitting, on the branch that added the
# required-answer registry (V5). The sitting ran as several consecutive
# invocations with NOTHING touching the repository between them, the ten-minute
# cap on a tool call being what splits them; that is a continuation of one
# sitting and not the patch this paragraph forbids. FOUR earlier sets taken on
# this same branch were DISCARDED rather than used, for the reason the paragraph
# exists: the first because a test was added and twelve subprocess spawns
# rewired after it ran, the second and third because two review rounds added ten
# tests to the preflight module and three to the host guard, and the fourth
# because a THIRD review round added five more to preflight, eight to the host
# guard, and seven rows to a loop inside the hooks module — which is a round
# moving three of the seven rows and both derived ones at once. Each described a
# tree that no longer exists, and the cost of discarding one is half an hour —
# which is the argument for taking this measurement LAST, after every edit is
# final, and NOT the argument for patching a row. That patch is what the revision
# before THAT was: count columns edited and seconds carried over, going wrong in
# BOTH directions at once, every count landing short (100 where 116 run, 288
# where 289 run, 2936 where 2956 collect) while its module rows summed to about
# 136s against a cold pass it stated as 121-124s. Parts exceeding the whole was
# its signature; this sitting has the parts INSIDE the whole at both ends by
# 0.2%, and the ARITHMETIC paragraph below separates that case from the patched
# one rather than leaving a reader to guess which they are looking at. So: no
# row here is edited alone. Either the whole block is re-measured in one sitting or it is
# left as it stands, stale and legibly so.
#
# MEASURING THE FAST-PATH ROW HAS A TRAP, stated because it cost two full passes
# to find. That row needs a stamp NEWER than every watched file, and a bare
# `touch` on the stamp does not produce one: the trust ladder above backdates
# `$NOW_MARKER` by a second and treats `[ "$STAMP" -nt "$NOW_MARKER" ]` as
# untrustworthy, so a stamp dated this instant forces a FULL pass. The first
# invocation after such a touch measures the cold pass, the hook then rewrites
# the stamp with a trustworthy date, and every run after it is genuinely fast —
# which reads exactly like a warm-up effect and is not one. Backdate the stamp
# (`touch -A -000010`) and all six runs are fast. It has now cost two full passes
# a second time, to a reader who had this paragraph in front of them: clearing
# the red carry-forward means rewriting the stamp, and the obvious way to date
# the rewrite is the way that forces the pass.
#
# A THIRD reader has now paid it, backdating BETWEEN every run rather than once
# ahead of them, and got 0.08, 12.49, 0.10, 0.08, 0.08, 0.08 — one full pass
# sitting in the middle of five fast ones, which reads as noise and is the trap
# firing. The row above was re-taken with no touching at all, six runs after a
# pass had already written a trustworthy stamp, and that is the shorter recipe:
# do not backdate per run, and if a reading is two orders of magnitude off its
# neighbours it is this and not the machine.
#
# READ THE RATIOS, NOT THE SECONDS, and the conditions are why. One macOS host,
# `.venv/bin/python -m pytest`, every line taken three times, back to back, with
# nothing else running against the repository — an earlier attempt at this same
# sweep was ABANDONED mid-flight because edits were landing during it and firing
# the hook's own pytest underneath the measurement. Run-to-run spread is ±5% on
# the slow lines here, and the spread BETWEEN sittings is far larger: readings of
# `pytest tests/` on this same host have returned 139-155s, 175-209s, 179-191s,
# 184-190s, 199-204s, 248.7-250.5s, 228.4-243.4s and now 330.0-333.8s — a jump
# of 37% comparing the ceilings and 44% comparing the floors, on a suite that
# gained SIX tests, which is the largest move this row has ever recorded
# between sittings and has no cause anyone should name. The reading before it
# was the first in seven to come DOWN while the suite GREW; the pair together
# is the point about weather made by the data, and the reason nothing here is
# quoted to three significant figures. The portability module moved further —
# 7.5-10.2s one day, 12.7-13.4s the next, with no code between the two — which is why the
# ratios below are stated as "roughly" and why a figure lifted from here to three significant
# figures would be wrong by the next reading. Every line is a RANGE with a run
# count.
#
# THE DRIFT-WHILE-IT-RAN PARAGRAPH THAT STOOD HERE IS GONE, and what it was
# about is worth one sentence: this slice's own sitting found every cheap row
# reading higher at the end of the sitting than at its start, and argued from
# the modules that never invoke the hook that the slide was the HOST rather
# than the change. That argument still looks right and its figures do not
# survive — they describe a tree this branch was rebased off, and the rows they
# were stated beside were replaced wholesale. Keeping the narrative over the
# trunk's table would be the exact staleness this block exists to catch, so it
# goes, and the sitting owed on this branch is where the question gets asked
# again with figures a reader can check against the rows above.
#
# SECONDS ARE WALL CLOCK of the invocation — interpreter start and collection
# included — because that is what the tool call waits on. pytest's own "in
# 0.39s" footer is the smaller number and reading one convention as the other is
# how a row ends up looking halved. Measured on its own — `pytest --version`,
# five runs — that overhead is 0.08-0.21s on this host: a fifth of the agents
# row, and a rounding error on the hooks module. It does NOT account for the
# whole gap on every line; capabilities reports 0.42s against a 1.2s wall clock,
# and the difference is that module's own imports, which pytest's footer starts
# after.
#
# THE AGENTS ROW WAS ONCE THE READING CARRIED AS STALE, and two sittings have
# now settled it. It started `git` subprocesses — Replay A's pinned blob, the two
# calls that classify a failure to read it, and the fixture repository that
# controls those — after a much older reading was taken, and the note here said
# only that its place among the rows was unknown rather than guessing at one.
# Measured with the set it came back 1.0s flat, the sitting after that 1.0-1.8s,
# and this sitting 1.2-1.4s: five times the row it replaced, and no longer the
# smallest of the seven. Where it sits against capabilities cannot be read off
# these rows in either direction — 1.2-1.4s against 1.0-1.3s OVERLAPS, as
# 1.0-1.8s against 0.8-3.1s did before it, and the prose here asserted an
# ordering both times anyway. Three sittings all landing inside 1.0-1.8s is the
# strongest thing this block can say about any row, and it is worth more than
# any single reading. The fixed-overhead sentence
# above under-explains this row for the same reason it always did — the git
# calls are most of what it costs, not the interpreter start.
#
#   what triggers it               what runs     observed range    runs
#   ---------------------------------------------------------------------
#   nothing watched changed        no pytest    0.07 - 0.10s        12
#   agent-definition edit           48 tests      0.9 - 1.2s          6
#   skill .md edit                1043 tests     21.9 - 23.4s         3
#   cold pass, all seven modules  1302 tests    155.4 - 159.3s        3
#   (for scale) `pytest tests/`   4941 tests    316.8 - 324.4s        2
#
# COUNTS ARE WHAT RUNS, not what collects. Two places bite: `run_focused_modules`
# passes two `--deselect` flags, so portability collects 247 and runs 245; and
# the hooks module holds two platform skips, so it collects 167 and runs 165.
# THE SUITE holds seven skips, not two: those two and the five Windows-only DACL
# cases W6 added. An eighth depends on the CHECKOUT rather than the platform —
# tests/test_pdf_toolchain.py skips when `generate_tax_pdf.py` is absent, which
# it is in a fresh clone and in CI. THIS refresh was taken in a developer
# checkout that HAS that untracked artifact, so it runs and the count is seven
# where the sitting the seconds came from recorded eight. Nothing about the
# suite changed; the tree did. SO THE ROW BELOW IS TWO TREES — this one's
# counts beside the previous one's seconds — which is what the paragraph on the
# owed sitting, further down, exists to say. An earlier draft of this sentence
# called the row "the tree the seconds came from": true while both halves were
# taken together, and false the moment the sitting was discarded.
# One figure used to cover both, on the assumption that the hooks module was
# the only place a test could be skipped — an assumption nothing stated and
# the first conditional test outside that module broke.
# `pytest tests/` collects 4948 and runs 4941 for those skips — and that row
# is the PLAIN invocation, WITHOUT the two deselections, which is the other way
# to state a count the measured command did not actually run. A first attempt at
# it passed the deselections through and measured 3000. Reporting any
# of them as collected made the decomposition sum past the total — three times
# now, and on one of those the derived rows were right and the part was wrong,
# which is the harder way round to notice.
#
# The first line is this hook end to end on its fast path — the real script, fed
# a Bash payload with nothing watched newer than the stamp. The rest are the
# pytest invocation alone, which is all but the entire cost: the hook's own work
# ahead of pytest is that same fast-path cost, so add it to any line below
# rather than measuring it again.
#
# THAT ROW IS UNCHANGED BY THIS SLICE, and the paragraph that stood here said
# the opposite — "THAT ROW CAME DOWN, which is the one figure in this block W3
# slice 2 moved on purpose". It is worth keeping the correction rather than the
# claim, because the claim was true about a comparison nobody should care about.
#
# What was measured, on this host within one afternoon and on one tree, was
# 0.08-0.10s asking the port to classify an empty list against 0.05-0.06s
# refusing to ask. Both of those are DRAFTS OF THIS SLICE. The first made the
# Python call unconditionally, including when `find` printed nothing, which is
# the commonest outcome in a session; the `-s "$CHANGED_LIST"` guard removes
# that. So the guard undoes a regression this slice introduced, and crediting
# the slice with the difference credits it for repairing its own defect.
#
# The tree WITHOUT this slice settles it independently: the sitting taken on the
# branch that stopped this hook trusting its environment — no port on the fast
# path, the `case` still in place — read this row at 0.05-0.06s, the same figure.
# The fast path returns before any selection happens, by either implementation,
# so there was never a cost here for the flip to remove. The port is not on this
# path. That is the whole of what can be said about it.
#
# DECOMPOSITION — each module timed alone, portability carrying the same two
# deselections run_focused_modules passes. This is the durable half: WHICH
# module dominates, and by how much, survives a change of machine.
#
#   tests/test_claude_hooks.py     165 tests    132.1 - 135.6s       3
#   tests/test_portability.py      245 tests     12.8 - 13.5s        6
#   tests/test_capabilities.py     185 tests      0.8 - 1.2s         6
#   tests/test_claude_agents.py     48 tests      0.9 - 1.2s         6
#   tests/test_secure_fs.py        182 tests      1.5 - 1.7s         6
#   tests/test_preflight.py        383 tests      6.7 - 11.0s        6
#   tests/test_host_guard.py        94 tests      1.4 - 2.5s         6
#
# The agent-definition row above and the agents row here are the SAME
# invocation — that trigger selects that module alone — and are stated with the
# same range on purpose. Two figures for one measurement is how this block
# started drifting from itself the first time.
#
# Read the preflight line as a RATIO, which is the half that survives a change
# of machine: it sits between the agents module and portability, and is
# somewhere between a twelfth and a twentieth of the hooks module that
# dominates every total here. That same ratio read a thirteenth-to-an-eighteenth
# one sitting ago, a sixteenth-to-a-twenty-third the sitting before that, a
# sixteenth-to-a-seventeenth before THAT, and a twentieth-to-a-twenty-second
# before THAT, on a module that has gained fifteen tests across the six and
# lost none — it moves because the SPREAD moves, not because the work does,
# which is itself the reason to distrust any single reading of the seconds any
# of them comes from. The clearest case is the sitting before this one: the
# same module, measured twice with nothing touched between, gave 6.3-7.2s and
# then 5.6-6.4s. A skill edit selects it alongside portability, capabilities,
# secure_fs and the agents module — FIVE, which is what the EXACT sum below
# adds — and that is where its cost actually lands. It has grown against its neighbours four times: the
# launcher tests EXECUTE `run.sh` in a subprocess rather than asserting against
# its text — the trade that bought a test able to catch a hang — and the
# installed-versus-locked comparison added a class that reads the lock and
# fabricates distributions, then a second one for the pins that are ABSENT from
# an environment rather than merely the wrong version. Then the import-time
# WIRING probe, which copies the package initialiser into a temp tree and
# executes it, and the environment-fault cases that fail its `mkdtemp` on
# purpose. All of those classes are tmp_path and fabrication, which is why the
# row moved much less than its count.
#
# THE TRIGGER WITH NO ROW is a test-module edit, and this is where its cost
# lives: this block owns a trigger's cost whether or not the trigger has a row,
# and this one has none because what it runs is not a module. It selects the
# edited module, the host-guard module, and the four node ids in
# `HOOKS_DEPENDENTS` — TWO modules and the ids, and THREE when the edited
# module is the host guard itself, whose arm adds portability because
# `TestTheEngineDirectoryIsFlat` reads that module's roster by name. Never the
# hooks module those ids sit in — the log entry below that records `RUN_HOOKS=1` on
# those arms was narrowed fourteen rounds later, and read as current it
# describes a policy that has not been in force since.
#
# What those ids cost was stated NOWHERE, and every comment beside them
# implied it was nothing by quoting the preflight-to-hooks ratio — a ratio about
# the module the narrowing SPARES, which says nothing at all about what replaced
# it. Measured WITH this sitting, against the preflight row, back to back: the
# ids cost 16.7-19.0s over three runs, between one and a half and two and four
# fifths of that module. TWO of them drive the real hook once per watched
# path, a subprocess each, and that is where all of it goes; the other two read
# source and parse it, worth nothing measurable between them.
#
# THE COUNT IN THIS PARAGRAPH WAS STALE, and it is worth naming because the
# staleness is the shape this whole block exists to catch. `HOOKS_DEPENDENTS`
# holds FOUR ids; every sentence here said three, including the one measuring
# them, because the fourth was appended with a comment ending "added with it"
# and nothing re-read the prose. Its own list is what the seconds above were
# taken from, which is why they are right and the words around them were not.
#
# THE RATIO IS ONE AND A QUARTER TO TWO this sitting, and it FELL with nothing
# about the ids changing: the preflight row it is stated against went UP,
# 5.6-7.2s to 7.6-10.4s, which is the whole hazard of expressing one cost as a
# ratio to another that is itself weather. A sitting ago it was one and seven
# tenths to two and three tenths, 12.2-12.9s over 5.6-7.2s. The list of four
# earlier readings that stood here is GONE rather than extended: it ran "one and
# a half to one and two thirds a sitting ago, one and three quarters to two and
# a quarter the one before", and the tables it claimed to summarise give 1.69 to
# 2.30 for the sitting it called the first of those. Every entry was off by a
# position, so none of them said what it claimed about the sitting it named, and
# a history nobody can re-derive is worth less than the two readings that can
# be.
#
# THE HOST-GUARD MODULE RIDES ON EVERY ONE of them, and on the forty-odd test
# modules that have no arm at all, where it is the whole of what the trigger
# runs. `TestNoTestSpawnsABareInterpreter` sweeps `tests/*.py` whole, so every
# module in that directory is source behind it; the price is the FOURTH cheapest
# row in the decomposition above this sitting — capabilities, the agents module
# and secure_fs all came in under it, where last sitting it was one of the three
# — paid on a save of any Python file there. Nothing about that module changed;
# its ceiling moved because one run in six came back at 2.42s against five in
# the low 1.4s, which is what a RANGE is for.
#
# So a save of tests/test_preflight.py pays between two and a quarter and five of
# its own module, and a save of tests/test_host_guard.py — the fourth cheapest
# of the seven this sitting, having been one of the three last — pays TWELVE to
# twenty-five times its own. That
# second figure read eight to twelve until a review round read the arm rather
# than the sentence: the host-guard arm also sets `RUN_PORTABILITY=1`, because
# `TestTheEngineDirectoryIsFlat` reads that module's roster BY NAME, and
# portability is 14.5-16.3s. One of the three cheapest rows in the
# decomposition is attached to the second most expensive one, which is the
# whole reason this paragraph states a trigger's cost rather than a module's. Against the hooks
# module they stand in for, the four ids are between a seventh and an eighth —
# 19.0 against 132.1 at one end and 16.7 against 135.6 at the other. THREE
# sittings ago it read "a tenth to a twelfth" over rows giving a
# NINTH at the top (11.7 against 112.2), which is a figure wrong in the direction
# that flatters the narrowing, and the fourth time one in this block has been: it still buys the order of magnitude it was made for, but it is not
# the rounding error the earlier wording made it, and the next narrowing has to
# come out of those two subprocess loops rather than out of this list.
#
# Still no row in the TABLE, and now for a reason that is not staleness: the
# seconds above were taken with this sitting, but the table's second column is a
# module count and what this trigger runs is two modules plus four node ids,
# three for the one arm named above — a
# row would have to state a count that is no module's, which is how the `allowed`
# set keeping every "N tests" in this block honest would come to have an
# exception in it. The figure lives in this paragraph instead, measured on the
# same terms as every row above it.
#
# ARITHMETIC, stated because a block whose parts do not add up is a block a
# reader stops believing. Those seven sum to 156.2s at their minima and 166.7s at
# their maxima, against 155.4-159.3s measured for the seven run together — so
# this sitting the parts sit INSIDE the whole at both ends, by 0.2% at each.
# Eight sittings have now put them above by 6%, inside it, above by 3-4%, above
# by 4-5%, above by 1-2%, above by 2-3%, straddling, and now inside by a fifth of
# a percent, on a decomposition whose membership did not change: the term that
# moves is the per-module spread, which the paragraph below says is the only
# accuracy parts and whole can ever have between them. THIS IS THE CLOSEST THE
# TWO HALVES HAVE COME AND IT IS WORTH NO MORE THAN THE OTHERS — 0.2% is well
# inside the run-to-run noise named above, so read it as the halves failing to
# disagree rather than as a measurement of anything, and do not treat it as a
# floor a future sitting has to beat. A row here is a RANGE and reading its floor
# as the figure is what the width is warning against.
#
# Seven separate invocations each paying an interpreter start pushes the parts
# up and is worth well under a second of it. What separates this from the
# count-column patch the header forbids is the other half, which is EXACT:
# 48 + 185 + 182 + 245 + 383 = 1043 for a skill edit, plus 94 + 165 = 1302 for a
# cold pass, both matching the trigger table above to the test. That sentence
# itself sat stale for several rounds at "160 + ... = 703 ... 845" — a typo'd
# part and a superseded sum, green the whole time because the mechanical check
# reads the TABLE and this line is prose. It then went stale a SECOND time, at
# "322 ... 753 ... 36 ... 913" against a table already reading 343 and 37, under
# the very clause claiming it moves with the table — because "it moves with the
# table" was a promise a person keeps, not a check. It is checked now: the same
# test reads this sentence, requires its five parts to be the five module rows
# and its two sums to be the two derived rows, so the three ways this block has
# stated one quantity are one figure or a red suite. A decomposition
# error shows up there first, because counts do not drift — and it now shows up
# MECHANICALLY: `test_the_measured_costs_counts_match_live_collection` collects
# each module and re-derives both sums, so a stale count column is a red suite
# rather than a figure a reader has to distrust on their own. What neither half is
# evidence about, in either direction, is collection overhead — a previous
# revision claimed that and had the parts exceeding the whole because a count
# column had been hand-patched.
#
# EVERY SECOND above was measured together, most recently 2026-09-17, on an idle
# machine, and re-measured END TO END rather than patched where a count moved:
# V5 added ONE case to secure_fs, which moves that row, the skill-edit row and
# the cold pass at once, and patching one row over stale neighbours is what left
# the derived rows wrong four revisions ago.
#
# ONE SITTING SAID SOMETHING LOUDER THAN ANY ROW, and it was the PREVIOUS one,
# not this one — the figures in this paragraph are that sitting's rows and none
# of them appears in the table above any more. Its cheap modules were measured
# TWICE, at its start and near its end, with nothing touched between, and every
# one came back LOWER the second time, by between about 5% and 70% depending on
# the module and on which end of its range is read — capabilities most,
# portability's ceiling least, and NOT the flat 20-40% this sentence claimed
# over the same five examples: capabilities 1.4-3.1s then
# 0.8-0.9s, preflight 6.3-7.2s then 5.6-6.4s, portability 12.5-13.1s then
# 11.1-12.4s, secure_fs 1.8-2.1s then 1.4-1.5s, the host guard 1.6-1.8s then
# 1.4-1.5s. Same host, same idle machine, same commands, half an hour apart. The
# ranges it published were the UNION of both passes, and the reason to publish
# the union rather than the tidier second pass is that a reader comparing a
# future row against a floor taken while the machine was warm would read
# weather as a regression. Whatever the effect is — filesystem cache, thermal, the full-suite runs earlier in the sitting — it is
# LARGER than most of the between-round differences previous sittings narrated as
# causes, and it is the strongest argument in this block for reading ratios.
#
# Against the previous end-to-end sitting every expensive row came DOWN and the
# fast path went UP: the hooks module 132.1-135.6s against 131.4-136.2s (inside
# it, and narrower), the cold pass 155.4-159.3s against 158.6-169.6s, the
# skill-edit row 21.9-23.4s against 26.6-27.5s, `pytest tests/` 316.8-324.4s
# against 330.0-333.8s — on a suite TEN tests larger — and the fast path
# 0.07-0.10s against 0.05-0.06s. The fast path is the one worth pausing on,
# because this branch adds work to it: a loop over `compgen -v` unsetting every
# inherited RUN_ flag, on every fire. MEASURED WITH AND WITHOUT that loop, six
# runs each, and the ranges overlap — 0.078-0.090s against 0.089-0.135s, the
# version WITHOUT it reading higher. So the loop is not the cause and nothing
# here is: it is the same host weather the rest of the sitting shows, pointing
# the other way on the one row small enough for it to dominate. Read it as the same
# weather the double-measurement above demonstrates inside a single sitting,
# and read the ratios, not the seconds.
#
# THE COUNTS ARE AHEAD OF THE SECONDS whenever a round adds tests without
# re-measuring, and this is the note that says by how much — the gap stated
# ONCE, in the suite pair below, because a headline carrying its own numeral is
# a second copy of one quantity and this paragraph shipped exactly that: it read
# "two hundred and forty-eight" over a parenthetical giving 259, the headline
# being the round-before's figure left unrefreshed. That is the order this block
# prescribes for itself: counts are derivable in a second and go first, seconds
# cost half an hour of an otherwise-idle machine and are re-read as a SET when
# the branch settles.
#
# Against the counts the seconds were taken at, the suite has gained
# forty-six tests (4895 to 4941). THE SECONDS ARE OWED AND THE COUNTS ARE NOT, which is a
# state this block has been in before and has a shape for: the counts are
# refreshed here and the seconds are the previous sitting's, left standing and
# said to be standing rather than quietly re-used as though they were fresh.
#
# THE SITTING WAS ATTEMPTED ON THIS BRANCH AND DISCARDED. Every reading, not the
# implausible ones — the whole set or none is the rule this block opens with,
# and cherry-picking the believable rows is how a block comes to read precise
# and be fiction. What it produced: a FAST-PATH reading of 122.3s, on the row
# that is the hook deciding not to run pytest at all and has never, across
# eight sittings, been read above a tenth of a second; the hooks module at 119.2/129.6/150.6, a thirty-one-second spread
# against three and a half the sitting before; and a cold pass at 175.9 against
# a 155.4-159.3 range. The machine was not idle and the sitting was measuring
# that: load average 3.6 climbing to 6.9, with an endpoint scanner and XProtect
# together holding more than a core. It is a FEEDBACK LOOP rather than bad luck
# — a pytest pass opens thousands of files, which is the event the scanner
# exists to wake for — so this host cannot produce a trustworthy sitting while
# that is running, and no amount of patience at the same time of day changes it.
# The precondition in the header is "an otherwise-idle machine" and it was
# already corrected once, from "an untouched repository", by a sitting abandoned
# for the same reason (PR #118, readings of 119.4/111.0/2261.2/134.3s).
#
# COUNTS WENT IN AHEAD OF THE SECONDS, which is the order this block prescribes
# and here was also a precondition for measuring anything. A red module is
# CARRIED FORWARD into the next run, so while this block was stale the assertion
# that reads it was red and every hook invocation re-ran the hooks module on top
# of whatever the trigger selected. A skill edit selects the five modules whose
# row says 1035; carrying the hooks module in on top of them is that plus the
# 163 of its own row — most of a cold pass, for a trigger that should cost a
# fifth of one.
#
# AN EARLIER DRAFT PUT A MEASURED FIGURE HERE and it was wrong twice over. It
# read "observed running 1295", which is not the size of that selection nor of
# any other this hook makes: it is what the seven modules COLLECTED on the tree
# before this branch, quoted in a block whose next paragraph says counts are
# what RUNS. And it stayed green only because the numeral and the word `tests`
# fell either side of a line break, so the guard that rejects a stray
# `<n> tests` never saw it. Both halves are one mistake — a number recalled
# into a sentence rather than derived from the rows above it. A trigger-row measurement taken in that state is
# the trigger's cost plus the module that dominates every total here. Counts are
# derivable in a second and nothing about the host can contaminate them.
#
# So read every SECOND below as belonging to the tree of the previous sitting
# and every COUNT as belonging to this one. The ratios are the half that
# survives that gap, and the paragraphs below say so for their own reasons.
#
# EVERYTHING FROM HERE TO THE END OF THIS BLOCK IS HISTORY, and should be read
# as the ledger that debt accrued in rather than as anything a row above is
# derived from. It is kept because it is the record of what those tests were
# FOR, and because two abandoned sittings are recorded in it with the
# preconditions they taught. But every figure it steps through is the count of a
# tree that no longer exists, and "since the sitting" in it means since V5's,
# not since this one. Retiring it is its own change.
#
# The V5 ledger, from here down. Since THAT sitting a review round landed fourteen cases in
# tests/test_answers.py, W6 added forty-seven in a new tests/test_ci_workflow.py
# plus two in tests/test_pii_content_scan.py, and V1 added ninety-five in a new
# tests/test_return_builder.py — none of them a module this hook has
# an arm for, which is the case this whole mechanism exists for. The DECOMPOSITION has moved on
# exactly ONE row since the sitting, and it is the row that cannot help moving:
# secure_fs parametrises over `agents/lib/*.py`, so every new engine module adds
# a case to it, and the Windows job's own cases landed there too. NINE tests on
# one row, against a stated RANGE wider than nine of that row's tests can
# reach — which is the one shape a counts-only refresh survives. A SECOND row
# moving, or that row moving far enough to leave its range, buys the half-hour
# sitting. V1's second and third slices moved no row at all: neither added an
# engine module, and the third's thirty-two cases landed in the receipt,
# filing-package, pdf_render and return_builder modules, none of which this hook
# has an arm for. Y4 added `agents/lib/workspace.py`, one engine module, so the
# parametrised row above gained the one case this paragraph predicts — plus
# twenty-three in a new tests/test_workspace.py the hook has no arm for either.
# Y5 IS THE SECOND ROW, and it is the case the sentence above says buys the
# sitting: six cases landed in tests/test_portability.py, which has an arm, so
# that row moved on its own account rather than by parametrisation. The counts
# below are refreshed and the seconds are not, which is the state this block
# calls stale and legibly so — the sitting is OWED and has to be taken LAST, on
# a settled branch, because a measurement taken mid-review is one more of the
# four this header records as discarded. Y5's own review moved this row twice
# after the first refresh, which is that argument arriving as evidence. The
# reason the sitting is not being taken as part of Y5: the six cases are file
# reads — byte counts, two regex sweeps over two files each, and a directory
# glob — and the row's stated range is two seconds wide, so the seconds here are
# stale in a way the counts can be trusted through. That is an argument for the
# ORDER, not for skipping it.
#
# THE SITTING WAS ATTEMPTED ON 2026-09-10 AND ABANDONED, which is recorded so
# the debt reads as blocked rather than neglected. The branch was settled and
# nothing else was touching the repository — the precondition this block asks
# for — but the HOST was not idle in the way that matters: it was under memory
# pressure, and the sweep was killed part-way through `pytest tests/`, which
# never produced a reading at all. The tell arrived before the kill did:
# tests/test_claude_hooks.py returned 119.4, 111.0, 2261.2 and 134.3 seconds
# across four runs. A nineteen-fold outlier between two ordinary neighbours is
# the machine swapping, not the work changing.
#
# NOTHING FROM IT WAS WRITTEN DOWN, and that is the point of recording it. The
# rows that completed looked plausible — portability 12.1-14.3s, preflight
# 6.2-7.6s, the skill-edit trigger 21.1-22.1s — and taking the plausible ones
# while discarding the outlier is exactly how a set stops being a set. These
# figures are read as a SET or not at all; a sitting that lost one row to the
# host lost the sitting. The previous abandonment, noted above, was for edits
# landing mid-sweep; this one is the same failure with a different cause, and
# the precondition should be read as an otherwise-idle MACHINE rather than
# merely an untouched repository. Y6 is the round that shows what the order buys: its
# twenty-five cases landed in tests/test_pii_content_scan.py, which this hook has no
# arm for, so NO decomposition row moved and the debt did not grow — the sitting
# Y5 bought is still the one that is owed, and it is now owed on a larger scale
# row rather than on a second moving part. The round the seconds were taken at was V5's required-answer
# registry: sixty-eight cases in that new module, and ONE case in secure_fs,
# which parametrises over `agents/lib/*.py` and so gains a case for every engine
# module added. That single case is the only reason a decomposition row moved at
# all (159 to 160), and it is the shape to expect from a new engine module: the
# row it moves is never the module's own.
# What follows is the history the previous sittings accumulated, kept because it
# is the record of what those tests were FOR.
# First, five launcher tests, executing the
# two present-but-unusable `.venv` states `run.sh` refuses — the one with no
# activate script, which had shipped untested, and the one it cannot inspect,
# which had been refused with `rm -rf`. Then the round that masked SSN/ITIN at
# the extraction boundary (V0): seventy-seven cases in tests/test_parse_utils.py
# and two in tests/test_crypto_importer.py, neither a module this hook selects,
# so no row here moved and the whole of that gain sits in the scale row. Then
# the round that made the capital-gains breakpoints fail closed (X2):
# thirty-four cases across tests/test_tax_computations.py, test_scenario.py,
# test_projection.py, test_roth_ladder.py and test_ptet.py — again none of the
# seven. Then the tax-law ingest command with per-block provenance (V4):
# seventy cases in tests/test_tax_law_fetcher.py, likewise outside the
# seven. Then the capital-loss carryforward split with the finite-value
# boundary at rollover (X3): twenty-one cases in tests/test_rollover.py, outside
# the seven again. Then the status-specific capital-gains breakpoints (X1),
# with the Form 8938 threshold row its acceptance replay turned up: fifteen cases
# across tests/test_projection.py, tests/test_tax_law_fetcher.py and
# tests/test_tax_computations.py, outside the seven.
# Then the Codex review round on the V0 branch below: seventeen more cases
# in tests/test_parse_utils.py, for the table layouts a per-cell masker
# cannot see, the W-2 box labels that are not identifier labels, and a
# filename refused rather than masked into a path that opens nothing.
#
# Then the Codex review round on the V4 branch below: six more cases in
# tests/test_tax_law_fetcher.py, on how a block is dated and on the zero a
# state can state.
#
# Then the X3 branch's own: two more in tests/test_rollover.py, on an
# unfilled worksheet block and on a bound of zero that must not land.
#
# Then the validation receipt bound to the inputs the gates saw (V8):
# twenty-six cases in tests/test_receipt.py and three more in
# tests/test_filing_package.py, neither a module this hook selects, plus the
# ONE case that does move a row — secure_fs sweeps the PII gate's sensitive
# filenames and the receipt is now one of them.
#
# Then this branch's own Codex review round: six more cases across
# tests/test_receipt.py and tests/test_filing_package.py, on the window
# between a gate reading its inputs and the receipt describing them.
#
# Then its second Codex round: three more, on a document filename that must
# not be written down, a symlinked duplicate that must still read as an
# arrival, and the bytes a gate parsed rather than the file underneath them.
#
# Then its third: one more case, on verifying the bytes a package is
# assembled from rather than the file underneath them.
#
# Then the tracked summary renderer (V9): twenty-two cases in
# tests/test_pdf_render.py, and the one that moves a row — secure_fs sweeps
# every module under agents/lib for a network import and a raw path in a
# message, so a new module is a new case there.
#
# Then that renderer's own Codex round: four more cases, on a limitations
# sentence that ran off the page, a section that IS a list, and a
# computation warning read as commentary.
#
# Then its second: four more, on field names that are not the official line,
# an amount in the payer's own currency, a name reportlab read as markup,
# and a narrative taller than the page.
#
# Then its third: three more, on an override that belonged to another form,
# cents lost with the dollar sign, and a partial PDF left at the umask's
# mode when the build failed.
#
# Then its third: five more, on an override that belonged to another form,
# cents lost with the dollar sign, a partial PDF left behind, a carryforward
# read as a year, and an alphabet the embedded font does not carry.
#
# Then one writer for every runtime artifact (V10's first half): four cases
# in tests/test_secure_fs.py, which is one of the seven — the temp-then-
# rename five modules had each hand-rolled, and the two that wrote in place.
#
# Then that writer's own Codex round: one more case, on a failure to SECURE
# the temp file leaving it behind — the securing sat outside the cleanup
# scope.
#
# Then the fourth round on that renderer, taken against the merged commit
# once the review limit reset: three more cases, on a non-finite figure
# printed as a computed one, exchange rates lost with the meta block, and a
# prerequisite naming the retired generator.
#
# Then the parser's resource budgets (V11): nine cases in
# tests/test_parse_utils.py, which this hook does not select — the zip bomb,
# the output and time budgets, and the page budget that bounds a CALL rather
# than a file.
#
# Then that round's own: three more cases, on a clock checked only where a
# character was produced — a numeric worksheet and a page with no tables
# never reached the deadline.
#
# Then that round's own: two more cases, on a clock checked only where a
# character was produced — a numeric worksheet and a page with no tables
# never reached the deadline.
#
# Then its second: two more, on a numeric cell that cost no budget and a
# table-free last page with no deadline check after it.
#
# Then its third: two more, on an empty row that cost nothing and a CSV
# masking pass that ran outside the deadline it had just checked.
#
# Then its third: two more, on an empty row that cost nothing and a CSV
# masking pass that ran outside the deadline it had just checked.
#
# Then V3's first half — the eval fixture, the first golden and the
# comparator: twenty-three cases in tests/test_evals.py, a module this hook
# does not select.
#
# Then that harness's own Codex round: three more cases, on a fixture that
# could not pass the two gates it has to get past, and a boolean scoring as
# a number.
#
# Then its second round: three more, on a golden that encoded a shape the
# pipeline does not produce and a case that implied a penalty it did not
# answer.
#
# Then its third round: three more, on zero-valued lines the pipeline
# persists, a missing CA SDI rate that stopped the computation, and intake
# questions the answers left open.
#
# Then its fourth round: four more, on a required CA worksheet the golden
# omitted, deduction questions the answers left open, and identity values
# the comparator printed.
#
# Then its fifth round: two more, on a worksheet the SKILL says to write
# only when itemizing, and identity data inside a composite difference.
# The seconds are carried,
# which is what this paragraph is for. The scale row's
# were read again on the V0 branch as a check, not a refresh — one plain
# `pytest tests/` came back inside the stated range — and the row keeps the
# sitting's figure so that it is not two readings from two trees. Of the seven
# modules this hook selects,
# portability has lost two (247 to 245), preflight has gained one (382 to 383), the
# agents module none (48 to 48), secure_fs has gained zero (182 to 182), the hooks module
# has gained two (163 to 165), capabilities has gained ten (175 to 185) and the host guard has gained zero (94 to 94). Three zeros, a one, a two, a
# ten and a minus two are what an owed sitting looks like when most of what
# it would re-measure has stood still. FOUR modules have moved: preflight by the
# test the floor branch added, the hooks module by the two added for the trust
# cascade branches that had no test at all, capabilities by AA1's ten over
# the Earned Income Credit disclosure, and portability DOWNWARD, by the
# roadmap collapse trading nine assertions over five hand-kept copies of an
# item's status for seven over a file that states it once. A module can shrink,
# and the seconds it is measured against are no less trustworthy for it —
# 245 of the same tests run where 247 did. The pairs are kept rather than deleted
# because their job is to say whether the seconds can still be trusted — an
# empty ledger is an answer, not a missing one. EVERY pair is measured from the SITTING and not from the round
# before it, which is the other half of what shipped wrong here once: two of
# them were last-round deltas spliced into a sentence whose stated baseline is
# the seconds, so the six could not be reconciled against the total by anyone
# who tried. A module that SHRINKS is written `has lost <words>`, which is a
# shape the reader of this paragraph could not parse at all until it was asked
# for — the guard blocked the honest refresh in the one direction no round had
# yet taken. ONE pair reads none and six do not, and the hooks module is the
# newest of the six: the W2 tail moved the last hand-rolled engine walks onto
# tests/engine_tree.py and pinned the helper's arms by DERIVING which watched
# modules import it, which is the case that found test_claude_agents.py reading
# tests/skill_text.py with no arm selecting it.
#
# Of the forty-six tests the suite has gained since the sitting, zero landed in
# tests/test_secure_fs.py, MINUS TWO in tests/test_portability.py, one in
# tests/test_preflight.py, ten in tests/test_capabilities.py, two in
# tests/test_claude_hooks.py, zero in tests/test_host_guard.py, and
# the other thirty-five in test_ci_workflow, test_tie_out and in every other
# module this hook has no arm for. The portability module is the first to go DOWN across a
# refresh: the 2026-09-20 roadmap collapse replaced nine assertions that held
# five hand-kept copies of an item's status against each other with seven over
# a file that states it once. A count falling is the ordinary consequence of
# deleting a mirror, and the arithmetic below has to carry it like any other — fourteen of the thirty-five in that module, which is where the
# floor guard lives, twenty in tests/test_tie_out.py for AA1's advisory, and
# the last not a new test at all: it is
# tests/test_pdf_toolchain.py, which stopped SKIPPING because this tree has the
# untracked artifact it needs. Forty-seven tests were written and forty-eight more
# run, and that one-test difference between the two sentences is the tree
# rather than the suite. That reads as a formality, and the shape is the point: the breakdown
# that stood here named every round between V5's sitting and this one, module
# by module, and it was correct prose about a tree that no longer exists. What
# the sentence is FOR is reconciling the seven pairs against the suite gap, and
# it does that whether the numbers are large or zero. That last group is the ordinary case and the reason the pairs
# exist — a round that adds tests OUTSIDE the seven costs the block a sentence,
# not another half-hour sitting. The seconds are re-taken when the DECOMPOSITION
# moves by more than the one case a new engine module puts in secure_fs, and it
# now HAS: the sitting is owed, and the paragraph above this table says why it is
# owed LAST rather than now.
# They sum to eleven; the remaining thirty-five are modules this
# hook does not select, which is the reconciliation this sentence exists to
# state — and which a re-taken sitting makes trivially true on both sides. It is
# kept in that state rather than deleted because the sentence is the SET check:
# deleted, the seven pairs go back to being checkable one at a time, which is
# how two of them once carried last-round deltas on a sitting-relative baseline.
#
# Both halves are CHECKED — `test_the_measured_costs_counts_match_live_collection`
# reads every `(A to B)` pair in this paragraph, requires each B to be a figure
# the block states elsewhere, and re-derives the suite gap from its own pair.
# The prose used to be the half nothing read, which is how one quantity came to
# have two values under a mechanical check that was green throughout.
#
# The seven rounds BEFORE the last one each moved the scale row ALONE, by four,
# then three, then three, then five, then four, then three, then one: they added
# tests to projection,
# tax_computations, withholding, tax_calendar, ptet, retirement_dashboard and
# irs_dates, and NONE of the six modules this hook selects — measured, not
# assumed, the six re-collecting at exactly the counts above every time. That
# shape is what makes a counts-only refresh legitimate here rather than the
# patch the header forbids: the derived rows are sums of the six, so a round
# that touches none of them cannot move either one. Three rounds back that
# stopped holding twice running — one test to portability and five to
# secure_fs, then three more to portability — so the derived rows were
# re-derived from the six rather than carried over (688 to 694 to 697, 830 to
# 836 to 839). It has now run twice more: eight tests to projection and ONE to
# portability, then five more to portability alone, so the derived rows were
# re-derived each time (697 to 698 to 703, 839 to 840 to 845) rather than
# carried over on the assumption that a projection-only round had happened, and
# once more since, for four tests to portability alone (703 to 707, 845 to 849),
# and once again after that for two tests to PROJECTION alone, which left the
# derived rows exactly where they stood (707, 849) — the projection-only shape
# the seven rounds above describe, back again after four rounds that were not,
# and measured rather than assumed on the strength of that. The round after it
# was the OTHER shape, and the one the sentence below predicts: five tests to
# projection and two to portability, because it edited a SKILL and a reference
# beside the engine branch they describe, so both derived rows moved (707 to
# 709, 849 to 851). The round after THAT was the same shape again and wider —
# three tests to projection, three to portability and one to irs_dates, a
# six-finding round touching two skills, a reference, AGENTS.md and two engine
# modules — so the derived rows moved again (709 to 712, 851 to 854) while the
# scale row moved by seven rather than by their three, irs_dates being a module
# this hook does not select. The round after it was the PROJECTION-ONLY shape
# again in the strict sense — three tests, two to retirement_dashboard and one
# to projection, and NONE to the six — so the derived rows did not move at all
# (712, 854) and only the scale row did, by three. It edited two ENGINE modules
# and no skill, which is the case that shape belongs to. A
# review round that edits a SKILL is the case to expect a portability move in,
# portability being the module that reads the skills. The round after it was
# exactly that case, and the narrowest form of it: ONE finding, one portability
# test replaced by two and nothing added anywhere else, so all three moving rows
# moved by one together (712 to 713, 854 to 855, scale 3205 to 3206). A
# skill-only round is a portability-only round, and this sitting is where the two
# are visibly the same statement. The round after it was the MIXED shape and the
# commonest one: a defect stated in ten places across four skills, two engine
# modules, a template and a design note, which put one test in portability and two
# in projection — so the derived rows moved by ONE (713 to 714, 855 to 856) while
# the scale row moved by THREE (3206 to 3209). The gap between them is the whole
# reason the derived rows are re-derived from the six rather than tracked off the
# scale row. The round after THAT repeated it to the test — one finding stated in
# an engine note, two skill documents and the design note behind them, again one
# portability test and two projection tests, derived rows 714 to 715 and 856 to
# 857 against a scale row 3209 to 3212. Twice running is not a rule: it is the
# commonest shape recurring, and the sums were still re-derived from the six. The
# round after IT broke the run and took the NARROW skill-only shape instead — one
# finding in step 0 of one skill and the design note behind it, no engine change at
# all, so ONE portability test and nothing anywhere else — and all three moving rows
# moved by one together again (715 to 716, 857 to 858, scale 3212 to 3213). That is
# the same shape as the ONE-finding round four back, and the same statement it made:
# a skill-only round is a portability-only round. The round after IT was the MIXED
# shape once more, and the widest instance of it yet on the SKILL side: two findings,
# one of them a single wrong line number stated in four separate documents — a skill's
# own intake plus the three sibling skills that write the same field — and the other an
# engine note. That put TWO tests in portability and ONE in projection, so the derived
# rows moved by two (716 to 718, 858 to 860) while the scale row moved by three
# (3213 to 3216). Four documents and two derived-row tests is the ratio to expect from
# a SWEEP finding: the sweep is one test over N files, not N tests. The round after THAT
# went back to the NARROW shape — one finding, one skill's step 0 plus the step-1 line
# that already held the same check, one portability test and nothing outside it — so all
# three moving rows moved by one together (718 to 719, 860 to 861, scale 3216 to 3217).
# That is now three skill-only rounds in the last six, each moving the three rows by
# exactly the count of portability tests it added, which is what a skill-only round IS.
# The round after those went back to the MIXED shape, in its narrowest form and
# with an outside module none of the earlier mixed rounds used: one P1 in a
# skill's step 4 and one P2 in the DATE engine's California comments, which put
# ONE test in portability and TWO in irs_dates. So the derived rows moved by one
# (719 to 720, 861 to 862) while the scale row moved by THREE (3217 to 3220) —
# irs_dates being a module this hook does not select, which is the whole of the
# gap, and the second module after projection to play that part. Reading the
# scale row's three as the derived rows' three is exactly the error the
# re-derivation from the six exists to prevent. The round after IT was the
# PROJECTION-ONLY shape in the strict sense — no move in any of the six, so the
# derived rows stood at 720 and 862 and only the scale row moved, by ONE (3220
# to 3221) — and it reached that shape by a route none of the earlier rounds
# did. It edited a portability test and a TEMPLATE the portability module reads,
# which is the fingerprint of a portability move, and added no portability test
# at all: the finding was a fifth site for an EXISTING sweep, so the fix widened
# that sweep's tuple and its one test now covers five files instead of four.
# A sweep is one test over N files (the round five back says the same thing
# about the other direction), so EXTENDING one is the case where the file count
# moves and the test count does not. Which is why the shape is measured and
# never inferred from the diff: this round's diff looks exactly like a mixed
# round and counts like a projection-only one.
# The round after IT is the first in this whole log to move the AGENTS row, and
# the first whose two derived rows move by DIFFERENT AMOUNTS. Two tests went to
# `test_claude_agents.py` — a fixture-vacuity guard and a coverage check for the
# review-subagent acceptance record — and three to `test_preflight.py`, a heredoc
# form of the launcher rule that the existing per-line check could not see. The
# agents module sits in the cold-pass row alone; preflight sits in both. Two
# tests went to agents and THREE to preflight — the heredoc guard plus the
# positive and negative controls a later self-review pass added to it. So the
# skill-edit row moved by THREE (720 to 723) and the cold-pass row by FIVE (862
# to 867), with the scale row also five (3221 to 3226). Every earlier round in this
# log moved the two derived rows by the same amount or left one of them still,
# because every earlier round moved PORTABILITY, which is in both. Stepping one
# derived row from the other — in EITHER direction — is the error the sums-from-
# the-six rule exists to prevent, and this is the round that finally makes the
# two disagree numerically rather than merely one-sidedly.
# The round after IT put the two back in step, and by the OTHER module that sits
# in both rows. One review finding: the heredoc opener the round above added
# anchored end-of-line straight after the terminator, so `python3 - <<'PY' >
# fixture.json` — an opener carrying any redirection, pipeline or operator —
# registered as no heredoc at all and its body went unread. The fix widened that
# tail to a closed class of shell syntax, and its two tests are the controls on
# either side of the new boundary: one that the tails are now seen, one that
# prose QUOTING an opener still is not. Both went to `test_preflight.py`, so all
# three moving rows moved by TWO together (723 to 725, 867 to 869, scale 3226 to
# 3228). Every earlier round that moved the derived rows in step moved
# PORTABILITY; this is the first to do it through preflight, and the pairing is
# the same arithmetic for the same reason — a module in both rows moves both.
# The round after IT is the first to move the AGENTS row ALONE, and so the first
# where the two derived rows move by different amounts in the OTHER direction:
# agents sits in the cold pass and not in the skill-edit row, so the skill-edit
# row stood at 725 while the cold pass went 869 to 870 and the scale row 3228 to
# 3229. Five review findings, four of them fixed in tests already counted — a
# selector arm for `ptet.py`, a widened heredoc terminator, two controls added
# inside an existing test — and only the fifth added a test function: the
# acceptance guard now EXECUTES the record's fenced generator and audits the
# cache it writes, rather than re-deriving from its source what it ought to have
# written. That is the shape to expect from a review round whose findings are
# about a guard's discriminating power rather than its coverage: controls and
# arms go inside tests that already exist, so the counts move far less than the
# diff suggests.
# The round after IT was the AGENTS-ONLY shape a second time — one finding, one
# test — so the skill-edit row stood again at 725 while the cold pass went 870 to
# 871 and the scale row 3229 to 3230. Twice running in the last two rounds, after
# a log in which the agents row had never moved at all: the branch's subject is
# the review subagents, so the module that reads them is where its findings land.
# That is the same statement the skill-only rounds make about portability, from
# the other end of the table.
# The round after IT went back to PREFLIGHT, and to the pairing that module makes:
# both derived rows moved by TWO (725 to 727, 871 to 873) with the scale row also
# two (3230 to 3232). One finding, two tests — the third consecutive round on the
# same heredoc opener, and the one that stopped widening the pattern and changed
# what it is keyed on. Three rounds of tails is the signal worth keeping here: a
# regex being asked to tell a command from a sentence had been given no way to,
# and each widening bought one form and moved the false-positive edge. The two
# tests are the fence's two directions plus the malformed-block bound, which is
# the shape a discrimination fix takes — one test per side, never one per form.
# The round after IT was PREFLIGHT again and the fourth in a row on that module,
# but the first whose findings were about the guard's PREDICATES rather than its
# pattern: an engine import stopped at a dot after `lib`, so the package-level
# `from agents.lib import x` was not an engine call, and routing was read off the
# whole line, so a launcher anywhere left of the interpreter cleared it. Two
# findings, THREE tests — and the third is the shape worth recording. Both
# defects had a SIBLING in the older skills-side audit that neither finding
# cited, sharing the dot and the line-wide read, so the fix hoisted one
# `_owning_command` and one import spelling for both audits and the third test is
# the control pair on that older one, which had none. All three moving rows moved
# by three (727 to 730, 873 to 876, scale 3232 to 3235). A round that sweeps a
# sibling costs one more test than the findings do, never N more: the sibling
# shares the fix, so what it needs is a control, not a copy.
# The round after IT moved BOTH of the modules this branch has been alternating
# between, and so is the first where the two derived rows part company by THREE:
# preflight sits in both rows and gained one test, agents sits in the cold pass
# alone and gained three, so the skill-edit row went 730 to 731 while the cold
# pass went 876 to 880 and the scale row 3235 to 3239. Three findings. The
# preflight one is the fifth consecutive round on the same heredoc opener and,
# like the two before it, spent its test on a CONTROL rather than on coverage:
# the terminator now matches sh's rule — the delimiter alone on the line, tabs
# stripped for `<<-` and nothing stripped for `<<` — instead of `.strip()`'s, and
# the one added test is the cases either side of that boundary. The other two are
# the agents module's, and they are one defect asked of two different things: a
# recorded PASS was bound neither to the definition bytes it judged nor to the
# claim CLAUDE.md makes about it, so an edited prompt and a regressed re-run both
# left the trust signal green. Two audits and ONE shared control is three tests
# for two findings — the sibling-sweep arithmetic above read from the other end.
# A round whose findings are about a record's EVIDENCE rather than a pattern's
# reach pays for the control once, however many audits come to share it.
# The round after IT is the first in this log whose findings landed OUTSIDE the
# six modules as well as inside them, and so the first where a routing arm moved
# for a reason the counts do not show. One test went to preflight (731 to 732,
# 880 to 881, scale 3239 to 3240) and the other three findings cost no test at
# all: two were new cases inside the agents module's existing control, and the
# third was a MAPPING defect — test_preflight's DOCUMENTS grew two entries,
# AGENTS.md and the issue template, that this hook routed to portability and to
# nothing. Editing either turned preflight red at CI with no local signal. The
# arm is fixed, and so is the reason nobody saw it: the read-set locator in
# tests/test_claude_hooks.py recovers `REPO / "literal"` chains and cannot see a
# path read over a loop, which is exactly how a swept document set is written. It
# now also recovers a constant tuple whose every element names an existing file —
# measured against these six modules that is one constant and no noise, where
# "any literal that happens to be a path" was fifty-four entries of `.git` and
# `.venv`. A round can move this table by one test and still be the round that
# closed the largest gap in it.
# The round after IT parted the two derived rows again, by ONE this time rather
# than by three: one test to preflight and one to agents, so the skill-edit row
# went 732 to 733 and the cold pass 881 to 883 with the scale row also two (3240
# to 3242). Three findings, and all three were the same defect in three places —
# a guard that reads a SHELL construct one line, or one claim, at a time. A
# `\`-continued heredoc opener put the interpreter and the delimiter on
# different lines and was not seen at all; an escaped quote inside a `-c` body
# split one command into two, so neither half was an invocation; and a second
# `**Both PASSED <date>**` in CLAUDE.md was resolved by `re.search` to the older
# claim, which the record still backs. Two tests for three findings is the
# controls-inside-an-existing-test shape this log has recorded twice before: the
# escape fix went into the activation control that already stood, and only the
# forms with no control of their own — the continued opener, the doubled claim —
# needed a test function. This round also found the prose half of THIS block
# holding one quantity at two values, 225 in two sentences against 230 in a
# third, which is the drift the mechanical check does not reach because the
# figure is spelled in words. Refreshed with the rest; a round that edits the
# table refreshes them together or the next one inherits three values.
# The round after IT is the first in this whole log to move NOTHING — not the
# derived rows, not the scale row, not even by one. Two findings, both real, both
# fixed, and every control they needed went inside a test that already existed;
# the only other structural change was a RENAME, `test_the_per_line_check_...`
# becoming `test_the_document_check_...` because the check it names stopped
# reading one line at a time. Earlier rounds in this log recorded the
# projection-only shape as the floor — no move in the six, one in the scale row —
# and this is the floor beneath it. The findings were that the launcher rule
# asked for the launcher's LITERAL rather than its execution, so a mention in a
# comment or a quoted body cleared a bare engine call, and that the documents
# audit read physical lines, so a `\`-continued invocation matched nothing. What
# a round like this costs is not tests but PREDICATES: three of them were merged
# into one the two audits now share. A count column is a poor proxy for the size
# of a change, and this is the round that says so most plainly.
# The round after IT moved PREFLIGHT alone by one — all three moving rows by one
# together (733 to 734, 883 to 884, scale 3242 to 3243), the pairing that module
# makes. Three findings, and every one of them was a predicate reading TEXT where
# the shell reads STRUCTURE. A `NAME=value` prefix is not a command word, so
# `RUNNER=agents/lib/run.sh python3 -m agents.lib.ptet` read as routed and the
# compliant mirror read as bare — one construct, both directions wrong. A `<<`
# inside a quoted string is not a redirection, so a block that only ECHOED an
# example was indicted. And the LAST `<<` on a command is the one python is fed,
# so `<<A <<B` had its executed body read as ordinary text. One test for the
# three, because the last two are one rule about which `<<` is real and they
# bound each other: taking the last is only safe once the quoted ones are gone.
# The round also had to teach the quote scanner that `$( ... )` inside double
# quotes is ACTIVE — an existing test caught that immediately, which is the
# system working as designed, and is why the seventh finding this branch has
# produced against its own guards cost nothing to find.
# The round after IT moved no row either — five findings, every control inside a
# test that already stood — but it is the first to change what a TRIGGER costs
# rather than what a row counts. Editing any of the six test modules now selects
# the hooks module as well, because test_claude_hooks PARSES all six to re-derive
# this mapping, so a path constant added to one of them could only ever go red in
# CI. Nothing in the table above moves: none of its four triggers is a test-module
# edit. What moves is the trigger that is NOT listed — a test-module edit, which
# went from its own module to a cold pass — and the reason it is worth writing
# down here is that this block is where the cost of a trigger is supposed to live,
# whether or not the trigger has a row. The arms were a hand edit until the read-
# set locator was widened to see a lone string constant naming a file; now the
# coverage test demands them, and reverting one names the dependency in its own
# failure message. SUPERSEDED fourteen rounds later, and this pointer is the
# whole of why the entry stays: the arms take a node-id subset now, not the
# module, so the cost stated here is the cost of a policy no longer in force.
# THE TRIGGER WITH NO ROW, above, is the current one — a log is a history, and
# the standing statement of a trigger's cost cannot live inside one. A reader
# who stops at the first entry naming the trigger stops at the wrong one, which
# is the whole reason this pointer is not left to the reader's diligence.
# The round after IT moved no row either — a third in a row, and the shape is now
# the branch's own: four findings, every control inside a test that already stood.
# Two were the previous round's fixes arriving incomplete, which is worth the note.
# Making the not-accepted state WRITABLE guarded the two audits and not the
# CONTROL beside them, so the state was still uncommittable — by a TypeError three
# frames down rather than by an assertion. And the withdrawal was checked AFTER
# the pass claim was returned, so a file carrying both sentences resolved to the
# stale PASS. A fix for a workflow-blocking guard has to be committable in the
# state it unblocks, and a fix that adds a second state has to say what happens
# when both are present; neither is something the fix's own tests will ask.
# The round after IT took the AGENTS-ONLY shape for the third time in this log —
# one test to that module and none anywhere else, so the skill-edit row stood at
# 734 while the cold pass went 884 to 885 and the scale row 3243 to 3244. Four
# findings, and the one that bought the test is the only kind that can: the other
# three were a predicate swap, an added assertion and a rewritten fixture, all
# inside tests that already stood. That one is worth the note because it is a
# class this log has not recorded before — a REMEDY that does not work. The audit
# printed `shasum -a 256` for renewing a pin while the pin is taken with the
# carriage returns stripped, so a maintainer on a CRLF checkout who followed the
# failure message recorded a digest the same audit rejects, and the only way out
# of the loop is to stop believing the guard. The command now lives in one
# constant that both the message and the record are checked against. A guard's
# accuracy and its remedy's accuracy are separate properties and only the first
# has ever had a test here.
# The round after IT moved the HOOKS row, which no round in this log had moved
# before — so the cold pass went 885 to 886 and the scale row 3244 to 3245 while
# the skill-edit row stood at 734, the hooks module sitting in the cold pass
# alone. Three findings, and the one that added the test is the reason this
# paragraph is here at all: the round BEFORE it added `RUN_HOOKS=1` to the six
# test-module arms, and one of those arms also named
# `agents/templates/taxpayer_profile_template.json`. So every edit of a JSON
# template began paying for the slowest module in this table, and nothing in the
# suite could see it — every other assertion about the mapping is a LOWER bound
# ("does this path reach the module that reads it"), which an arm selecting
# everything satisfies perfectly. Cost is the only thing that makes an
# over-broad arm wrong, so cost is the only thing that can catch one, and this
# block is where the cost of a trigger has always lived. The new test is the
# first UPPER bound on the mapping. Its own first draft then collided with the
# read-set locator — a constant tuple of existing paths is exactly what that
# locator reads as "this module reads these", so a list declaring two paths
# UNCOUPLED from the hooks module made the coverage test demand they select it,
# and two tests asserted opposite things about the same two paths. Pairing each
# path with the module that does read it fixed both halves at once.
# The round after IT moved NOTHING — the second such round in this log, and the
# second in five. Two findings, both in the shell scanners, both fixed inside
# tests that already stood. What they have in common is worth more than the
# arithmetic: each was a character the shell reads as part of ONE token that the
# scanner read as two. `&>` is a redirection whose first character is the
# separator `&`, so `python3 &>/dev/null -m agents.lib.ptet` was cut in half and
# neither half held an interpreter AND an engine target; a `<<` inside a `#`
# comment is a sentence, so the greedy reach for the LAST `<<` took the
# commented one as the delimiter and closed the body before the import. Both
# report CLEAN, which is the fail-open direction, and neither needed a test
# function because both audits already had a control asking exactly this
# question of a different character. A round that adds no test can still be the
# round that closes two silent passes; the count column measures coverage, not
# reach.
# The round after IT parted the two derived rows by TWO, the widest gap this log
# has recorded, and did it with both of the modules this branch alternates
# between moving at once: two tests to preflight, which sits in both rows, and
# two to agents, which sits in the cold pass alone — so the skill-edit row went
# 734 to 736 while the cold pass went 886 to 890 and the scale row 3245 to 3249.
# Three findings. Two were the launcher rule read as TEXT again: routing was
# granted by `startswith`, so every executable the launcher's path is a prefix of
# — `run.sh.bak`, a rename, a half-finished edit — inherited the clearance, and
# the heredoc BODY was still read one physical line at a time, so a `\`-continued
# `from agents.` / `lib import ptet` executed the engine while neither half named
# it. The third is the one worth the note: the acceptance record has TWO replay
# fixtures and only the generated one had a guard. The other is a git SHA, and a
# SHA moved to the FIX rather than its parent leaves an honest empty report that
# reads exactly like a broken reviewer's — the class this whole module exists for,
# reached through the input nobody was watching. It cost the second agents test
# too, for a reason none of the earlier rounds had: the guard SKIPS what a shallow
# clone cannot reach, and CI checks out shallow by default, so the workflow's
# depth became part of whether the guard runs at all and is now pinned. That in
# turn moved the ROUTING table — a test module read `.github/workflows/ci.yml`
# for the first time — and the read-set locator said so within the same pass,
# which is the second time this log records the coverage test catching a mapping
# gap the same hour it was created rather than at CI.
# The round after IT was the NARROWEST shape this branch has: ONE test to
# preflight and nothing anywhere else, so all three moving rows moved by one
# together (736 to 737, 890 to 891, scale 3249 to 3250). Two findings, both in
# the heredoc scanner and both the SAME defect the round before it fixed at one
# site: the launcher read as text rather than as a POSITION. A near-miss check
# written as `LAUNCHER in head` indicted `echo sh agents/lib/run.sh.bak <<'PY'`,
# which runs nothing, and a `case` arm or a function definition put a token
# ending in `)` where the command word goes, so `yes) python3 - <<'PY'` opened no
# heredoc at all. One is a gate firing on a document that quotes the rule
# correctly and one is a silent pass, which is the pair this scanner keeps
# producing from the same root. Two findings closed by one anchored regex and one
# prefix, and only the second needed a test function: the first is a third case
# added to the control that already asks where the launcher sits.
#
# And the round after THAT repeated the shape exactly — one test to preflight,
# nothing else, all three moving rows plus one (737 to 738, 891 to 892, scale
# 3250 to 3251) — for a third consecutive round of the same root. `env -u` takes
# an OPERAND, and the prefix that unwraps the exec words consumed the option
# without it, leaving `PYTHONPATH` standing where the command word goes: an
# argument handed back as an executable, which is the position error the two
# rounds before it fixed in two other spellings. Its two siblings needed no test
# at all. One was an acceptance criterion whose fail condition named a KEY where
# its pass criterion names a FINDING, so an auditor that found the fixture's
# unplanted source-pin defect and missed the planted stale value satisfied it —
# the identical criterion-versus-condition gap Replay A had, one replay along.
# The other was this file: `tax_computations.py` became the third engine module
# `test_claude_agents.py` reads and got no arm, so the QBI assertion added with
# it could go red at CI while the hook ran five modules and exited 0. That edge
# is an IMPORT, and the derivation test recovers path literals, so the hand list
# was the only place it could have been caught.
#
# The round after THAT moved the AGENTS row again, 30 to 31 — the FOURTH round
# in this log to move it, after the three recorded above, and the first to move
# it in the same round as PREFLIGHT. That pairing is what is new, and it is what
# makes the three moving rows disagree three ways at once: two tests to
# preflight and one to agents means the skill row gained two (738 to 740) and
# the cold pass three (892 to 895), against a scale row that also moved three
# (3251 to 3254). Read either derived row off the scale row's three and one of
# them is wrong; that is the arithmetic the re-derivation from the six exists to
# do. The agent-definition TRIGGER row moved with it, being the same
# measurement under another name. Two of the three findings were the heredoc
# scanner again and were one root wearing both faces: `$( ... )` NESTS, and only
# the `$(` half was counted, so a subshell's `)` popped the substitution and the
# double quote under it made the rest of the line inert — opener, delimiter and
# all — while `<<<` is a here-string whose last two `<` the opener pattern
# happily matched, inventing a heredoc terminated by a word no line equals. One
# silent pass and one gate crying wolf, from one habit of reading a construct
# two characters at a time. The third was the acceptance record: `## Replay`
# sections collapsed through a `dict`, which is the identical collapse `##
# Results` had fixed one function above, so an appended revision left the marker
# check reading the LAST section while the pin and the criterion read the FIRST.
# Which case a round is, is MEASURED by
# `test_the_measured_costs_counts_match_live_collection`, never assumed. The gap
# stated above — and stated ONCE, this sentence having carried a second copy of
# it that was two tests stale, inside the block whose whole subject is a figure
# written twice — is worth well under a second and cannot move a
# row that spans 17 seconds at its narrowest, so the ranges stand — but a reader
# deriving anything from the gap should know it is there rather than infer that
# the sitting covered it.
#
# The round after that moved the SAME two rows by one each, and the derived rows
# came apart a third way: preflight and agents each gained a test, so the skill
# row went 740 to 741 while the cold pass went 895 to 897 and the scale row also
# two (3254 to 3256). The cold pass and the scale row have now agreed twice
# running, which is not a rule and must not be read as one — they agree exactly
# when every test a round adds lands inside the six, and three rounds back the
# scale row moved ALONE. The other gap was called STRUCTURAL here — "an agents
# test never reaches the skill row" — and it is not, and was not: the skill row
# carries five module rows and the FIRST of them IS the agents module — a sum
# deliberately not restated here, because the table above owns it and a second
# copy in a paragraph about a superseded round is how this block drifts. What the skill row does not carry is the hooks module and the host
# guard, which is the gap that actually never closes.
#
# Both findings were fail-open, and the scanner one is the same habit a third
# round running — a construct read at the wrong granularity. A command
# substitution that is not the FIRST token: `_inert` handed the `$(` the flag of
# whatever surrounded it, so inside a double quote the OPENER was inert while
# everything it contained, its own `)` included, stayed active. `_commands`
# splits at an active `(`, so the split never happened, `echo "$(python3 -
# <<'PY')"` was one command with `echo` for a head, no heredoc was counted and
# the body was never read. One line deleted. The rule that DID cover the leading
# form went with it: it now fires nowhere in the tree and no test kills its
# removal, and leaving it would leave the trap, because the obvious repair is to
# widen it from the first token to any position — and `_command_head` returns
# ONE position, so entering the substitution discards the command that OWNS it
# and `python3 -m agents.lib.ptet "$(date)"` reads as a `date)"` that calls no
# engine. A line holding a substitution holds two commands, so splitting is the
# only answer to it. Measured across the corpus before adoption: seven more
# command units, two fewer heads moved, and ZERO verdicts changed — the hole
# closed is entirely prospective. The other finding was the replay pin's
# shallow-clone skip, keyed on shallowness ALONE: `git show` fails on a
# misspelled path too, so a broken record in a `--depth` clone was excused by a
# message asserting the checkout lacked a commit it was holding. A guard
# reporting the wrong reason for its own silence is worse than an absent one,
# the message being what stops anyone looking. `git cat-file -e <sha>^{commit}`
# separates them, and the test builds a real two-object shallow repository —
# `.git/shallow` is a file — rather than stubbing the `git` calls it changed.
#
# The round after that moved ONE row, and it is the first time in this log that
# the derived rows can be read off a single delta without being wrong: both
# findings landed in preflight, so the skill row and the cold pass and the scale
# row all moved by its two (741 to 743, 897 to 899, 3256 to 3258) while the
# agents row stood. That is the shape the seven rounds before last had, and it
# is a property of WHERE the tests went, never a rule — the re-derivation from
# the six is what says which case a round is.
#
# Both findings were about the same function again — `_command_head`, which is
# now the fourth round running — and this time about the grammar it enumerates
# rather than about a construct read at the wrong granularity. `[assignments]
# [redirections] word...` is the shell's own shape for a simple command, and
# REDIRECTIONS had no rule at all, so `2>/dev/null python3 - <<'PY'` handed back
# `2>/dev/null` as the executable: not an interpreter, no heredoc counted, body
# never opened. `time` had a rule one token short — `-p` is the only option
# bash's `time` takes — and left `-p` standing in the same place. Both silent.
# The new rule is deliberately NARROWER than POSIX, and that was measured rather
# than preferred: the grammar-faithful spelling moves 140 command heads in this
# tree and 126 of them are markdown BLOCKQUOTES, the one class most likely to
# quote a command; requiring the target to be attached and path-shaped leaves 2,
# both prose. Zero verdicts change under any spelling, so the choice was about
# which noise to invite. The second finding was the body scan reading a heredoc
# as flat text, so a python COMMENT or a string that merely quotes the rule was
# reported as the engine running on a bare interpreter — the shell-side `echo
# 'python3 - <<PY'` case one language over. The body is tokenized now, with two
# bounds that keep the fix from being a hole: a body that calls exec/eval/
# compile has its strings read raw, and a body the tokenizer refuses is read raw
# as well, which is exactly the behaviour that shipped.
#
# The round after that is the AGENTS-AND-PREFLIGHT shape a second time, and the
# largest single move in this log: ten tests to preflight and one to agents, so
# the skill row gained ten (743 to 753), the cold pass eleven (899 to 910) and
# the scale row eleven with it (3258 to 3269). The two derived rows come apart
# for the fifth time and by the widest margin yet, which is the arithmetic the
# re-derivation from the six exists to do — read the skill row off the scale
# row's eleven and it is out by one.
#
# The round after THAT is AGENTS-ONLY, a shape this log had not seen: three
# tests to test_claude_agents.py and none anywhere else, so the skill row did not
# move at all (753) while the cold pass gained three (910 to 913) and the scale
# row three with it (3269 to 3272). It is the sharpest form of the argument for
# re-deriving both rows from the six rather than tracking the scale row: read
# the skill row off the scale row's three and it is out by exactly three, on a round
# whose whole content is a module the skill row does not select. Preflight
# collected the same 322 across it, having gained cases rather than tests — a
# widened predicate exercised through the existing controls, which moves no
# count and is why a count is not a measure of coverage.
#
# It was a review round rather than a feature one, and the size is the shape:
# fifteen findings, none of them a live red, almost all of them a guard that
# declines to LOOK rather than one that looks and is wrong. Six were fail-open
# holes in the heredoc scanner — a pipe into an interpreter, an exec wrapper the
# prefix list did not name, a second heredoc on one line, a backslash-quoted
# delimiter, a four-backtick fence read inside out, a body written to a file and
# run later — and three were the same scanner crying wolf, on a routed line's
# comment, on `re.compile` reading as a dynamic exec, and on a t-string that
# lexed differently between the two interpreters CI runs. A guard that silently
# declines to look and one that looks and finds nothing are the same green, which
# is this branch's own subject arriving inside the branch.
#
# THE ROUND AFTER IT WENT UNLOGGED, and that omission is the finding this entry
# and the two below it settle. It moved preflight twenty-one (322 to 343) and
# the agents module has gained twelve (36 to 48), so the skill row went 753 to 774, the cold
# pass 913 to 935 and the scale row 3272 to 3294 — an AGENTS-AND-PREFLIGHT round,
# the third in this log, parting the two derived rows by one. The table was
# refreshed for it and the ARITHMETIC sentence four paragraphs up was not, so the
# block carried 753 and 913 in prose against 774 and 935 in its own columns for a
# whole round: one quantity, two values, under a clause promising it "moves with
# the table now". A log entry is not decoration here — it is where a refresh is
# reconciled, and a round with no entry is a round nobody reconciled.
#
# The round after THAT is this one, and it is the AGENTS-AND-PREFLIGHT shape's
# sibling: PREFLIGHT AND HOOKS. Seven tests to preflight (343 to 350) and two to
# the hooks module has gained four (124 to 128) and one to the agents module (37 to 38), so the
# skill row moved by SEVEN (774 to 781) and the cold pass by TEN (935 to 945),
# with the scale row also ten (3294 to 3304).
# The two derived rows part company for the sixth time, and by two modules at
# once — read the skill row off the scale row's ten and it is out by three, which
# is the hooks module and the agents module, both sitting in the cold pass alone.
#
# Ten review findings, six of them in the preflight lexers and every one the
# same root a fourth branch running: a predicate that recognises a construct by
# its SPELLING. `-c` had to be detached, so `python3 -uc '` opened no join; a
# redirection among the arguments was read as the script operand, so
# `python3 2>&1 <<'PY'` opened no body; a quoted assignment spanning lines was
# not joined, so the name it binds read as empty; a heredoc bound to a variable
# had no reading at all; and the interpreter itself was `python` or `python3` and
# nothing else, so the Windows launcher, a path with an interior `..`, a
# cwd-relative operand after a `cd` and an alias each reached the engine green.
# Two more were this block: the arithmetic sentence above, and a reconciliation
# that could express only non-negative deltas.
#
# The tenth is the one that moved the HOOKS row and the reason a trigger's cost
# changed again. `RUN_HOOKS=1` on the five remaining test-module arms — added
# two rounds after the arm this log already records as an over-broad selection —
# charged every save of a test module the dominant row in this table for a
# dependency that is THREE assertions. They are selected by node id now, as a
# check of their own; the two tests are the upper bound that would have caught
# it and the pin that keeps the ids real. A lower bound is satisfied by an arm
# that selects everything, which is why cost is the only thing that catches one,
# and this is the second time that sentence has had to be written here.
#
# A cross-file tracer then landed two more against the same round and neither
# moved a count, which is why they sit in this entry rather than a new one. The
# first is the sibling sweep this repository keeps re-learning: a rationale
# written as HISTORY — an allowlist entry said to have been REPLACED, in a file
# whose every revision says otherwise — stood in THREE places, two documents were
# corrected and the scanner's own comment was the one left. The second is the row
# above: the hooks module's RUN figure is the only one no collection can reach,
# platform skips being conditional, so it is pinned to the block's stated skip
# count from both sides with a tripwire under it. A skip added to an EXISTING
# test moves no collected count, so every assertion here stayed green while that
# row and the cold pass summed from it drifted — the drift this block's header
# records four instances of, arriving through the one quantity none of them was
# about.
#
# THE ROUND AFTER THAT moved NOTHING — the third such round in this log — and it
# is the one that made this block's ownership of a trigger's cost true rather
# than merely claimed. Nine findings, every control inside a test that already
# stood, so no row and no derived row and not the scale row moved. FIVE of them
# were about the entry immediately above — the round that introduced
# `RUN_HOOKSDEPS` — which is the sibling sweep this repository keeps re-learning
# arriving inside the fix that created it, and the count is the argument: a
# round that narrows a gate has to be swept as one change and was not.
#
# The first is the one that changes behaviour. `RUN_HOOKSDEPS` had the counts
# check in it, so ADDING a test to any of the five turned the gate red and the
# red carry-forward re-blocked every tool call until this block was refreshed in
# six places — a refresh that can only be made coherently once a round's tests
# are final. A gate firing on correct in-progress work is the pattern the
# `--deselect` pair at `run_focused_modules` already records, met a second time
# through a check that is factually RIGHT and fires where nothing can be done
# about it. It is out of the list and stays in CI, in the review, and on every
# edit that takes `RUN_HOOKS` — which is every edit to the file this block lives
# in. The second is the other direction: the list called itself the whole of
# what a test-module edit can turn red while omitting the deselect pin, and its
# own guard was a lower bound over two hardcoded names, which cannot see an
# omission. Pinned as a SET now, both directions, the way the deselect pair
# beside it is. The third is THE TRIGGER WITH NO ROW above, which is the cost
# neither of those two decisions could be judged against.
#
# The pin's resolution check was a substring search, which a strict PREFIX
# satisfies while collecting nothing — and pytest answers a selection like that
# with exit 4 for the whole pass, which this hook holds its stamp on and reports
# as a block with no verdict behind it. A wedge, from a rename, with the suite
# green. Two more were the preflight lexers reading a CONSTRUCT by its spelling,
# which is now a fifth branch running: a script operand was relocated only when
# it was a bare FILENAME, so `cd agents && python3 lib/ptet.py` cleared while the
# `-m` twin of the same command was reported, and a `read` option was tested as
# a whole WORD, so the bundled `read -rd '' CODE` — the spelling every runbook
# uses — bound nothing and its body went unread. One was the replay pin's
# blobless arm, keyed on partial-and-present without asking whether the commit's
# TREE holds the path: the shallow arm's own defect, one environment along, and
# the control that catches it is the one the shallow arm already had. The last
# was a vocabulary closed at what the corpus happened to contain — no thousand,
# and words the only accepted spelling — so the first four-figure delta would
# have had no writable form and the exit would have been to edit the test. The
# ninth cost nothing to fix and is the same round's: CLAUDE.md described the
# selection as "six test modules and a node-id subset of the seventh", and there
# is no seventh — the subset is of tests/test_claude_hooks.py, which is one of
# the six. A miscount in the one file an agent has loaded at session start.
#
# THE HAND-REFRESH FAILED AGAIN, which is why the counts are no longer kept by
# hand. The revision that made this block the SOLE owner of these figures — it
# deleted the count from docs/contributing/test-invariants.md in the same
# change — shipped a portability row five tests light and a scale row twenty,
# under the very paragraph below asserting every count had been re-derived by
# measurement. Four hand-refreshes, four drifts. So the rule this block states
# is now ENFORCED: `test_the_measured_costs_counts_match_live_collection` in
# tests/test_claude_hooks.py collects each of the seven modules and the whole
# suite, derives portability's gap from the hook's own `--deselect` flags, and
# re-computes both derived rows as sums of the parts. A stale count column is a
# red suite. The SECONDS stay unenforceable — they need an idle machine — which
# is exactly why the two halves are recorded and dated separately.
#
# EVERY COUNT ABOVE WAS RE-DERIVED BY MEASUREMENT, not by adding this round's
# delta to the previous figures, and the difference is the point: the suite row
# had drifted NINE tests while the note above it claimed to be current to seven,
# so a reader adding a delta to it would have carried the error forward and
# widened it. `--collect-only -q` on each module answers this in about a second.
# Do that instead, every time — or just read the failure the test above prints.
# Re-reading ONE row against neighbours measured in
# another sitting is the separate failure this block has shipped twice; that is
# why the SECONDS get a note and not a patched row, while the COUNTS get
# re-measured outright.
#
# What the previous revision recorded here, kept because it is the same lesson
# arriving from the other side: that revision ran eight tests ahead, the branch
# settled, the whole block was re-read and the gap note came out with it. The
# gap re-opened one round later. It will keep re-opening, and the block stays
# honest by SAYING which half is current rather than by pretending they never
# diverge. What it must never do is patch a count column and carry the seconds —
# nor, as this round found, patch the seconds and carry a count SENTENCE: the
# fourth round's header said fourteen tests and eight to portability where the
# real deltas were twenty-four and seven — deltas its OWN table three lines below
# carried, reading 2978 to 3002 and 131 to 138 as it then stood, while the same
# paragraph went on to say "gaining seven tests" in its very next clause. Prose
# absolutes go stale exactly as fast as the columns do, and are harder to see.
#
# Counts are derivable in a second, so a stale one has no excuse and they are
# refreshed with every change; seconds cost half an hour of a machine that has
# to be otherwise idle, so they are re-read as a SET when the branch settles
# rather than after each round. Re-read as a set is the point: a row measured in
# a different sitting than the rows it is compared against is how this block
# twice shipped a decomposition that did not sum. A row added later is
# re-measured with the rest rather than appended to an older run. The previous revision
# was not, and shipped counts that predated the branch's own additions: the
# hooks module read 105 while that branch had added a test to it, and the
# derived rows were arithmetic over stale parts. Counts are derivable in one
# second and there is no excuse for a stale one; seconds are not, which is why
# they carry run counts and why the note about ratios above still governs.
#
# The hooks module dominates the total, but it no longer dominates the NOISE,
# which is what parts and whole can agree to. Its own spread is 3.5s this
# sitting against 7.0s for the other six added together — most of that
# preflight's 4.3s and the host guard's 1.1s — where it was 4.8s against 5.5s
# the sitting before and 15.3s against 7.8s the one before that, when this
# sentence said it swung more than every other module combined and that was
# true. TWO SITTINGS RUNNING, the module that dominates the TOTAL has been the
# smaller half of the NOISE. The SUM of the per-module spreads is 10.5s here —
# preflight 4.3, hooks 3.5, the host guard 1.1, portability 0.7, and nine
# tenths between the other three — and that figure is NOT a threshold, which is
# what this paragraph used to make it. Both halves are RANGES: the parts run
# 156.2-166.7s and the whole 155.4-159.3s, so a disagreement of 11.3s is
# consistent with both without anything having changed, and the cold-pass row
# spans 3.9s on its own. An endpoint difference smaller than that says nothing
# at all.
#
# WHAT A FUTURE READING CAN SAY IS LESS THAN THIS PARAGRAPH HAS TWICE CLAIMED.
# Three or six readings are sampled extrema, not bounds, and this block's own
# ledger settles it: capabilities went 1.4-3.1s then 0.8-0.9s inside ONE
# sitting with nothing touched between — DISJOINT ranges from weather alone. So
# non-overlap does not establish that anything changed, and overlap does not
# establish that nothing did. Both are reasons to look, and neither is a
# verdict. This sitting the two halves do NOT coincide: the parts start 0.8s
# above the whole and end 7.4s above it, both ends stated because one of them
# alone is the half that flatters the agreement. Where the 7.4s comes from is
# readable in the rows rather than a mystery — preflight and the host guard
# each returned ONE slow run out of six when measured alone, 10.90s against
# five in the low sevens and 2.42s against five in the low 1.4s, and neither
# recurred across the three cold passes. The parts carry two ceilings the whole
# never paid, which is what summing independent maxima does and is why the
# ARITHMETIC paragraph reads the gap as arithmetic rather than as a
# measurement of anything.
#
# SPREAD is what justifies filtering at all: more than three orders of magnitude
# from the fast path to the cold pass, both in the first table. The previous behaviour
# ran the checks after every Bash call, which made `ls` and `git status` pay the
# cold-pass line rather than the fast-path one.
#
# WHERE THE COST IS is one module, and the bound is arithmetic rather than
# approximate: portability, capabilities, agents, secure_fs, preflight and the
# host guard together are at most 31.1s even taking each at its slowest, so of a
# cold pass that never measured below 155.4s, at least 79% is
# tests/test_claude_hooks.py. A hook can only be tested by running it — that
# module spawns bash processes across its 165 tests, more than one per case
# (205 spawns over 105 cases when last counted with a `bash` shim on PATH;
# the RATIO is what carries forward, not the absolute) and holds deliberate
# sleeps — five tests at about 4.5s each. Selecting a subset is therefore the
# whole win: a skill edit skips that module and costs about a seventh of a cold
# pass (21.9-23.4s against 155.4-159.3s), an agent-definition edit roughly a
# hundred-and-fiftieth. When this gets tight the lever
# is that module's own runtime, not this list and not the timeout.
#
# HEADROOM is the cold pass against the timeout registered for this hook in
# .claude/settings.json. That timeout is 480s and the worst observed cold pass
# is 159.3s, so the margin is 3.0x. Plan against the WORST — the run that overruns
# is by definition the slow one. It was 2.2x at the 300s this branch started on,
# having narrowed from 2.5x the sitting before that, and it was RAISED here rather
# than measured again: the floor `test_the_skill_hook_has_headroom_over_its_measured_
# runtime` derives from the selected modules' collected count had reached 289s
# against that 300s — eleven seconds, about thirty more tests anywhere in the six.
# That is the instruction this paragraph used to carry ("the next block of work
# here needs the timeout raised WITH it rather than after that test says so")
# being followed, one round late, by the block of work that spent the margin.
# The seconds did not move: 480 is a BUDGET chosen above this block, not a
# measurement, which is why it may be edited alone when the ratio narrows again.
#
# WHY THE OTHER LEVER WAS NOT PULLED, since the paragraph above names it as the
# right one and then reaches past it. The three sentences after this settle a
# review finding that read the raise as buying a single round; it did not, and
# the correction and the concession both belong here.
#
# What it bought is measurable without a stopwatch, because the floor is a
# function of the COLLECTED count: at 0.35s per collected test, 480s permits
# 1371 collected tests and the seven selected modules now collect 1293, so the
# margin is SEVENTY-EIGHT — about 6% of the current selection, not the two-thirds
# this paragraph claimed. That claim is the shape this block keeps finding in
# itself: a figure true when written, against a smaller selection, left reading
# as current while the selection grew past it. Eighty-seven is a few rounds, not
# many, and the lever below has to be pulled before the next raise rather than
# after it. The thirty-tests figure was true against the 300s the branch started
# on and is what the raise removed.
#
# The concession stands anyway: the runtime was NOT reduced, and the module that
# dominates it cannot be made cheap without giving up what it tests. Its slowest
# tests drive a real hook through real signals — a stamp overlap tied to this
# script's own one-second backdating, subprocess shims that sleep to hold a pass
# open while a concurrent write lands, a TERM delivered mid-pass. Every one of
# those seconds IS the assertion; shortening them buys a faster gate that no
# longer proves the thing it exists to prove, which is the worse trade of the
# two. Parallelism is the honest lever left (the module is nearly all subprocess
# wait), and it needs a dependency this lock does not carry — a change to make
# deliberately, with before/after numbers, not as a footnote to a review round.
#
# It bit once already. At the previous 180s the margin had fallen to 1.7x and
# `test_the_skill_hook_has_headroom_over_its_measured_runtime` went red, because
# the suite's own size implied a 182s floor. Overrunning reports NOTHING: Claude
# Code cancels the hook and the agent sees nothing at all — the same silent mode
# the "hook that never fires" note at the top of this file describes. So the
# cold pass and the registered timeout are re-read TOGETHER whenever either
# moves, and work added here needs the timeout raised with it. The timeout is a
# budget chosen ABOVE this block, never a copy of it.
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
# The COUNTS check is deliberately not among the three, and that is a cost
# decision in the other direction: it goes red the moment a test is ADDED to any
# of the seven and stays red until this block is refreshed in six places, which
# is a hand edit a round can only make once its tests are final. Selected here
# it blocked every tool call in between — the gates-that-cry-wolf pattern, and
# the same judgement the `--deselect` pair below records. See HOOKS_DEPENDENTS.
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
    # ...or the three assertions in it that a test-module edit can reach, when
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
out=$(run_focused_modules)
status=$?

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
#      pricing rather than asserting: while the checks are unrunnable, every
#      following tool call retries them. That retry is not the cold pass —
#      pytest gives up before running a test body, measured over three runs each
#      at 0.23-0.41s for a renamed module (exit 4) and 0.30-0.35s for an import
#      that fails at collection (exit 2), which is the same order as the fast
#      path itself. Sub-second noise until the checks are repaired, against an edit
#      that is otherwise never checked again, is not a close trade.
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
# until the red one clears, which costs seconds and can hide nothing.
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
    # pass; the pass is tens of seconds of wall clock, and `.claude` can be
    # replaced by a symlink pointing outside the workspace at any point in it.
    # Both `mktemp` and `mv` FOLLOW that link, so a pass that validated a real
    # directory would create its candidate and install its stamp under the
    # link's target — a hook write outside the clone, which is the one thing the
    # entry check exists to forbid. Constructed and observed: with this line
    # removed, an external directory substituted mid-pass gains a
    # `.skill-invariants-stamp`; with it, nothing is created there at all.
    # harden-runtime-perms.sh asserts the same three conditions immediately
    # before its own write_stamp for the same reason — the difference is only
    # that its window is milliseconds wide and this one is a minute, which makes
    # the race here the reachable one rather than the theoretical one.
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

[ "$status" -eq 0 ] && exit 0

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
} >&2
exit 2
