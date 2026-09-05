#!/bin/sh
# Run an engine command on the workspace virtualenv's interpreter.
#
# ONE copy of the activation. It was inlined at every call site — 106 of them
# across 22 skills plus three documents — and a 190-character string repeated
# that many times drifts: five sites chained this prefix and then invoked a bare
# `python3`, so on a Windows venv they silently ran the HOST interpreter and the
# runtime directories they were securing stayed world-listable. A wrapper makes
# that mistake unrepresentable rather than merely detectable.
#
# Both layouts, each behind its own `-f` test. `{ . a || . b; }` looks like the
# obvious fallback and is not one: POSIX requires a non-interactive shell to
# ABORT when `.` cannot read its file, so under sh or dash the `||` never runs
# and neither does the command. Testing the file first sidesteps the abort.
# RESET first. `TAXPY` is set below, but it is an ordinary variable and an
# exported one from the caller's environment would otherwise choose the
# interpreter for every engine call in the toolkit — including preflight's
# own `sys.prefix` check, which would then certify exactly what was
# substituted. Verified by DELETING this line, which is the only state in which
# the demonstration works: `TAXPY=/bin/echo sh run.sh X` then printed `X`
# instead of running it. Against the shipped script it reports that Python
# cannot open a file named `X`, which is the point.
TAXPY=

# ANCHORED TO THIS SCRIPT, not to the caller's directory. `.venv` was probed
# relatively, so any engine call issued from a sub-directory — a parallel parse
# sub-task working in `summaries/`, any agent step whose shell starts one level
# down — found no activate script and silently exec'd the HOST interpreter. It
# is silent in both halves: `preflight.check()` resolves `.venv` against its own
# CWD too, so it sees no workspace venv either, returns clean, and certifies the
# host Python it never compared against. The launcher lives at
# `<root>/agents/lib/run.sh`, so the root is two levels up and is knowable
# without asking the caller where they are.
#
# `cd` is deliberately NOT done: the arguments are the caller's, and a script
# path or a `--workspace` relative to their directory would silently change
# meaning. Anchoring the venv probe alone leaves a wrong-directory call failing
# LOUDLY on the file it cannot open, rather than quietly on the wrong Python.
# `|| exit 1`, never `|| ROOT=.`. The fallback restored the CWD-relative probe
# this anchor exists to eliminate: an unreadable ancestor or an oddly-resolved
# `$0` and the launcher was back to finding no `.venv` and exec'ing the HOST
# interpreter, silently. Failing here is the contract — loudly wrong beats
# quietly wrong, which is the same reason no `cd` is done below.
#
# NO PATH IN THE MESSAGE, and `cd`'s own stderr is discarded for the same
# reason. `$0` is an absolute path whenever a skill invokes the launcher by one,
# and the workspace it sits under is named by the person who made it — a folder
# carrying a taxpayer's name, or their SSN, is the shape AGENTS.md §1 restricts
# those directories to prevent (an example is not written out here for the same
# reason), and the shell's own `cd: can't cd to ...` diagnostic discloses it
# just as completely as this echo did. There is no redactor reachable from a
# POSIX shell, so the path is dropped rather than masked. What the reader needs
# is here without it: which script failed, at which step, and what to do.
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." 2>/dev/null && pwd) || {
    echo "run.sh: cannot resolve the toolkit root two levels above this" \
         "script — run it by its path inside the clone" >&2
    exit 1
}

# EXCLUSIVE, not two independent tests. A tree carrying BOTH layouts — a venv
# built under MSYS/Cygwin, which creates `bin/`, in a workspace that later gains
# a native-Windows `Scripts/` one, or any hand-assembled tree — sourced both:
# PATH prepended twice, `VIRTUAL_ENV` left from the second while `deactivate`
# had already been redefined by the first, and `TAXPY=python` set whichever
# layout actually holds the interpreter. `elif` costs nothing and makes the
# states what they are.
# `-r` beside `-f`, and not for symmetry. `-f` is true of an activate script
# this process cannot read, and `.` on such a file makes a non-interactive
# shell abort with ITS OWN diagnostic, which names the file by its full path
# — the workspace path this script is written not to print — and exits 1
# rather than the 2 every skill branches on. Testing readability first
# routes that state to the access branch below, which says what to do
# without saying where.
if [ -f "$ROOT/.venv/bin/activate" ] && [ -r "$ROOT/.venv/bin/activate" ]; then
    . "$ROOT/.venv/bin/activate"
# A stdlib venv on Windows puts `python.exe` in `Scripts/` and NEVER
# `python3.exe` — CPython's `['python', 'python3', 'python3.X']` link loop is
# the POSIX branch only — so activating a Scripts venv and then running
# `python3` finds the host interpreter anyway.
elif [ -f "$ROOT/.venv/Scripts/activate" ] && [ -r "$ROOT/.venv/Scripts/activate" ]; then
    . "$ROOT/.venv/Scripts/activate"
    TAXPY=python
# INACCESSIBLE IS NOT ABSENT, and the tests above cannot tell them apart: `-f`
# answers false for an activate script that exists under a directory this
# process may not search, exactly as it does for one that is not there. With
# no branch between them an intact virtualenv behind a permission problem —
# a `.venv` restored from a backup with its modes lost, a `chmod` that reached
# one directory too many — fell through to the branch below and was told
# `rm -rf .venv`. That is the destructive direction of the conflation
# `preflight._activate_path` already refuses, fixed there and not here; and
# since preflight is reached THROUGH this launcher, its "do not delete it on
# this evidence" verdict was unreachable for the one state it was written for.
# Reported against PR #82.
#
# Each test below is a way the tests above could have answered "absent" for a
# reason other than absence: a directory on the path to the script that exists
# and cannot be searched, or the script itself, present and unreadable. Root
# passes every one of them and so never lands here, which is correct — root
# would have activated.
elif { [ -d "$ROOT/.venv" ] && ! [ -x "$ROOT/.venv" ]; } \
  || { [ -d "$ROOT/.venv/bin" ] && ! [ -x "$ROOT/.venv/bin" ]; } \
  || { [ -d "$ROOT/.venv/Scripts" ] && ! [ -x "$ROOT/.venv/Scripts" ]; } \
  || { [ -f "$ROOT/.venv/bin/activate" ] && ! [ -r "$ROOT/.venv/bin/activate" ]; } \
  || { [ -f "$ROOT/.venv/Scripts/activate" ] && ! [ -r "$ROOT/.venv/Scripts/activate" ]; }; then
    # NO PATH, as below. The remedy is the one preflight's `inspect_remedy`
    # gives, in the launcher's own words, because the launcher is the layer
    # that sees this state first and preflight cannot be reached to give it.
    echo "run.sh: this workspace has a .venv that cannot be inspected — a" \
         "directory on the way to its activate script refuses to be searched," \
         "or the script itself refuses to be read." >&2
    echo "That says nothing about the virtualenv, which may be intact." \
         "Do not delete it on this evidence. From the directory that holds" \
         "agents/, find out which:" >&2
    echo "    ls -ld .venv .venv/bin .venv/bin/activate" >&2
    echo "(a Windows-layout virtualenv keeps them under .venv/Scripts). Fix the" \
         "permission the failing line names, then re-run this command." >&2
    exit 2
# PRESENT BUT UNUSABLE is a THIRD state, and it used to be the silent one.
# `python3 -m venv .venv` on a system whose stdlib venv module is packaged
# separately — Debian and Ubuntu without `python3-venv` — fails inside
# `EnvBuilder._setup_pip`, which runs BEFORE `setup_scripts`, so it leaves a
# `.venv/` directory behind with no activate script in it. That is exactly what
# `tax-setup` Step 6 produces there. With no branch for it this fell through and
# exec'd the HOST `python3`; the engine guard then refused, and the first two
# remedies that refusal prints are `sh agents/lib/run.sh ...` — byte for byte
# the command that produced it. Reported against PR #82.
#
# Refusing HERE ends that loop, because this is the layer that can tell the
# three states apart. Exit 2 is "blocked", the status every skill already
# branches on. The message carries the WHOLE remedy rather than deferring to
# preflight: preflight is reached through this same launcher, so a refusal that
# said "run preflight" would be one more circle.
#
# `-e` does not see a dangling symlink, so `-L` is tested too — a `.venv` link
# whose target is gone is this state, not an absence. This branch is reached
# only once the access branch above has ruled out a permission problem, so
# "no activate script" here is a finding and not a guess.
elif [ -e "$ROOT/.venv" ] || [ -L "$ROOT/.venv" ]; then
    # NO PATH, for the reason the ROOT failure above gives: the workspace
    # directory is named by the person who made it. `.venv` and `agents/` are
    # literals of this toolkit's own, and neither discloses anything.
    echo "run.sh: this workspace has a .venv holding no activate script, in" \
         "either bin/ or Scripts/ — so there is no virtualenv interpreter" \
         "here to run the engine on." >&2
    echo "Falling back to the host interpreter is what this launcher exists to" \
         "prevent: a return computed against dependency versions nothing" \
         "pinned." >&2
    echo "Delete the incomplete environment and rebuild it, from the directory" \
         "that holds agents/:" >&2
    echo "    rm -rf .venv" >&2
    echo "    python3 -m venv .venv" >&2
    echo "    sh agents/lib/run.sh -m pip install -r requirements.txt" >&2
    echo "If that rebuild stops at \"ensurepip is not available\", this system" \
         "packages the standard library's venv module separately — install it" \
         "(Debian/Ubuntu: python3-venv) and rebuild." >&2
    exit 2
fi

# No arguments is never a real call, and it is the one that must not be run.
# `exec python3` with nothing to run starts an interactive REPL, which in an
# agent's non-interactive tool call blocks on a stdin that never closes until the
# call times out — a hang, reported as a timeout, in the launcher every skill now
# goes through. A dropped path variable or a typo is all it takes. AGENTS.md §5
# says an engine entry point run bare either self-tests or states its usage.
if [ "$#" -eq 0 ]; then
    # `-c` belongs in this list: four shipped skill steps use it (tax-setup's
    # dependency probe, tax-fbar's foreign-payer cross-check, tax-parse step
    # 2.5, and tax-compute's sales-tax worksheet). A usage line naming only two
    # of the three forms is what a later hardening pass would validate `$1`
    # against, and it would break all four.
    echo "usage: sh agents/lib/run.sh <script.py | -m module | -c code> [args...]" >&2
    echo "Runs the command on the workspace virtualenv's interpreter." >&2
    exit 2
fi

# `exec`, so the engine's own exit status is this script's. Every skill branches
# on that status, and a wrapper that swallowed it would turn every gate into a
# report about the wrapper.
exec "${TAXPY:-python3}" "$@"
