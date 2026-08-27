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
# The watched set, and which of the six modules each watched path can turn red.
#
# DERIVED by reading those six modules, not guessed. A guessed set is wrong in
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
#
# These are `find` ROOTS, so a directory here already covers everything beneath
# it: `agents` covers `agents/lib` and `agents/templates/*.json`, `scripts`
# covers the PII scan. Five such entries had accumulated, each listing a path
# an ancestor already reached — `find` then walked and printed them twice, and
# more to the point they read as the thing a new watched file needs, when what
# it actually needs is its arm in `select_modules_for` below. A file belongs
# here only when NO listed directory contains it (`.claude/settings.json`,
# `docs/QUICKSTART.md`, the `tests/*.py` modules — there is no `tests` root).
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
CONTRIBUTING.md
LICENSE
README.md
agents
docs/QUICKSTART.md
docs/contributing
requirements.in
requirements.txt
scripts
tests/skill_text.py
tests/test_capabilities.py
tests/test_claude_agents.py
tests/test_claude_hooks.py
tests/test_portability.py
tests/test_preflight.py
tests/test_secure_fs.py
'

RUN_PORTABILITY=0
RUN_CAPABILITIES=0
RUN_AGENTS=0
RUN_HOOKS=0
RUN_PREFLIGHT=0
RUN_SECUREFS=0

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
            # test_secure_fs pins ~40 exact `secure_fs` command strings
            # inside SKILL.md and its references. Changing the invocation
            # form broke twenty of them in one commit while the hook,
            # running six modules on a skill edit, selected none.
            RUN_SECUREFS=1
            # A SKILL.md edit can drop the engine-preflight gate heading, its
            # fail-closed sentence, or the chained activation prefix on
            # an engine invocation — the three things test_preflight.py asserts
            # per skill, and all three are one deletion away.
            RUN_PREFLIGHT=1
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
            # test_secure_fs parses this script's PII_PATTERNS array to pin
            # redactor parity, and THAT failure is a disclosure channel: a
            # family detected here and not redacted there means the gate blocks
            # a path holding a credential and prints the credential.
            RUN_SECUREFS=1
            ;;
        # These three carry EXTRA selections on top of what any engine module
        # gets. `case` is first-match-wins, so they have to repeat EVERY
        # selection the `agents/lib/*.py` arm makes rather than fall through to
        # it — without that, EDITING one of them skipped a module that DELETING
        # it did not, which is the wrong way round. It has now been the wrong
        # way round twice: RUN_PORTABILITY was the first omission, and
        # RUN_SECUREFS was still missing after that fix, on the three files
        # where it matters most. test_secure_fs sweeps `agents/**/*.py` for a
        # network import and for a raw path reaching a message, so a fetcher
        # that grew an outbound call, or a redaction dropped from a message,
        # turned it red while this hook ran five modules, passed them all, and
        # exited 0 silent.
        agents/lib/capabilities.py|agents/lib/rollover.py)
            RUN_CAPABILITIES=1
            RUN_AGENTS=1
            RUN_PREFLIGHT=1
            RUN_PORTABILITY=1
            RUN_SECUREFS=1
            ;;
        agents/lib/tax_law_fetcher.py)
            RUN_AGENTS=1
            RUN_PREFLIGHT=1
            RUN_PORTABILITY=1
            RUN_SECUREFS=1
            ;;
        tests/test_claude_agents.py)
            RUN_AGENTS=1
            ;;
        # The directory AND every module under it. The roster test is only half
        # the reason and was, on its own, the wrong half: it pins the roster
        # against `agents/lib/*.py`, so only an ADD or REMOVE turns it red, and
        # an addition shows up as the directory's own timestamp moving. Reasoning
        # from that alone, this arm was once narrowed to the directory — and left
        # a hole, because `preflight.check()` IMPORTS all twenty-one modules and
        # executes one of them. A SyntaxError in `projection.py` turns
        # test_preflight red while the hook, selecting nothing, exits 0 silent.
        # Verified by breaking that module. An engine edit therefore costs the
        # preflight module (MEASURED COSTS: between capabilities and
        # portability), and that is the cheaper half of the trade.
        # The bare directory is what a DELETE looks like — a removal surfaces
        # only as the directory's own ctime moving, never as a path beneath it —
        # so it selects every module that imports anything in here, not just the
        # roster check. Deleting capabilities.py or tax_law_fetcher.py fails
        # test_capabilities.py and test_claude_agents.py at COLLECTION, and the
        # per-file arms above cover an edit to those files, not their removal.
        agents/lib/secure_fs.py)
            # Every per-file arm above imports it — rollover, capabilities
            # and tax_law_fetcher all do — so it earns their selections
            # too. The header makes this import-edge argument for
            # rollover.py and stopped one edge short.
            RUN_SECUREFS=1
            RUN_CAPABILITIES=1
            RUN_AGENTS=1
            RUN_PREFLIGHT=1
            RUN_PORTABILITY=1
            ;;
        # test_secure_fs sweeps these two WHOLE — `Path(REPO_ROOT, "agents")`
        # and `Path(REPO_ROOT, "scripts")`, every `*.py` and `*.sh` beneath —
        # for a network import, a bare `chmod`, and a raw path reaching a
        # message. Anything ADDED under either is covered by that sweep the
        # moment it lands, so a file outside `agents/lib/` (a new
        # `agents/tools/*.py`, a second script) turned it red with no local
        # signal at all. The more specific arms above still win for the paths
        # they name; these are the catch-alls the sweep implies.
        agents|scripts)
            RUN_SECUREFS=1
            ;;
        agents/lib)
            RUN_PREFLIGHT=1
            RUN_CAPABILITIES=1
            RUN_AGENTS=1
            # ...and secure_fs, which this arm's own comment claims to cover:
            # test_secure_fs imports the module at file scope, so DELETING it
            # fails that module at COLLECTION.
            RUN_SECUREFS=1
            # And portability: TestSelfTestClaim subprocess-executes four of
            # these modules, so DELETING one turns it red exactly as editing one
            # does. The arm below adds it for the edit; this arm is the one a
            # removal reaches, and it was the half that omitted it.
            RUN_PORTABILITY=1
            ;;
        # test_preflight pins RUNTIME_DEPENDENCIES against this file, so a
        # fifth direct dependency added here and not there leaves the gate
        # reporting "pinned dependencies importable" about four of five.
        # The LOCK is watched for the same reason one step further out: the gate
        # now compares installed versions against it, and test_preflight reads
        # it to derive what a pin says. A Dependabot bump that drops a pin
        # turns that module red, and this is the arm that says so locally.
        requirements.in|requirements.txt)
            RUN_PREFLIGHT=1
            ;;
        # run.sh is what every skill actually invokes, so it is engine code by
        # any measure the rest of this arm uses.
        agents/lib/*.sh)
            RUN_PREFLIGHT=1
            RUN_PORTABILITY=1
            # test_secure_fs bans a bare `chmod` and a shell fetcher anywhere
            # under agents/ and scripts/.
            RUN_SECUREFS=1
            ;;
        agents/lib/*.py)
            RUN_PREFLIGHT=1
            # test_secure_fs audits EVERY module here for a network import and
            # for a raw path reaching a message.
            RUN_SECUREFS=1
            # test_portability EXECUTES four of these modules and asserts they
            # exit 0 (TestSelfTestClaim), so an engine edit that breaks a
            # built-in self-test turns portability red — and selecting preflight
            # alone left that silent, since the module still imports fine.
            RUN_PORTABILITY=1
            ;;
        tests/test_preflight.py)
            RUN_PREFLIGHT=1
            ;;
        tests/test_secure_fs.py)
            RUN_SECUREFS=1
            ;;
        agents/templates/taxpayer_profile_template.json|\
        tests/test_capabilities.py)
            RUN_CAPABILITIES=1
            ;;
        # test_portability builds a prior-year return from result_template to
        # prove raw line 24 is ignored, then checks the explicit completed
        # Form 2210 base exists in projection_inputs_template. A template edit
        # must therefore select the module that pins both halves of the intake.
        agents/templates/result_template.json|\
        agents/templates/projection_inputs_template.json)
            RUN_PORTABILITY=1
            ;;
        tests/skill_text.py)
            RUN_PORTABILITY=1
            RUN_CAPABILITIES=1
            # test_preflight.py derives its whole skill roster through this.
            RUN_PREFLIGHT=1
            ;;
        # .vscode/settings.json is watched as a FILE and .vscode is not a root:
        # everything else under that directory is gitignored editor state, and
        # watching the directory would spend ten seconds on every window-layout
        # write.
        # README.md, CONTRIBUTING.md and docs/QUICKSTART.md carry the clone URL
        # that preflight's remedy repeats, and test_preflight compares the four
        # against each other — a rename in any of them leaves the ONE copy that
        # is a stranded reader's only escape route pointing at a 404.
        # agents-compat.md and QUICKSTART both carry forms test_preflight now
        # asserts against — the activation prefix and the clone URL. The
        # coverage test cannot see either: it recovers `REPO / "literal"`
        # chains, and both reads are written over a loop.
        docs/QUICKSTART.md|docs/contributing/agents-compat.md)
            RUN_PREFLIGHT=1
            RUN_PORTABILITY=1
            ;;
        CONTRIBUTING.md|README.md)
            RUN_PORTABILITY=1
            RUN_PREFLIGHT=1
            ;;
        # install.sh is under `agents/`, which test_secure_fs sweeps for a bare
        # `chmod` and a shell fetcher.
        agents/install.sh)
            RUN_PORTABILITY=1
            RUN_SECUREFS=1
            ;;
        AGENTS.md|CLAUDE.md|LICENSE|.gitignore|\
        .pii-allowlist|.vscode/settings.json|\
        docs/contributing|docs/contributing/*.md|tests/test_portability.py)
            RUN_PORTABILITY=1
            ;;
        # LAST, and this is the half the `agents|scripts` arm above was missing.
        # That arm matches those two words EXACTLY — a `case` pattern is not a
        # prefix — so it fires only when find prints the ROOT itself, which
        # happens when a file is added directly beneath it. A path one level
        # further down (`agents/tools/check.py`, `scripts/helpers/check.sh`)
        # matched no arm at all, and neither branch has a fallback: the
        # Write/Edit branch exits 0 on an empty selection, and the Bash branch's
        # scan hands each printed path to this same function. So the sweep the
        # arm above documents — every `*.py` and `*.sh` under `agents/` and
        # `scripts/` — was enforced for the two subtrees that happen to be named
        # by an arm and silently unenforced everywhere else beneath them.
        #
        # `*` in a `case` pattern spans `/`, so these reach any depth. Scoped to
        # the two EXTENSIONS the sweep actually reads rather than a bare
        # `agents/*`: an engine run rewrites `agents/lib/__pycache__/*.pyc`,
        # whose ctime then beats the stamp on the next Bash call, and a bare
        # pattern would buy a pytest module for every `ls` that followed one.
        agents/*.py|agents/*.sh|scripts/*.py|scripts/*.sh)
            RUN_SECUREFS=1
            ;;
    esac
}

select_every_module() {
    RUN_PORTABILITY=1
    RUN_CAPABILITIES=1
    RUN_AGENTS=1
    RUN_HOOKS=1
    RUN_PREFLIGHT=1
    RUN_SECUREFS=1
}

nothing_selected() {
    [ "$RUN_PORTABILITY" -eq 0 ] && [ "$RUN_CAPABILITIES" -eq 0 ] \
        && [ "$RUN_AGENTS" -eq 0 ] && [ "$RUN_HOOKS" -eq 0 ] \
        && [ "$RUN_PREFLIGHT" -eq 0 ] && [ "$RUN_SECUREFS" -eq 0 ]
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
            preflight) RUN_PREFLIGHT=1 ;;
            securefs) RUN_SECUREFS=1 ;;
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
    [ "$RUN_PREFLIGHT" -eq 1 ] && NEW_RED="${NEW_RED}preflight"$'\n'
    [ "$RUN_SECUREFS" -eq 1 ] && NEW_RED="${NEW_RED}securefs"$'\n'
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
            portability|capabilities|agents|hooks|preflight|securefs)
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
    # the red set matters: every branch above forces all six modules anyway.
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
# RE-MEASURED END TO END, every row in ONE sitting, after `tax-safe-harbor`'s
# fourth review round added twenty-four tests — seven to the portability module
# this hook selects, seventeen to modules it does not. The sitting ran as several
# consecutive invocations with NOTHING touching the repository between them, the
# ten-minute cap on a tool call being what splits them; that is a continuation of
# one sitting and not the patch this paragraph forbids. An earlier set taken
# during the same round WAS discarded rather than used, for the reason the
# paragraph exists: source files were still being edited while it ran, so its
# rows described a tree that no longer exists. That patch is what the revision
# before THAT was: count columns edited and seconds carried over, going wrong in
# BOTH directions at once, every count landing short (100 where 116 run, 288
# where 289 run, 2936 where 2956 collect) while its module rows summed to about
# 136s against a cold pass it stated as 121-124s. Parts exceeding the whole was
# its signature; this sitting has them exceeding by about 6% for an entirely
# different reason, and the ARITHMETIC paragraph below separates the two rather
# than leaving a reader to guess which they are looking at. So: no row here is
# edited alone. Either the whole block is re-measured in one sitting or it is
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
# (`touch -A -000010`) and all six runs are fast.
#
# READ THE RATIOS, NOT THE SECONDS, and the conditions are why. One macOS host,
# `.venv/bin/python -m pytest`, every line taken three times, back to back, with
# nothing else running against the repository — an earlier attempt at this same
# sweep was ABANDONED mid-flight because edits were landing during it and firing
# the hook's own pytest underneath the measurement. Run-to-run spread is ±5% on
# the slow lines here, and the spread BETWEEN sittings is far larger: readings of
# `pytest tests/` on this same host have returned 139-155s, 175-209s, 179-191s,
# 184-190s and 199-204s. The portability module moved further — 7.5-10.2s one day,
# 12.7-13.4s the next, with no code between the two — which is why the ratios below are
# stated as "roughly" and why a figure lifted from here to three significant
# figures would be wrong by the next reading. Every line is a RANGE with a run
# count.
#
# SECONDS ARE WALL CLOCK of the invocation — interpreter start and collection
# included — because that is what the tool call waits on. pytest's own "in
# 0.39s" footer is the smaller number and reading one convention as the other is
# how a row ends up looking halved. The fixed overhead is under 0.2s on this
# host, which is the whole of the agents row and a rounding error on the hooks
# module.
#
#   what triggers it               what runs     observed range    runs
#   ---------------------------------------------------------------------
#   nothing watched changed        no pytest    0.04 - 0.05s         6
#   agent-definition edit           19 tests      0.2 - 0.2s         3
#   skill .md edit                 720 tests     16.4 - 17.2s        3
#   cold pass, all six modules     862 tests    121.8 - 138.9s       3
#   (for scale) `pytest tests/`   3221 tests    190.5 - 209.6s       2
#
# COUNTS ARE WHAT RUNS, not what collects. Two places bite: `run_focused_modules`
# passes two `--deselect` flags, so portability collects 179 and runs 177; and
# the hooks module holds two platform skips, so it collects 125 and runs 123.
# `pytest tests/` collects 3223 and runs 3221 for the same skips — and that row
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
# DECOMPOSITION — each module timed alone, portability carrying the same two
# deselections run_focused_modules passes. This is the durable half: WHICH
# module dominates, and by how much, survives a change of machine.
#
#   tests/test_claude_hooks.py     123 tests    108.8 - 129.2s       4
#   tests/test_portability.py      177 tests     10.3 - 11.6s        3
#   tests/test_capabilities.py     116 tests      0.6 - 0.8s         3
#   tests/test_claude_agents.py     19 tests      0.2 - 0.2s         3
#   tests/test_secure_fs.py        138 tests      1.1 - 1.3s         3
#   tests/test_preflight.py        289 tests      3.9 - 4.2s         3
#
# The agent-definition row above and the agents row here are the SAME
# invocation — that trigger selects that module alone — and are stated with the
# same range on purpose. Two figures for one measurement is how this block
# started drifting from itself the first time.
#
# Read the preflight line as a RATIO, which is the half that survives a change
# of machine: it sits between capabilities and portability, and is somewhere
# between a twenty-sixth and a thirty-third of the hooks module that dominates every
# total here — a spread that wide on a ratio is itself the reason to distrust any
# single reading of the seconds it comes from. A skill edit
# selects it alongside portability, capabilities and secure_fs, which is where
# its cost actually lands. It has grown against its neighbours three times: the
# launcher tests EXECUTE `run.sh` in a subprocess rather than asserting against
# its text — the trade that bought a test able to catch a hang — and the
# installed-versus-locked comparison added a class that reads the lock and
# fabricates distributions, then a second one for the pins that are ABSENT from
# an environment rather than merely the wrong version. Both of those classes are
# tmp_path and fabrication, which is why the row moved much less than its count.
#
# ARITHMETIC, stated because a block whose parts do not add up is a block a
# reader stops believing. Those six sum to 124.9s at their minima and 147.3s at
# their maxima, against 121.8-138.9s measured for the six run together — so this
# sitting the parts do NOT bracket the whole, they sit above it by about 6%. That
# 6% is the hooks module's own spread and nothing else: it read 108.8-129.2s
# across four back-to-back runs, a 20-second swing, wider than every other module
# added together, and the paragraph below states why parts and whole can only ever
# agree to within it. Six separate invocations each paying an interpreter start
# pushes the same way, and is worth well under a second of it. What separates this
# from the count-column patch the header forbids is the other half, which is
# EXACT: 177 + 116 + 289 + 138 = 720 for a skill edit, plus 123 + 19 = 862 for a
# cold pass, both matching the trigger table above to the test. That sentence
# itself sat stale for several rounds at "160 + ... = 703 ... 845" — a typo'd
# part and a superseded sum, green the whole time because the mechanical check
# reads the TABLE and this line is prose. It moves with the table now. A decomposition
# error shows up there first, because counts do not drift — and it now shows up
# MECHANICALLY: `test_the_measured_costs_counts_match_live_collection` collects
# each module and re-derives both sums, so a stale count column is a red suite
# rather than a figure a reader has to distrust on their own. What neither half is
# evidence about, in either direction, is collection overhead — a previous
# revision claimed that and had the parts exceeding the whole because a count
# column had been hand-patched.
#
# EVERY SECOND above was measured together, most recently 2026-08-18, on an idle
# machine, and re-measured END TO END rather than patched where a count moved:
# twenty-four tests were added across the suite that round, seven of them to a
# module this hook selects, and patching one row over stale neighbours is what left the
# derived rows wrong three revisions ago. Against the previous end-to-end sitting
# — the day before, the same host — the picture is mixed rather than uniform, which
# is itself the point: the hooks module WIDENED hard (108.8-129.2s against
# 113-120s, straddling it in both directions), portability came down (10.3-11.6s
# against 11.2-11.4s) while gaining seven tests, the cold pass came down and
# widened (121.8-138.9s against 123-132s), and `pytest tests/`
# (190.5-209.6s against 199-204s) came down at the BOTTOM and went up at the top,
# widening in both directions. Twenty-four tests worth well
# under a second cannot produce any of that, and no single reading of it should be
# read as a trend. It is the between-sitting spread recorded above, on a host idle
# for both: do NOT read a cause into it, and do read the ratios, not the seconds.
#
# COUNTS ARE TWO HUNDRED AND ELEVEN TESTS AHEAD OF THE SECONDS, and this is the
# note that says so. Every COUNT above is current as of the review round that
# fixed this block; every SECOND above is still the sitting recorded below,
# unmoved. That is the order this block prescribes for itself: counts are
# derivable in a second and go first, seconds cost half an hour of an
# otherwise-idle machine and are re-read as a SET when the branch settles.
# Against the counts the seconds were taken at, the suite has gained two hundred
# and eleven tests (3010 to 3221) and the portability row thirty-eight (139 to
# 177), with secure_fs moved once as well (133 to 138). The seven rounds BEFORE the
# last one each moved the scale row ALONE, by four, then three, then three, then five,
# then four, then three, then one: they added tests to projection,
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
# Which case a round
# is, is MEASURED by
# `test_the_measured_costs_counts_match_live_collection`, never assumed. Two hundred and eleven tests are worth well under a second and
# cannot move a row that spans 17 seconds at its narrowest, so the ranges
# stand — but a reader deriving anything from the gap should know it is there
# rather than infer that the sitting covered it.
#
# THE HAND-REFRESH FAILED AGAIN, which is why the counts are no longer kept by
# hand. The revision that made this block the SOLE owner of these figures — it
# deleted the count from docs/contributing/test-invariants.md in the same
# change — shipped a portability row five tests light and a scale row twenty,
# under the very paragraph below asserting every count had been re-derived by
# measurement. Four hand-refreshes, four drifts. So the rule this block states
# is now ENFORCED: `test_the_measured_costs_counts_match_live_collection` in
# tests/test_claude_hooks.py collects each of the six modules and the whole
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
# The hooks module dominates both the parts and the total — it alone swings
# several seconds between runs, more than every other module added together — so
# parts and whole can only ever agree to within ITS noise. A future reading where
# they disagree by more than that is a reading where something real changed.
#
# SPREAD is what justifies filtering at all: three and a half orders of magnitude
# from the fast path to the cold pass, both in the first table. The previous behaviour
# ran the checks after every Bash call, which made `ls` and `git status` pay the
# cold-pass line rather than the fast-path one.
#
# WHERE THE COST IS is one module, and the bound is arithmetic rather than
# approximate: portability, capabilities, agents, secure_fs and preflight
# together are at most 19s even taking each at its slowest, so of a cold
# pass that never measured below 121.8s, at least 84% is
# tests/test_claude_hooks.py. A hook can only be tested by running it — that
# module spawns bash processes across its 123 tests, more than one per case
# (205 spawns over 105 cases when last counted with a `bash` shim on PATH;
# the RATIO is what carries forward, not the absolute) and holds deliberate
# sleeps — five tests at about 4.5s each. Selecting a subset is therefore the
# whole win: a skill edit skips that module and costs between a seventh and an
# eighth of a cold pass (16.4-17.2s against 121.8-138.9s), an
# agent-definition edit roughly a six-hundredth. When this gets tight the lever
# is that module's own runtime, not this list and not the timeout.
#
# HEADROOM is the cold pass against the timeout registered for this hook in
# .claude/settings.json. That timeout is 480s and the worst observed cold pass
# is 138.9s, so the margin is 3.5x. Plan against the WORST — the run that overruns
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
# function of the COLLECTED count: at 0.35s per collected test the gate re-fires
# only after roughly another two-thirds of the current selection is added — call
# it many rounds, not one. The thirty-tests figure was true against the 300s the
# branch started on and is what the raise removed.
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
# KNOWN GAP, the same trade from the other direction: test_claude_hooks.py READS
# test_preflight.py and test_secure_fs.py — its watched-set coverage test opens
# all six module files and recovers every `REPO / "literal"` path they name — so
# adding such a literal to either turns the hooks module red while the arms
# above select only that module's own tests. By the import-edge argument used
# for rollover.py, those two arms should therefore also carry RUN_HOOKS. They
# deliberately do not: the hooks module is at least 84% of a cold pass
# (MEASURED COSTS), and both files are edited constantly, so that edge would put the most
# expensive module on the most frequent edit — the fast path this hook exists to
# protect, spent on a signal CI produces two minutes later anyway. A gate that
# slow on routine work gets switched off, and a switched-off gate catches
# nothing at all. The lever, when this matters, is that module's runtime.
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
    [ "$RUN_PREFLIGHT" -eq 1 ] \
        && modules[${#modules[@]}]=tests/test_preflight.py
    [ "$RUN_SECUREFS" -eq 1 ] \
        && modules[${#modules[@]}]=tests/test_secure_fs.py
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
