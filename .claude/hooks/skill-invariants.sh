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

    # COMPILED BYTECODE IS NEVER SOURCE, and saying so once here is what lets
    # the arms below be written as what they mean. Every engine run rewrites
    # `__pycache__/*.pyc`, whose ctime then beats the stamp on the next Bash
    # call — so the arms that wanted to say "anything under this directory"
    # each said "anything with these two extensions" instead, and a rule
    # phrased around the thing it is dodging stops covering what it was for:
    # a package DIRECTORY has no extension and matched none of them.
    #
    # Only the ENGINE arm was widened on the strength of this. The `agents/`,
    # `scripts/` and `tests/` arms keep their `*.py`/`*.sh` scoping, and it is
    # not a leftover: each mirrors a sweep that parses exactly those
    # extensions, so the scoping is faithful to what can actually turn the
    # module red rather than a workaround for bytecode.
    case "/$path/" in
        */__pycache__/*) return 0 ;;
    esac

    # A PACKAGE DIRECTORY reads as its own `__init__.py`.
    #
    # `find` prints the bare directory when a file is ADDED to or DELETED from
    # it, and that string matches no arm below: `agents/lib/*.py` reaches a
    # module at any depth — a `case` glob's `*` spans `/` — but never the
    # directory. Every package therefore carried a hand-written arm repeating
    # the generic selections, W2 adding one per slice, and the one that gets
    # forgotten selects NOTHING while looking exactly like a clean path.
    #
    # `__pycache__` is excluded EXPLICITLY, and that is the whole reason this
    # is a normalisation rather than a wider glob. Widening the arm to a bare
    # `agents/lib/*` was the other candidate and is wrong: it swallows
    # `agents/lib/__pycache__/*.pyc`, which every engine run rewrites, so the
    # next tool call would run a pytest module on the strength of compiled
    # bytecode nothing reads. A hook test caught that within the minute, and
    # naming the one directory is what the second attempt costs.
    #
    # The rewrite is NOT conditional on the initialiser existing. A package
    # that has just lost its `__init__.py` is precisely the change worth
    # selecting on — preflight and the host guard both go red for it — and a
    # rule that declined there would go quiet at the one moment it matters.
    #
    # It lands on a path the arms ALREADY handle, so it adds no selections of
    # its own: `projection` becomes `projection/__init__.py` and falls to the
    # generic engine arm, `tax_computations` becomes
    # `tax_computations/__init__.py`, which its own arm still wins first and
    # still adds the agents module to, and `tax_computations/state` lands under
    # that same arm. No package needs an entry anywhere again.
    #
    # `agents/lib` ITSELF is excluded, and that is the whole reason this is
    # scoped to paths strictly beneath it. The engine root holds an
    # `__init__.py` too, but its arm means "a module was added to or removed
    # from the engine" and names two modules the generic arm does not; folding
    # it into its own initialiser would silently drop them.
    # Matched on `"/$path/"`, like the two guards above and NOT like the arms
    # below. Two reasons, and the first is mechanical: the selector's own
    # `case` carries an `agents/lib/*` arm, and a rule written the same way
    # here gives one function two arms spelled identically — which is how
    # `test_a_per_file_arm_never_selects_less_than_the_generic_one` came to
    # read this block's body as the generic arm's and measure zero flags.
    #
    # The second is that the delimited form says what is meant. `agents/lib`
    # ITSELF must not be rewritten — the engine root's arm means "a module was
    # added to or removed from the engine" and names two modules the generic
    # arm does not — and `/agents/lib/*/` already excludes it, because `*/`
    # cannot match an empty remainder. An explicit arm for the root would be
    # dead code; the test that the root keeps its own selections is what says
    # this still holds.
    case "/$path/" in
        # A dot beneath `agents/lib/` means a FILE's name, and a file keeps
        # whatever arm it already had. Without this a module replaced by a
        # directory of the same name — `capabilities.py/` — would normalise
        # into the generic arm and lose the two modules only its own arm
        # names. A Python package never has a dot in a path segment, so
        # nothing this rule is for is excluded by it.
        /agents/lib/*.*/) ;;
        /agents/lib/*/)
            [ -d "$path" ] && path="$path/__init__.py"
            ;;
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
            # V6: test_claude_agents reads the skill tree to check that every
            # skill handing an untrusted file to somebody names the enforced
            # parse worker AND carries the "boundary NOT enforced" banner for the
            # sequential path. An agent nothing dispatches is not a boundary, and
            # deleting the sentence that says so is a skill edit.
            #
            # This was scoped to tax-parse alone for one revision, when it was the
            # only hardened skill. Sweeping the siblings made that wrong rather
            # than merely narrow: the module now reads three skills, and a
            # per-skill list here would be a fourth place to remember them.
            RUN_AGENTS=1
            ;;
        .claude/agents|.claude/agents/*.md)
            RUN_AGENTS=1
            ;;
        docs/ops/review-subagent-acceptance.md)
            # An ASSERTED artifact, not prose, and asserted by TWO modules:
            # test_claude_agents requires it to exist and to name every agent in
            # EXPECTED_AGENTS, and test_preflight's DOCUMENTS tuple covers it
            # for the launcher rule. Folding this into the agents arm above was
            # the first attempt and it made an agent-definition edit pay for the
            # preflight module, which two tests caught by pinning that edit's
            # selection to AGENTS alone — a good reminder that these arms are
            # per-PATH, never per-directory-that-feels-related.
            RUN_AGENTS=1
            RUN_PREFLIGHT=1
            # ...and portability since Y5, which names this file as the one
            # `docs/ops/` pointer either instruction file carries — the floor
            # that keeps its pointer sweep from reporting a clean pass over a
            # regex that has stopped matching half its subject.
            RUN_PORTABILITY=1
            ;;
        docs/contributing/residency-guardrails.md)
            # The residency figures' ONE home. test_capabilities reads its table
            # to check every registered figure carries a source URL and a date
            # it was read — AGENTS.md §2's whole contract is that a stale figure
            # never passes as fresh, and a figure with no source is one nobody
            # can re-verify at season open.
            RUN_CAPABILITIES=1
            ;;
        docs/contributing/parse-boundary.md)
            # The parse boundary's ONE home — which mode, its cost, the fence
            # marker, the banner. test_claude_agents reads it to check that the
            # banner and the per-dispatch marker are stated verbatim, since both
            # are literals an agent copies and the whole distinction between an
            # enforced parse and an unenforced one.
            RUN_AGENTS=1
            ;;
        docs/ops/season-open-runbook.md)
            # test_preflight's DOCUMENTS again. It was in NEITHER list before —
            # a documented gap. Adding `docs/ops` to WATCHED_ROOTS without this
            # arm was the half-covered shape the header warns about, in its
            # worse direction: visible to a Bash call and selecting nothing,
            # which reads as covered and is not.
            RUN_PREFLIGHT=1
            # ...and the agents module, which reads it as the ASSERTED other end
            # of a pointer. The acceptance record names this runbook as its first
            # re-run trigger, so the runbook has to name the acceptance back —
            # a pointer that resolves in one direction is how the re-run came to
            # be documented in every file except the one a maintainer follows at
            # season open.
            RUN_AGENTS=1
            # ...and portability, which since Y5 requires every file under
            # `docs/ops/` to appear in AGENTS.md §9's index.
            RUN_PORTABILITY=1
            ;;
        docs/ops)
            # The bare directory is what a DELETE looks like — or an ADD, which
            # is portability's case: an unindexed new file here is the failure
            # its index test exists for. All three modules assert about what is
            # under this directory.
            RUN_PREFLIGHT=1
            RUN_AGENTS=1
            RUN_PORTABILITY=1
            ;;
        .claude/hooks|.claude/hooks/*|.claude/settings.json)
            RUN_HOOKS=1
            ;;
        # SPLIT from the arm above the moment `tests/*.py` became source behind
        # an assertion. This module is swept by
        # `test_host_guard.py::TestNoTestSpawnsABareInterpreter` like every
        # other one; the three `.claude/` paths beside it are not Python and are
        # not swept, so sharing an arm would have bought the host-guard module
        # on every hook and settings edit for a dependency it does not have —
        # the same trade the template arm below records.
        tests/test_claude_hooks.py)
            RUN_HOOKS=1
            RUN_HOSTGUARD=1
            ;;
        # EVERY one of the six OTHER test modules also selects the hooks CHECK,
        # and the arms below each say so. test_claude_hooks re-derives the read
        # set by PARSING all seven, itself included — that is what keeps the
        # hand-written mapping above honest — so a path literal added to any of them can turn it red while
        # the module the contributor edited stays green. That is the CI-only
        # failure this hook exists to convert into a local one, and the mapping's
        # own comment already warns the transcription goes stale.
        #
        # Six of them select `RUN_HOOKSDEPS` and not `RUN_HOOKS`, which is the
        # difference between the three assertions that reach them and the module
        # that dominates every total in MEASURED COSTS. The paragraph that stood
        # here argued for the whole module on the ground that "a partial run
        # reported as a module's verdict is how a check comes to mean less than
        # it says" — true, and about a different thing: that sentence is about
        # reporting a SUBSET as the hooks module's verdict, and this is a subset
        # reported as its own check, carried forward under its own name. What
        # the trade costs on BOTH sides — the module spared and the three ids
        # that replaced it, which are not free — is stated once, in MEASURED
        # COSTS under THE TRIGGER WITH NO ROW. It is not restated here: this
        # comment quoted the spared module's ratio as though it settled the
        # subset's cost, which is the two-copies-of-one-quantity failure that
        # block exists to end.
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
            # ...and the host guard, which the generic arm below selects for
            # every engine module: `TestEveryModuleIsCovered` sweeps this
            # directory for the package import that puts a module behind the
            # refusal. Missing from all five per-file arms at first, for the
            # same first-match-wins reason the two flags above it were.
            RUN_HOSTGUARD=1
            ;;
        agents/lib/tax_law_fetcher.py)
            RUN_AGENTS=1
            RUN_PREFLIGHT=1
            RUN_PORTABILITY=1
            RUN_SECUREFS=1
            # ...and the host guard, which the generic arm below selects for
            # every engine module: `TestEveryModuleIsCovered` sweeps this
            # directory for the package import that puts a module behind the
            # refusal. Missing from all five per-file arms at first, for the
            # same first-match-wins reason the two flags above it were.
            RUN_HOSTGUARD=1
            ;;
        # ptet.py is the SECOND engine module the agents test reads, and it got
        # there without an arm: the acceptance guards derive the planted SALT cap
        # from `_salt_cap`, so the OBBBA window in that file decides whether the
        # fixture is corrupt at all. Editing the window is precisely the change
        # that turns those guards red, and the generic `agents/lib/*.py` arm
        # below sets everything EXCEPT agents — so the one edit boundary they
        # exist to watch was the one the hook did not select them for, leaving it
        # to CI. Half-covered is the failure mode this whole block is built
        # around: a path in the roots but not in the selector looks identical to
        # a path that is genuinely clean.
        agents/lib/ptet.py)
            RUN_AGENTS=1
            RUN_PREFLIGHT=1
            RUN_PORTABILITY=1
            RUN_SECUREFS=1
            # ...and the host guard, which the generic arm below selects for
            # every engine module: `TestEveryModuleIsCovered` sweeps this
            # directory for the package import that puts a module behind the
            # refusal. Missing from all five per-file arms at first, for the
            # same first-match-wins reason the two flags above it were.
            RUN_HOSTGUARD=1
            ;;
        # ...and the THIRD, arriving by a different edge and missed for the
        # same reason. The two above are read for their DATA — a schema key, an
        # OBBBA window — and both were noticed while writing a test that names
        # the file. This one is read for its BEHAVIOUR: the Replay A guard
        # imports `tax_computations` and asserts what the shipped
        # `compute_qbi_deduction` returns on the record's own case, so a change
        # to the Form 8995 cap turns test_claude_agents red. An import edge is
        # invisible to `test_the_watched_set_still_covers_what_the_modules_read`
        # — that test recovers `REPO / "..."` literals, and there is no path
        # literal here to recover — which is exactly why the hand list this arm
        # belongs to still exists, and exactly why a new edge slips past it.
        # W2 made this a PACKAGE, and there is no `tax_computations.py` left
        # to name. One glob covers it: a `case` pattern's `*` spans `/`, so
        # `state/ny.py` and `__init__.py` match the same arm, and the bare
        # directory `find` prints on an add or a delete is normalised into
        # `tax_computations/__init__.py` before this `case` runs — which is why
        # the alternative that used to name it here is gone rather than merely
        # unused.
        agents/lib/tax_computations|agents/lib/tax_computations/*)
            RUN_AGENTS=1
            RUN_PREFLIGHT=1
            RUN_PORTABILITY=1
            RUN_SECUREFS=1
            # ...and the host guard, which the generic arm below selects for
            # every engine module: `TestEveryModuleIsCovered` sweeps this
            # directory for the package import that puts a module behind the
            # refusal. Missing from all five per-file arms at first, for the
            # same first-match-wins reason the two flags above it were.
            RUN_HOSTGUARD=1
            ;;
        # A TEST module in the selector, and this arm is about the OTHER
        # module that reads it. `test_claude_hooks.py` derives the MEASURED
        # COSTS count column from live collection, so a test added or removed
        # HERE moves a figure asserted THERE — and an arm that selected only the
        # module it touched would leave that assertion to fail at CI instead,
        # which is the whole failure this hook exists to move forward in time.
        # The reverse edge needs no arm: the hooks module already selects itself.
        #
        # The comment that stood here was a verbatim paste of the
        # `tax_computations.py` rationale above it, describing an import edge
        # this arm has nothing to do with. A reader following it would either
        # "repair" this arm with engine selections or delete the identically
        # commented one as a duplicate — after which tax_computations.py falls to
        # the generic `agents/lib/*.py` arm and stops selecting the agents module
        # at all.
        tests/test_claude_agents.py)
            RUN_AGENTS=1
            RUN_HOOKSDEPS=1
            RUN_HOSTGUARD=1
            ;;
        # The directory AND every module under it. The roster test is only half
        # the reason and was, on its own, the wrong half: it pins the roster
        # against `agents/lib/*.py`, so only an ADD or REMOVE turns it red, and
        # an addition shows up as the directory's own timestamp moving. Reasoning
        # from that alone, this arm was once narrowed to the directory — and left
        # a hole, because `preflight.check()` IMPORTS all twenty-one modules and
        # executes one of them. A SyntaxError in `projection/` turns
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
            # ...and the host guard, which the generic arm below selects for
            # every engine module: `TestEveryModuleIsCovered` sweeps this
            # directory for the package import that puts a module behind the
            # refusal. Missing from all five per-file arms at first, for the
            # same first-match-wins reason the two flags above it were.
            RUN_HOSTGUARD=1
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
            # And the host guard, for the ADD case specifically: it asserts
            # every module here except two imports the package, so a new leaf
            # module written without that import is the failure it exists for —
            # and a new file shows up as this directory's timestamp moving,
            # never as an arm below.
            RUN_HOSTGUARD=1
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
        # `agents/lib/*`, not `agents/lib/*.py`. The extension was there to
        # dodge `__pycache__`, which the early return above now settles, and it
        # cost coverage in the one direction nobody watches: a path under the
        # engine that is NOT a `.py` file — a package replaced by a file, a
        # directory whose initialiser was just deleted — matched nothing at all
        # and this hook exited 0 over a broken engine.
        agents/lib/*)
            RUN_PREFLIGHT=1
            # test_host_guard reads agents/lib/__init__.py directly (the
            # no-env-opt-out assertion) and sweeps every module here for the
            # package import that carries the guard into the script-path form.
            RUN_HOSTGUARD=1
            # test_secure_fs audits EVERY module here for a network import and
            # for a raw path reaching a message.
            RUN_SECUREFS=1
            # test_portability EXECUTES four of these modules and asserts they
            # exit 0 (TestSelfTestClaim), so an engine edit that breaks a
            # built-in self-test turns portability red — and selecting preflight
            # alone left that silent, since the module still imports fine.
            RUN_PORTABILITY=1
            ;;
        # SUITE-WIDE, so it selects everything. It runs before every module
        # here, imports the engine package, and calls `pytest.exit` from
        # `pytest_configure` when the guard refuses — it can end a run before a
        # single test of any module executes. It was watched one by one before
        # `tests` became a root, and until it was named here an edit to the one
        # file that can stop collection reached `nothing_selected` and exited
        # silently, and a later Bash call could not see it either. The arm stays
        # now that the root covers the visibility half: WATCHED_ROOTS decides
        # only what is SEEN, this decides what it means, and what this one means
        # is everything. The cost is the cold-pass
        # row in MEASURED COSTS, which is the honest price of a file whose
        # blast radius is the whole table.
        tests/conftest.py)
            select_every_module
            ;;
        tests/test_preflight.py)
            RUN_PREFLIGHT=1
            RUN_HOOKSDEPS=1
            RUN_HOSTGUARD=1
            ;;
        tests/test_secure_fs.py)
            RUN_SECUREFS=1
            RUN_HOOKSDEPS=1
            RUN_HOSTGUARD=1
            ;;
        tests/test_host_guard.py)
            RUN_HOSTGUARD=1
            RUN_HOOKSDEPS=1
            # ...and portability, since `TestTheEngineDirectoryIsFlat` reads
            # this module's roster BY NAME. That guard's premise is that a flat
            # `glob("*.py")` stops covering a module the day it moves into a
            # package, so a roster here that learned to descend leaves the
            # guard standing over a reason that has gone.
            RUN_PORTABILITY=1
            ;;
        # SPLIT from the arm below, and the split is the whole point. The six
        # test-module arms select the hooks module because test_claude_hooks
        # PARSES their source to re-derive this mapping — a dependency on the
        # SOURCE, which a template edit does not touch. Sharing one arm charged
        # every edit of this template a full hooks pass, the dominant row in
        # MEASURED COSTS, for a dependency it does not have. First-match-wins
        # means the cheap arm has to come first and carry its own selections.
        agents/templates/taxpayer_profile_template.json)
            RUN_CAPABILITIES=1
            ;;
        tests/test_capabilities.py)
            RUN_CAPABILITIES=1
            RUN_HOOKSDEPS=1
            RUN_HOSTGUARD=1
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
            RUN_HOSTGUARD=1
            # test_claude_agents.py reads every parsing skill through it, and
            # has been missing from this arm since PR #101 gave it the import:
            # dropping the `references/*.md` fold turns three of its
            # parse-boundary assertions red, and this hook ran four modules,
            # passed them and exited 0. Found by DERIVING the importers rather
            # than listing them, which is what the W2 tail added — the list had
            # simply never been revisited after the import landed.
            RUN_AGENTS=1
            ;;
        # The OTHER shared test helper, and watched for the same reason
        # `skill_text.py` is: the modules below do not merely import it, they
        # ask it what `agents/lib/` CONTAINS. Narrowing its walk narrows the
        # host-guard coverage assertion, the packaged-engine guard, preflight's
        # roster comparison and the unredacted-path roster at once, and every
        # one of them would stay green — which is the failure the helper itself
        # exists to end. The W2 tail moved the last hand-rolled walks onto it,
        # which is what added the second pair. Four more modules read it and
        # are not among the seven — CI is where those show. They are not listed
        # here: the membership is DERIVED by
        # `test_every_watched_module_that_reads_a_shared_helper_is_in_its_arm`,
        # and a list in a comment beside a derivation is the second copy that
        # goes stale.
        tests/engine_tree.py)
            RUN_PORTABILITY=1
            RUN_HOSTGUARD=1
            RUN_PREFLIGHT=1
            RUN_SECUREFS=1
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
        # CLAUDE.md is split out because a SECOND module reads it: the
        # acceptance guards require the fresh-session constraint to survive in
        # the file an agent actually has loaded, that constraint having been
        # deleted once already by a rewrite of the paragraph holding it. Left in
        # the portability-only arm below, editing CLAUDE.md ran the module that
        # checks its LENGTH and not the one that checks that sentence is still
        # there — half-covered at the exact boundary the guard was written for.
        CLAUDE.md)
            RUN_PORTABILITY=1
            RUN_AGENTS=1
            ;;
        # AGENTS.md is split out for the same reason CLAUDE.md above is: a
        # SECOND module reads it. test_preflight's DOCUMENTS sweeps it for a
        # bare-interpreter engine call, per-line and by heredoc, so adding one
        # to §7's `python3 -m venv` paragraph turns that module red at CI while
        # the portability-only arm ran the length check and nothing else.
        AGENTS.md)
            RUN_PORTABILITY=1
            RUN_PREFLIGHT=1
            ;;
        # Same list, and the only entry in it that is not prose. The template
        # quotes the commands a reporter is asked to run, so it is scanned like
        # any other document.
        #
        # By its exact path, on the terms the workflow arm below states. DOCUMENTS
        # names bug_report.yml and nothing else under that directory, so a
        # `.github/ISSUE_TEMPLATE/*` glob charged every edit of config.yml and
        # state_module.yml a preflight pass for a read no module makes. The BARE
        # directory stays beside it: it is a watched root, and find prints the
        # root itself when a file is added directly beneath it — the addition
        # this arm cannot see the name of yet.
        .github/ISSUE_TEMPLATE|.github/ISSUE_TEMPLATE/bug_report.yml)
            RUN_PREFLIGHT=1
            ;;
        # The WORKFLOW, by its exact path and not by its directory. It was
        # deliberately absent while its only reader was
        # tests/test_pii_content_scan.py, which is not one of the six modules
        # this hook runs; the agents module now reads it too, to pin the
        # checkout depth that decides whether the acceptance record's Replay A
        # guard can reach the revision it replays. A directory arm would select
        # for every other file under `.github/workflows/`, which no selected
        # module reads — the over-broad shape this table has already paid for
        # once, and which only a COST assertion can catch.
        .github/workflows/ci.yml)
            RUN_AGENTS=1
            ;;
        LICENSE|.gitignore|\
        .pii-allowlist|.vscode/settings.json|\
        docs/contributing|docs/contributing/*.md|\
        docs/ops|docs/ops/*.md|\
        docs/roadmap|docs/roadmap/*.md)
            # The roadmap joined this arm when `TestTheBacklogSaysWhatIsOpen`
            # started reading it. Its Part 4 list is what a reader works down
            # to pick the next PR, and its status table is what says which
            # entries are already shipped — two halves that drifted for a
            # month, until V11 was nearly re-implemented off the list.
            RUN_PORTABILITY=1
            ;;
        tests/test_portability.py)
            RUN_PORTABILITY=1
            RUN_HOOKSDEPS=1
            RUN_HOSTGUARD=1
            ;;
        # `TestTheEngineDirectoryIsFlat` COUNTS the flat `glob("*.py")` sweeps
        # across tests/, so a NEW test module — which is when find prints the
        # bare directory — moves the figure its non-vacuity check reads. The
        # per-module half of the same dependency is on `test_host_guard.py`'s
        # own arm above, which first-match-wins reaches before this one.
        tests)
            RUN_PORTABILITY=1
            RUN_HOSTGUARD=1
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
        # LAST among the `tests/` arms, so every one above keeps its own richer
        # selection — `case` is first-match-wins, and each of those repeats
        # `RUN_HOSTGUARD=1` rather than falling through to here.
        #
        # `test_host_guard.py::TestNoTestSpawnsABareInterpreter` walks
        # `tests/*.py` WHOLE — every module in the directory, not the seven this
        # hook otherwise knows about — asserting that no test spawns the engine
        # through a PATH lookup for `python3`. So roughly forty modules became
        # source behind an assertion with no arm at all: adding a
        # `subprocess.run(["python3", ...])` to tests/test_scenario.py turned
        # that sweep red at CI while this hook matched nothing and exited 0 in
        # milliseconds. The branch that introduced the sweep edited two such
        # modules itself. The read-set locator in
        # `test_the_watched_set_still_covers_what_the_modules_read` cannot see
        # this, because it recovers `REPO / "literal"` reads and a glob is not
        # one — which is exactly the case the hand-written mapping exists for.
        #
        # Scoped to the EXTENSION the sweep parses, for the reason the arm above
        # is: a pytest run rewrites `tests/__pycache__/*.pyc`, whose ctime then
        # beats the stamp on the next Bash call, and a bare `tests/*` would buy
        # a pytest module for every `ls` that followed one. The bare directory
        # is kept because `find` prints the root itself when a file is ADDED
        # directly beneath it — a new test module, which the sweep reads and
        # this arm cannot see the name of yet.
        tests|tests/*.py)
            RUN_HOSTGUARD=1
            ;;
    esac
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
# 184-190s, 199-204s, 248.7-250.5s and now 228.4-243.4s — the first reading in
# seven to come DOWN while the suite GREW, by sixty-nine tests, which is the point
# about weather made by the data. The portability module moved further — 7.5-10.2s
# one day, 12.7-13.4s the next, with no code between the two — which is why the
# ratios below are stated as "roughly" and why a figure lifted from here to three significant
# figures would be wrong by the next reading. Every line is a RANGE with a run
# count.
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
# Measured with the set it came back 1.0s flat, and this sitting 1.1-1.2s: five
# times the row it replaced, no longer the smallest of the seven, and sitting
# just below capabilities rather than well below it. Two independent sittings
# agreeing to a tenth is the strongest thing this block can say about any row,
# and it is worth more than either reading alone. The fixed-overhead sentence
# above under-explains this row for the same reason it always did — the git
# calls are most of what it costs, not the interpreter start.
#
#   what triggers it               what runs     observed range    runs
#   ---------------------------------------------------------------------
#   nothing watched changed        no pytest    0.08 - 0.11s         6
#   agent-definition edit           48 tests      1.0 - 1.8s          6
#   skill .md edit                1034 tests     19.7 - 23.4s         3
#   cold pass, all seven modules  1274 tests    137.6 - 150.1s        3
#   (for scale) `pytest tests/`   4879 tests    228.4 - 243.4s        2
#
# COUNTS ARE WHAT RUNS, not what collects. Two places bite: `run_focused_modules`
# passes two `--deselect` flags, so portability collects 249 and runs 247; and
# the hooks module holds two platform skips, so it collects 148 and runs 146.
# THE SUITE holds seven skips, not two: those two plus the five Windows-only
# DACL cases W6 added, which are skipped everywhere but on the windows job.
# One figure used to cover both, on the assumption that the hooks module was
# the only place a test could be skipped — an assumption nothing stated and
# the first conditional test outside that module broke.
# `pytest tests/` collects 4886 and runs 4879 for the same skips — and that row
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
#   tests/test_claude_hooks.py     146 tests    114.7 - 130.0s       4
#   tests/test_portability.py      247 tests     11.1 - 13.1s        6
#   tests/test_capabilities.py     175 tests      0.8 - 3.1s         6
#   tests/test_claude_agents.py     48 tests      1.0 - 1.8s         6
#   tests/test_secure_fs.py        182 tests      1.4 - 2.1s         6
#   tests/test_preflight.py        382 tests      5.6 - 7.2s         6
#   tests/test_host_guard.py        94 tests      1.4 - 1.8s         6
#
# The agent-definition row above and the agents row here are the SAME
# invocation — that trigger selects that module alone — and are stated with the
# same range on purpose. Two figures for one measurement is how this block
# started drifting from itself the first time.
#
# Read the preflight line as a RATIO, which is the half that survives a change
# of machine: it sits between the agents module and portability, and is
# somewhere between a sixteenth and a twenty-third of the hooks module that
# dominates every total here. That same ratio read a sixteenth-to-a-seventeenth
# one sitting ago, a twentieth-to-a-twenty-second the sitting before that, and a
# thirteenth-to-a-twenty-third before THAT, on a module that has gained fifteen
# tests across the four and lost none — it moves because the SPREAD moves, not
# because the work does, which is itself the reason to distrust any single
# reading of the seconds any of them comes from. This sitting is the clearest
# case yet: the same module, measured twice with nothing touched between, gave
# 6.3-7.2s and then 5.6-6.4s. A skill
# edit
# selects it alongside portability, capabilities and secure_fs, which is where
# its cost actually lands. It has grown against its neighbours four times: the
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
# edited module, the host-guard module, and the three node ids in
# `HOOKS_DEPENDENTS`, never the hooks module those ids sit in — the log entry below that records `RUN_HOOKS=1` on
# those arms was narrowed fourteen rounds later, and read as current it
# describes a policy that has not been in force since.
#
# What the three ids cost was stated NOWHERE, and every comment beside them
# implied it was nothing by quoting the preflight-to-hooks ratio — a ratio about
# the module the narrowing SPARES, which says nothing at all about what replaced
# it. Measured WITH this sitting, against the preflight row, back to back: the
# three ids cost 12.2-12.9s over three runs, between one and three quarters and
# two and a third of that module. Two of them drive the real hook once per watched
# path, a subprocess each, and that is where all of it goes; the third is an
# `ast.parse` worth nothing measurable. The ratio was one and a half to one and
# two thirds a sitting ago, one and three quarters to two and a quarter the one
# before, and TWO to two and a half before THAT. It rose back this sitting with
# nothing about the ids changing: the preflight row they are stated against came
# DOWN, which is the whole hazard of expressing one cost as a ratio to another
# that is itself weather.
#
# THE HOST-GUARD MODULE RIDES ON EVERY ONE of them, and on the forty-odd test
# modules that have no arm at all, where it is the whole of what the trigger
# runs. `TestNoTestSpawnsABareInterpreter` sweeps `tests/*.py` whole, so every
# module in that directory is source behind it; the price is the smallest row in
# the decomposition above, paid on a save of any Python file there.
#
# So a save of tests/test_preflight.py pays about three of its own module, and a
# save of tests/test_host_guard.py — the cheapest of the seven — pays many times
# its own. Against the hooks module they stand in for, the three ids are between
# a ninth and an eleventh — 12.9 against 114.7 at one end and 12.2 against 130.0
# at the other. Two sittings ago it read "a tenth to a twelfth" over rows giving a
# NINTH at the top (11.7 against 112.2), which is a figure wrong in the direction
# that flatters the narrowing, and the fourth time one in this block has been: it still buys the order of magnitude it was made for, but it is not
# the rounding error the earlier wording made it, and the next narrowing has to
# come out of those two subprocess loops rather than out of this list.
#
# Still no row in the TABLE, and now for a reason that is not staleness: the
# seconds above were taken with this sitting, but the table's second column is a
# module count and what this trigger runs is one module plus three node ids — a
# row would have to state a count that is no module's, which is how the `allowed`
# set keeping every "N tests" in this block honest would come to have an
# exception in it. The figure lives in this paragraph instead, measured on the
# same terms as every row above it.
#
# ARITHMETIC, stated because a block whose parts do not add up is a block a
# reader stops believing. Those seven sum to 136.0s at their minima and 159.1s at
# their maxima, against 137.6-150.1s measured for the seven run together — so this
# sitting the parts BRACKET the whole rather than sitting one side of it: 1% below
# at the bottom, 6% above at the top. Seven sittings have now put them above by
# 6%, inside it, above by 3-4%, above by 4-5%, above by 1-2%, above by 2-3% and
# now straddling, on a decomposition whose membership did not change: the term
# that moves is the per-module spread, which the paragraph below says is the only
# accuracy parts and whole can ever have between them. The straddle is that
# paragraph's own point arriving as a measurement — the cheap rows were taken
# TWICE this sitting, once at its start and once near its end, and every one of
# them came back 20-40% lower the second time on an idle machine with nothing
# touched between (capabilities 1.4-3.1s then 0.8-0.9s; preflight 6.3-7.2s then
# 5.6-6.4s). Both passes are in the ranges above, which is why they are wider
# than any previous sitting's and why the sum straddles. A row here is a RANGE
# and reading its floor as the figure is what the width is warning against.
# Seven separate invocations each paying an interpreter start pushes the parts
# up and is worth well under a second of it. What separates this from the
# count-column patch the header forbids is the other half, which is EXACT:
# 48 + 175 + 182 + 247 + 382 = 1034 for a skill edit, plus 94 + 146 = 1274 for a
# cold pass, both matching the trigger table above to the test. That sentence
# itself sat stale for several rounds at "160 + ... = 703 ... 845" — a typo'd
# part and a superseded sum, green the whole time because the mechanical check
# reads the TABLE and this line is prose. It then went stale a SECOND time, at
# "322 ... 753 ... 36 ... 913" against a table already reading 343 and 37, under
# the very clause claiming it moves with the table — because "it moves with the
# table" was a promise a person keeps, not a check. It is checked now: the same
# test reads this sentence, requires its four parts to be the four module rows
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
# EVERY SECOND above was measured together, most recently 2026-09-08, on an idle
# machine, and re-measured END TO END rather than patched where a count moved:
# V5 added ONE case to secure_fs, which moves that row, the skill-edit row and
# the cold pass at once, and patching one row over stale neighbours is what left
# the derived rows wrong four revisions ago.
#
# THIS SITTING SAYS ONE THING LOUDER THAN ANY ROW. The cheap modules were
# measured TWICE, at its start and near its end, with nothing touched between,
# and every one came back 20-40% LOWER the second time: capabilities 1.4-3.1s
# then 0.8-0.9s, preflight 6.3-7.2s then 5.6-6.4s, portability 12.5-13.1s then
# 11.1-12.4s, secure_fs 1.8-2.1s then 1.4-1.5s, the host guard 1.6-1.8s then
# 1.4-1.5s. Same host, same idle machine, same commands, half an hour apart. The
# ranges above are the UNION of both passes, which is why they are wider than any
# previous sitting's, and the reason to publish the union rather than the tidier
# second pass is that a reader comparing a future row against a floor taken while
# the machine was warm would read weather as a regression. Whatever the effect is
# — filesystem cache, thermal, the full-suite runs earlier in the sitting — it is
# LARGER than most of the between-round differences previous sittings narrated as
# causes, and it is the strongest argument in this block for reading ratios.
#
# Against the previous end-to-end sitting every row came down: the hooks module
# 114.7-130.0s against 121.8-124.4s (wider, and straddling it), the cold pass
# 137.6-150.1s against 136.4-143.5s at the floor but reaching further, the
# skill-edit row 19.7-23.4s against 24.5-26.6s, and `pytest tests/` 228.4-243.4s
# against 248.7-250.5s — on a suite sixty-nine tests LARGER. None of that has a
# cause anyone should name: one case was added to one selected module and nothing
# was made faster. Read it as the same weather the double-measurement above
# demonstrates inside a single sitting, and read the ratios, not the seconds.
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
# nine hundred and six tests (3973 to 4879), and the gap was ZERO when the sitting ended: it took the
# counts and the seconds TOGETHER and took them LAST, after every edit on that
# branch was final. Since then a review round landed fourteen cases in
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
# portability has gained seventy (177 to 247), preflight has gained seven (375 to 382), the
# agents module none (48 to 48), secure_fs has gained twenty-two (160 to 182), the hooks module
# has gained eighteen (128 to 146), capabilities has gained fifty-nine (116 to 175) and the host guard has gained one (93 to 94). EVERY pair is measured from the SITTING and not from the round
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
# tests/skill_text.py with no arm selecting it. Of the eight hundred
# and ninety-three tests the suite has gained since the sitting, TWENTY-TWO landed in
# tests/test_secure_fs.py — the Windows cases, one case each for
# V2's, V1's, Y4's and V7's new engine modules, and W2's ten further engine
# files, that module's roster being parametrised per file — SIXTY-FIVE landed in
# tests/test_portability.py, which is Y5, the Z1 skill-text checks, the
# backlog-status pair, X5's figure lint, W2's packaged-engine guard and the
# shared engine enumerator's own five, the backlog table's ordering pin, the
# caveat-matches-the-schema pair, the hand-walk guard's three, X6's three pins
# on the skills the figure registry did not cover, and the seventeen the
# registry's DERIVED scope added — one per triaged skill, plus the two that
# refuse an untriaged or unexplained one, and
# is a row moving on its OWN account
# rather than by parametrisation, SEVEN landed in tests/test_preflight.py, which
# is W2's package entry-point check in both directions and the two that pin the
# unified dispatch rule, FIFTY-NINE landed in
# tests/test_capabilities.py, which is Z7's residency registry and Z1's depth
# note, and is the third
# such row, EIGHTEEN in tests/test_claude_hooks.py — the derived helper-arm
# check and its three cry-wolf probes, the package-directory class's nine,
# and W2's — and ONE in
# tests/test_host_guard.py, and
# the other seven hundred and twenty-nine in test_answers, test_ci_workflow,
# test_pii_content_scan, test_evals, test_eval_run, test_parse_records,
# test_return_builder, test_receipt, test_filing_package, test_pdf_render,
# test_workspace, test_tie_out, test_yoy_diff, test_tax_calendar,
# test_withholding, test_tax_computations, test_projection and
# test_loss_harvest, none
# of which this hook has an arm for. That last group is the ordinary case and the reason the pairs
# exist — a round that adds tests OUTSIDE the seven costs the block a sentence,
# not another half-hour sitting. The seconds are re-taken when the DECOMPOSITION
# moves by more than the one case a new engine module puts in secure_fs, and it
# now HAS: the sitting is owed, and the paragraph above this table says why it is
# owed LAST rather than now.
# They sum to one hundred and seventy-seven; the remaining seven hundred and twenty-nine are modules this
# hook does not select, which is the reconciliation this sentence exists to state.
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
# scale row moved ALONE. What is structural is the other gap: an agents test
# never reaches the skill row, so the skill row trails the cold pass by the
# agents delta every time, one here and one last round.
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
# The hooks module dominates both the parts and the total — it alone swings
# several seconds between runs, more than every other module added together — so
# parts and whole can only ever agree to within ITS noise. A future reading where
# they disagree by more than that is a reading where something real changed.
#
# SPREAD is what justifies filtering at all: more than three orders of magnitude
# from the fast path to the cold pass, both in the first table. The previous behaviour
# ran the checks after every Bash call, which made `ls` and `git status` pay the
# cold-pass line rather than the fast-path one.
#
# WHERE THE COST IS is one module, and the bound is arithmetic rather than
# approximate: portability, capabilities, agents, secure_fs, preflight and the
# host guard together are at most 31.2s even taking each at its slowest, so of a
# cold pass that never measured below 148.9s, at least 79% is
# tests/test_claude_hooks.py. A hook can only be tested by running it — that
# module spawns bash processes across its 146 tests, more than one per case
# (205 spawns over 105 cases when last counted with a `bash` shim on PATH;
# the RATIO is what carries forward, not the absolute) and holds deliberate
# sleeps — five tests at about 4.5s each. Selecting a subset is therefore the
# whole win: a skill edit skips that module and costs between a sixth and an
# eighth of a cold pass (17.0-21.7s against 131.5-133.1s), an agent-definition
# edit roughly a hundred-and-twentieth. When this gets tight the lever
# is that module's own runtime, not this list and not the timeout.
#
# HEADROOM is the cold pass against the timeout registered for this hook in
# .claude/settings.json. That timeout is 480s and the worst observed cold pass
# is 150.7s, so the margin is 3.2x. Plan against the WORST — the run that overruns
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
# else: the three assertions in that module that reach the other six. Only
# tests/test_claude_hooks.py itself takes `RUN_HOOKS`. The whole module was the
# policy for three rounds and the bill was the dominant row in the DECOMPOSITION
# above, so a save of the file this branch grows fastest paid that module on top
# of its own row for three assertions. That is the fast path this hook exists to
# protect, and a gate that slow on routine work is one that gets switched off,
# which is the outcome no assertion constrains.
#
# What the three ids cost INSTEAD is not nothing, and this paragraph used to
# imply it was by quoting the preflight-to-hooks ratio — which is about the
# module the narrowing spares. The figure is THE TRIGGER WITH NO ROW, above,
# stated once and not restated here.
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
