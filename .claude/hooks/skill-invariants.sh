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
set -uo pipefail
umask 077

# No jq means no way to read the hook payload. A hook that cannot identify its
# target must not guess, and silence beats a broken session.
command -v jq >/dev/null 2>&1 || exit 0

payload=$(cat) || exit 0
tool_name=$(printf '%s' "$payload" \
    | jq -r '.tool_name // empty' 2>/dev/null) || exit 0
file=$(printf '%s' "$payload" \
    | jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0
unset payload

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

# ---------------------------------------------------------------------------
# The watched set, and which of the four modules each watched path can turn red.
#
# DERIVED by reading those four modules, not guessed. A guessed set is wrong in
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
# modules and compares it against the case statement below.
#
# WATCHED_ROOTS is where the Bash branch LOOKS; select_modules_for is what a
# path MEANS. Both are needed and they are separate lists, so a path added to
# one and not the other is half-covered: missing from the roots it is invisible
# to a Bash call, missing from the case it selects nothing when edited directly.
# ---------------------------------------------------------------------------
WATCHED_ROOTS='
.agents/skills
.claude/agents
.claude/hooks
.claude/settings.json
.claude/skills
.gitignore
.pii-allowlist
.vscode/settings.json
AGENTS.md
CLAUDE.md
LICENSE
README.md
agents/install.sh
agents/lib/capabilities.py
agents/lib/rollover.py
agents/lib/tax_law_fetcher.py
agents/templates/taxpayer_profile_template.json
docs/contributing
scripts/pre-commit-pii-scan.sh
tests/skill_text.py
tests/test_capabilities.py
tests/test_claude_agents.py
tests/test_claude_hooks.py
tests/test_portability.py
'

RUN_PORTABILITY=0
RUN_CAPABILITIES=0
RUN_AGENTS=0
RUN_HOOKS=0

# Takes a REPOSITORY-RELATIVE path and turns on the modules it can break. The
# directory roots appear both bare and with a `/*` suffix because find prints
# the root itself when its own timestamp moved, which is what a file being
# added to or deleted from that directory looks like.
select_modules_for() {
    local path=$1

    # A `.` or `..` component makes the textual match meaningless: a payload
    # spelled documents/../.agents/skills/x matches the skills arm while
    # .agents/skills/../../documents/x matches it too and is not a skill at all.
    # Select nothing and let the Bash branch's stamp scan classify it by its
    # real location — find prints the canonical path, so a genuine skill edit
    # spelled this way is caught on the next tool call rather than lost.
    case "/$path/" in
        */../*|*/./*|*//*) return 0 ;;
    esac

    case "$path" in
        .agents/skills|.agents/skills/*|.claude/skills|.claude/skills/*)
            RUN_PORTABILITY=1
            RUN_CAPABILITIES=1
            ;;
        .claude/agents|.claude/agents/*.md)
            RUN_AGENTS=1
            ;;
        .claude/hooks|.claude/hooks/*.sh|.claude/settings.json|\
        tests/test_claude_hooks.py)
            RUN_HOOKS=1
            ;;
        scripts/pre-commit-pii-scan.sh)
            RUN_HOOKS=1
            RUN_PORTABILITY=1
            ;;
        agents/lib/capabilities.py|agents/lib/rollover.py)
            RUN_CAPABILITIES=1
            RUN_AGENTS=1
            ;;
        agents/lib/tax_law_fetcher.py|tests/test_claude_agents.py)
            RUN_AGENTS=1
            ;;
        agents/templates/taxpayer_profile_template.json|\
        tests/test_capabilities.py)
            RUN_CAPABILITIES=1
            ;;
        tests/skill_text.py)
            RUN_PORTABILITY=1
            RUN_CAPABILITIES=1
            ;;
        # .vscode/settings.json is watched as a FILE and .vscode is not a root:
        # everything else under that directory is gitignored editor state, and
        # watching the directory would spend ten seconds on every window-layout
        # write.
        AGENTS.md|CLAUDE.md|README.md|LICENSE|.gitignore|.pii-allowlist|\
        .vscode/settings.json|agents/install.sh|docs/contributing|\
        docs/contributing/*.md|tests/test_portability.py)
            RUN_PORTABILITY=1
            ;;
    esac
}

select_every_module() {
    RUN_PORTABILITY=1
    RUN_CAPABILITIES=1
    RUN_AGENTS=1
    RUN_HOOKS=1
}

nothing_selected() {
    [ "$RUN_PORTABILITY" -eq 0 ] && [ "$RUN_CAPABILITIES" -eq 0 ] \
        && [ "$RUN_AGENTS" -eq 0 ] && [ "$RUN_HOOKS" -eq 0 ]
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
        esac
    done <<< "$KNOWN_RED"
    return 0
}

# The mirror image, run after the verdict: which checks does this pass hand to
# the next one as still-red. Both directions read the same four RUN_* flags,
# which are frozen from apply_red_checks until the process exits — so "what ran"
# and "what is recorded as red" cannot disagree without an edit that moves one of
# them across that line.
remember_red_checks() {
    NEW_RED=''
    [ "$RUN_PORTABILITY" -eq 1 ] && NEW_RED="${NEW_RED}portability"$'\n'
    [ "$RUN_CAPABILITIES" -eq 1 ] && NEW_RED="${NEW_RED}capabilities"$'\n'
    [ "$RUN_AGENTS" -eq 1 ] && NEW_RED="${NEW_RED}agents"$'\n'
    [ "$RUN_HOOKS" -eq 1 ] && NEW_RED="${NEW_RED}hooks"$'\n'
    # Never leak the last test's status: with RUN_HOOKS unset the chain above
    # ends in a false condition, and this function's value would then say
    # "nothing was written" to a caller that never asked about the hooks module.
    return 0
}

# A Write or Edit names its exact file, so the payload is authoritative about
# what THIS call did and the filter can short-circuit before any other work.
# That ordering is the point: without it every unrelated Python edit in the
# repository would pay for a pytest run, and a hook that slow on unrelated work
# gets deleted. A Bash payload is a command string and can rewrite anything, so
# it gets no textual filter at all — the stamp scan below is its filter.
#
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
    select_modules_for "$relative"
    nothing_selected && exit 0
    case "$relative" in
        .agents/skills/*|.claude/skills/*|.claude/agents/*)
            display_target='a repository skill or agent definition' ;;
        *)
            display_target='a watched repository source file' ;;
    esac
fi

# A contributor with no virtualenv gets silence rather than a session that fails
# on every edit. AGENTS.md §7 makes the venv the documented way to run the suite,
# so its absence means the checks are unavailable, not that they failed. This
# check precedes every stamp write below, so an environment that never ran the
# checks never advances the boundary that says they were run.
[ -x .venv/bin/python ] || exit 0

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
# Line 1 is the format version; every line after it names a CHECK — portability,
# capabilities, agents, hooks — that came back red in the pass which wrote the
# stamp (see "what the pass is allowed to remember"). Version 1 carried the
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
# and look exactly like a clean pass. Recognizing the four tokens here means the
# only thing an unreadable stamp can cost is a re-run.
read_red_checks() {
    local line
    while IFS= read -r line; do
        case "$line" in
            portability|capabilities|agents|hooks)
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
    # the red set matters: every branch above forces all four modules anyway.
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
    while IFS= read -r -d '' changed; do
        select_modules_for "$changed"
    done < "$CHANGED_LIST"
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
# prompt rather than busywork, and `tests/` wholesale is roughly twice the cold
# pass for seven times the tests — far too slow to pay on a save (MEASURED COSTS
# below owns the seconds). `test_the_hook_runs_test_files_that_exist` pins that
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
# READ THE RATIOS, NOT THE SECONDS, and the conditions are why. One macOS host,
# 2026-08-10, `.venv/bin/python -m pytest`, every line taken at least three
# times, on a machine that was NOT idle — a second agent session was editing
# this repository and running this suite throughout, which is the honest
# condition for a hook that fires on tool calls rather than a best-case one.
# Run-to-run spread is ±15% on the slow lines, and a second independent
# measurement of `pytest tests/` on this same host returned 139-155s where this
# block returns 163-178s: two careful readings of one command, 25% apart. Every
# line below is therefore a RANGE with a run count, and a figure lifted from
# here to three significant figures would be wrong again by the next reading.
#
#   what triggers it               what runs     observed range    runs
#   ---------------------------------------------------------------------
#   nothing watched changed        no pytest     0.07 - 0.10s        6
#   agent-definition edit           19 tests     0.22 - 0.26s        4
#   skill .md edit                 208 tests       12 - 14s          4
#   cold pass, all four modules    332 tests       83 - 109s         6
#   (for scale) `pytest tests/`   2413 tests      163 - 178s         5
#
# The first line is this hook end to end on its fast path, timed in an isolated
# copy of the repository so that the concurrent editor could not turn it into a
# full pass mid-measurement. The rest are the pytest invocation alone, which is
# all but the entire cost: the hook's own work ahead of pytest is that same
# 0.07-0.10s, so add it to any line below rather than measuring it again.
#
# DECOMPOSITION — each module timed alone, portability carrying the same two
# deselections run_focused_modules passes. This is the durable half: WHICH
# module dominates, and by how much, survives a change of machine.
#
#   tests/test_claude_hooks.py     105 tests       71 - 78s          4
#   tests/test_portability.py       92 tests      8.8 - 10.4s        4
#   tests/test_capabilities.py     116 tests     0.74 - 0.78s        3
#   tests/test_claude_agents.py     19 tests     0.22 - 0.26s        4
#
# ARITHMETIC, stated because a block whose parts do not add up is a block a
# reader stops believing: those four sum to 81s at their minima and 90s at their
# maxima, against 83-109s measured for the four run together. Parts SHOULD sum
# to slightly more than the whole — four interpreter starts and four collections
# instead of one, and collection is 0.05s for all 332 — and at the low end they
# do. At the high end they do not, and the missing term is no term at all: the
# hooks module alone moves 7s between runs, more than every other module in the
# list added together, so parts and total can only ever agree to within the
# noise of the one line that dominates both. A future reading where they
# disagree by more than that module's own spread is a reading where something
# real has changed.
#
# SPREAD is what justifies filtering at all: three orders of magnitude from the
# fast path to the cold pass, both in the first table. The previous behaviour
# ran the checks after every Bash call, which made `ls` and `git status` pay the
# cold-pass line rather than the fast-path one.
#
# WHERE THE COST IS is one module, and the bound is arithmetic rather than
# approximate: portability, capabilities and agents together are at most 11.4s
# even taking each at its slowest, so of a cold pass that never measured below
# 83s, at least 86% is tests/test_claude_hooks.py. A hook can only be tested by
# running it — that module spawns 205 bash processes across its 105 tests
# (counted with a `bash` shim on PATH) and holds deliberate sleeps — five tests
# at about 4.5s each. Selecting a subset is therefore the whole win: a skill edit
# skips that module and costs about a seventh of a cold pass, an
# agent-definition edit about a four-hundredth. When this gets tight the lever
# is that module's own runtime, not this list and not the timeout.
#
# HEADROOM is the cold pass against the 180s registered for this hook in
# .claude/settings.json: 1.6x at the worst observed 109s, 2.2x at the best 83s.
# Plan against the worst — the run that overruns is by definition the slow one,
# and overrunning reports NOTHING, because Claude Code cancels the hook and the
# agent sees nothing at all, the same silent mode the "hook that never fires"
# note at the top of this file describes. 1.6x is not comfortable. Work added
# here needs that timeout raised with it.
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
