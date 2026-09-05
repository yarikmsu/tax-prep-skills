"""Tests for the engine-presence preflight (agents/lib/preflight.py).

P1: the check blocks when the computation engine is missing, partially copied,
    or present but not computing correctly — and passes in a full clone.
P2: every skill that invokes the engine carries the gate, and the gate's
    load-bearing sentence (a failure to RUN the check IS the block) survives
    editing. That sentence is the only thing standing between a skill copied
    away from `agents/lib/` and a return computed with no hard gates at all,
    because the script cannot report its own absence.

SCOPE, and it is FROZEN. The launcher audits — `_bare_engine_units` and
everything it calls — model an HONEST author writing a bare interpreter call in
a shape this corpus plausibly contains. They are a documentation lint standing
behind an engine that refuses a host interpreter at import
(`agents/lib/__init__.py`), not a security boundary, and bash grammar is larger
than any approximation of it here will be. So a miss on a shape no document in
the tree writes is ACCEPTED and recorded, not fixed. A fix is taken only when
all three hold: the shape exists in the corpus or an author would plausibly
write it, the miss is SILENT, and the fix does not widen the parser. Otherwise
the finding is refuted by citing this paragraph — twenty rounds of widening this
file added parser surface for the next round to find bugs in, and the round that
prescribed joining implicit `|` continuations would itself have fail-opened on
69 markdown table rows and a YAML `run: |`.

KNOWN AND ACCEPTED, each zero-for-zero against the corpus as of 2026-09-04:
an implicit continuation after a trailing `|` is not joined; a `SEPARATOR`
inside a shell comment splits a span before `_blank_comments` runs; and
`_piped_engine` reads its downstream head without `_shell_unescape`, so a
pipe whose downstream head is split by a backslash continuation before
`python3 -` clears.
"""
import io
import json
import os
import pathlib
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import preflight as P  # noqa: E402
from tests.skill_text import (  # noqa: E402
    CONTRIBUTOR_SKILLS, ENGINE_FREE_SKILLS, skill_text)

REPO = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO / ".agents" / "skills"



def _complete_package(pkg):
    """A fake engine package that is complete APART from the file under test.

    These fixtures repoint `P._HERE` at a throwaway directory and used to leave
    its `__init__.py` empty, which is now a defect in its own right: preflight
    certifies the host-interpreter guard living there, so an empty initialiser
    blocks and every assertion below it would be measuring that instead of the
    one file it altered. Copying the real one keeps each fixture's single
    deliberate defect the only defect in it.
    """
    (pkg / "__init__.py").write_text(
        (REPO / "agents" / "lib" / "__init__.py").read_text())


GATE_HEADING = "### Engine preflight (HARD GATE — runs before every other step)"
# ONE launcher, not an inlined prefix. The activation used to be pasted at every
# call site — 106 of them — and a 190-character string repeated that often
# drifts: five sites chained the prefix and then invoked a bare `python3`, so on
# a Windows venv they ran the HOST interpreter while this audit, which only
# checked that the PREFIX was present, reported them covered. The wrapper makes
# that unrepresentable: there is nowhere left to name an interpreter.
LAUNCHER = "sh agents/lib/run.sh"
GATE_COMMAND = f"{LAUNCHER} agents/lib/preflight.py"

# Skills whose only engine call in SKILL.md is the gate itself, every other
# invocation living in a reference. Named rather than skipped silently, so
# the ordering assertion below cannot go vacuous without someone deciding it
# should. Empty today, and the assertion is what keeps it honest.
GATE_ONLY_SKILLS: set[str] = set()

# `python3 -c "…"` opens a command that runs on to its closing quote, so the
# `from agents.lib.tie_out import …` that makes it an engine call lands on a
# LATER line than the `python3` does. A per-line audit sees neither half as an
# engine call and reports full coverage over three commands it never looked at.
# The launcher form too: every inline `-c` in the skills is now
# `sh agents/lib/run.sh -c "`, so a pattern that knew only `python3 -c`
# matched ZERO units the moment the launcher landed — and the test that
# uses it, which exists because tax-fbar shipped an indented `-c` body
# that raises IndentationError below 3.13, went silently vacuous.
#
# The interpreter may carry its VERSION. `python3.12 -c "` is how a document
# names the exact interpreter a venv installed, and `python3? +` required a
# SPACE after the `3` — so the opener was not found, the join never happened,
# and the `-c` body arrived as a unit of its own with no interpreter in it.
# Neither half is an engine call and the pair is one, which is the silent pass
# this pattern exists to close, on the spelling it could not read.
# `BARE_ENGINE_CALL` and `INTERPRETER` already read the versioned name — `\b`
# falls between the `3` and the `.` — so the one-line and heredoc forms were
# caught throughout, and this join was the only half of the rule that was not.
#
# The `-c` OPTION as python spells it, and the SAME fragment `INLINE_SCRIPT`
# below is built from, because two lexers that disagree about which commands
# carry an inline body are two answers to one question. They disagreed: this one
# demanded a DETACHED `-c`, so `python3 -uc '` — the unbuffered cluster
# `INLINE_SCRIPT` was widened to read a round ago, and the very spelling the
# paragraph above says now lexes as `-c` — opened no join at all, and neither did
# the attached `python3 -c'`. A multi-line body written either way arrived as
# units with the interpreter in the first and the engine import in the third,
# each clean read alone.
#
# The cluster is read LEFT TO RIGHT and must END at the `c`, which is python's
# own reading: after `-c` the rest of the argument is the program, so `-cu 'x'`
# runs `u` and is not this shape. `(?<![\w-])` keeps it off the tail of a long
# option, and `[ \t]*` admits the attached operand — python takes `-c'code'`
# exactly as it takes `-c 'code'`.
INLINE_C = r"(?<![\w-])-[A-Za-z]*c[ \t]*"

INLINE_PY = re.compile(
    r"""(?:python3?(?:\.\d+)? +|run\.sh +)""" + INLINE_C + r"""(["'])""")

# `python` and `python3` both, matching INLINE_PY: a literal "python3" test
# would let `python -m agents.lib.projection` through the audit unprefixed,
# which is the same unchecked interpreter by another name.
# `python3 `, `python `, and the `${TAXPY:-python3}` form the skills use to
# name the interpreter a Windows venv actually installs — that one ends in
# `}`, not a space, so the original pattern stopped matching every engine
# invocation the moment the layout work landed.
# The launcher counts as running python — it is the ONLY way the skills are
# supposed to reach an interpreter now, so a predicate blind to it would
# find zero engine invocations and every per-skill assertion would iterate
# nothing while reporting full coverage. The bare spellings stay so a unit
# that goes around the launcher is still caught.
# The VERSION suffix for the reason INLINE_PY takes it, and with a sharper
# consequence: `[ }]` demanded a space right after `python3`, so
# `python3.12 -m agents.lib.ptet` read as not-python at all, and every per-skill
# selection keyed on this predicate DROPPED the unit while reporting coverage
# over the skill that holds it. A predicate used to choose what to check goes
# vacuous silently.

# The interpreter WORD, in every spelling that reaches one here, stated once so
# the four patterns below cannot enumerate different sets. `py` is the Windows
# launcher: `py -3 -m agents.lib.ptet` is what a Windows checkout writes where a
# POSIX one writes `python3`, this repository documents Windows checkouts, and
# recognition by enumerated spelling meant that command ran the engine on a
# stock interpreter with every audit here green.
#
# BOUNDED on both sides by hand rather than by `\b`, and that is the whole care
# in it: `\bpy\b` matches the EXTENSION of every `.py` path in the corpus, so
# `agents/lib/ptet.py` would read as an interpreter naming itself and every
# engine path in prose would become an invocation. The lookbehind rejects a
# preceding word character, dot or dash — `numpy`, `gen.py`, `-py` — while
# leaving `/usr/bin/py` readable, and the lookahead rejects `py-spy` and
# `py.exe` the same way.
PY_WORD = r"(?:\bpython3?\b|(?<![\w.-])py(?![\w.-]))"

RUNS_PYTHON = re.compile(
    r"\bpython3?(?:\.\d+)?[ }]|(?<![\w.-])py[ }]|run\.sh")

# The interpreter alone, WITHOUT `run.sh`. `RUNS_PYTHON` above counts the
# launcher as an interpreter because for the activation audit it is one; the
# heredoc scanner asks the narrower question — is this command a python
# invocation at all — and answering it with the wider one would make the plain
# `run.sh - <<'PY'` form register as an opener it is not. Built into the opener
# pattern below AND used to check who owns the delimiter, so the two cannot
# come to disagree about what an interpreter looks like.
INTERPRETER = re.compile(PY_WORD)


# The dot may carry WHITESPACE on either side: `from agents . lib import ptet`
# and `import agents . lib . ptet` are what python's tokeniser makes of a dotted
# name, both valid and both reaching the engine. Written tight the pattern read
# them as prose. The spelling is not a curiosity — it is what an INDENTED `\`
# continuation joins to, `from agents.` + `    lib import ptet`, which is how a
# reader would write the continued form the body scan exists to catch.
# ...and it may be one item of a LIST. `import json, agents.lib.ptet` is one
# valid statement binding the engine, and a pattern that demanded the module
# IMMEDIATELY after the keyword read it as an import of `json` and nothing else —
# so `python3 -c 'import json, agents.lib.ptet'` cleared the per-command audit
# and the heredoc body scan alike. The body scan is the worse of the two: it
# COUNTED the heredoc, read the body, and reported clean, which is the one
# failure shape indistinguishable from a body that holds no engine call.
#
# Each skipped item must be a dotted NAME with an optional `as` alias — python's
# own grammar for the list — and not a permissive run of characters. The
# difference is prose: `lib` is admitted bare (with `agents` on the path, `-m
# lib.ptet` loads the package), so a loose middle would let any English sentence
# holding "import" and a later "lib" read as an engine call, and the corpus this
# runs over is documentation.
ENGINE_IMPORT = (r"(?:from|import)\s+"
                 r"(?:[A-Za-z_]\w*(?:\s*\.\s*\w+)*(?:\s+as\s+\w+)?\s*,\s*)*"
                 r"(?:agents\s*\.\s*)?lib\b")

# ...and the same import written as a CALL. `__import__("agents.lib.ptet")` and
# `importlib.import_module("agents.lib.ptet")` bind the engine exactly as the
# statement does, and `ENGINE_IMPORT` — a pattern about the `from`/`import`
# keywords — matches neither, so `python3 -c '__import__("agents.lib.ptet")'`
# ran the engine on a stock interpreter with every audit clean.
#
# The literal is required to sit IN the call, and that is what makes this
# narrow enough to state: it is one construct, visible at the call site, with no
# dataflow to follow. `name = "agents.lib.ptet"` then `__import__(name)` is the
# assignment form and is NOT matched — the same bound `DYNAMIC_EXEC` declines to
# cross for `exec`, for the same reason, and named there rather than only here.
#
# The CALLEE is ANY name, and the closed list it replaced was the hole. It read
# `__import__|import_module`, so `runpy.run_module("agents.lib.tie_out")` — the
# stdlib's other loader, one import away — bound the engine with every audit
# green, and so did `from importlib import import_module as im` followed by
# `im("agents.lib.tie_out")`, an alias being a spelling no list can enumerate.
# A list of NAMES is the wrong shape for a question about a LITERAL: what
# reaches the engine is the module name in the argument, and the callee only
# says that a call is where it sits.
#
# What keeps this from reading every string in sight is `_blank_python`, never
# the pattern. A literal in a body survives the mask only where `DYNAMIC_EXEC`
# says the body loads dynamically at all, so the widening bites exactly in the
# bodies that could act on it. That is why the two moved together, and why an
# import of `importlib` or `runpy` is now itself a member there: the alias form
# has nothing at the call site to recognise, and the import that created it is
# the one place the body admits what it is about to do.
ENGINE_LOADER = r"\w+\s*\(\s*['\"](?:agents\.)?lib\b"

# The two spellings of "this text binds the engine", which is what a body scan
# asks. Kept as one name because the heredoc body scan and `_ENGINE_TARGET` both
# ask it and had drifted apart on the answer once already.
ENGINE_BINDING = f"(?:{ENGINE_IMPORT}|{ENGINE_LOADER})"

# `python3 -m agents.lib...`, `python3 agents/lib/...`, or a `-c` body importing
# from the package — the three forms that actually reach the engine. `python3 -m
# venv` and `python3 -m pytest` are not engine calls.
#
# ROUTING IS POSITIONAL HERE, and that is the whole reason this pattern is the
# one both audits now key on. `(?<!run\.sh )` asks whether the interpreter about
# to reach the engine is the LAUNCHER'S, which is a fact about where the literal
# sits. `LAUNCHER not in command` — what the activation audit asked before —
# is a fact about whether the literal appears ANYWHERE, and
# `python3 -m agents.lib.ptet  # use sh agents/lib/run.sh` satisfies it while
# running the engine on a stock interpreter. So does the launcher named inside a
# `-c` body, or in an argument. A mention is not an execution.
#
# The middle excludes a BACKTICK and nothing else. It excluded newlines too, and
# that quietly undid the join `_shell_units` performs: an inline `-c` body
# written across lines is ONE command, and a pattern that cannot cross the
# newline reads the interpreter and the import as unrelated. The backtick stays
# because in prose two separate code spans — "`python3` ... `agents/lib/x.py`" —
# are a sentence about two things, not one command.
#
# INSIDE A FENCE that exclusion is pure loss, so there is a second spelling for
# fenced text. A fenced block has no code spans — a backtick there is shell
# SUBSTITUTION — and `python3 -c "# writes `result.json`" …` is one command the
# pattern stopped dead at the first tick and cleared. The split is the one the
# opener patterns below already make for the same reason: prose has to be told a
# command from a sentence about one, and fenced text does not. Masking inert
# backticks instead was tried and is worse — an apostrophe in "the launcher's
# `python3` … `agents/lib/x.py`" opens a quote that never closes, so the whole
# sentence reads as quoted, both ticks vanish, and a paragraph ABOUT the rule is
# indicted for breaking it. That is the measurement recorded at `_unactivated`,
# reached from the other side.
#
# The PATH form takes a SUBPACKAGE. `agents/lib/\w+\.py` cannot cross a slash,
# so `python3 agents/lib/sub/tool.py` — an engine module one directory down —
# read as prose. There is no such directory today, which is what made the bound
# invisible: a pattern is not correct because the corpus happens to stay inside
# it, and `agents/lib/` is a package that may grow one.
# The MODULE form takes the same optional `agents.` as `ENGINE_IMPORT` above,
# and for the same reason: with `agents` on the path — `PYTHONPATH=agents`, a
# `sys.path` insert, an interpreter started from that directory — `-m lib.ptet`
# and `-m agents.lib.ptet` load one package. The import spelling has admitted
# both since it was written; the module spelling admitted one, so the two halves
# of the same rule disagreed about the same command.
#
# ...and the module NAME is a shell word like any other, so it may be quoted and
# it may be attached. `python3 -m "agents.lib.ptet"` and `python3 -magents.lib`
# are what python runs, and `\s+` unquoted matched neither — while
# `_calls_engine`, a substring search, called both engine calls. So the two
# halves of this rule disagreed about one command a second time, and in the
# worse arrangement: the skill was selected FOR audit as an engine caller and
# then cleared by the audit it was selected for. The PATH form needs nothing
# here — a quote sits outside `agents/lib/...` and the search steps over it.
#
# `-m runpy` is the fourth form and is the module spelling wearing a stdlib
# module's name. `python3 -m runpy agents.lib.tie_out` runs the engine exactly
# as `-m agents.lib.tie_out` does — runpy's whole purpose is to take the module
# name as its OPERAND — and the first alternative reads the word after `-m`, so
# it saw `runpy` and called the command clean. One indirection, no dataflow, and
# it is named here rather than left to `ENGINE_BINDING` because nothing in that
# command is an import statement or a call.
_ENGINE_TARGET = (r"(?:-m\s*['\"]?(?:agents\.)?lib\b"
                  r"|-m\s*['\"]?runpy['\"]?\s+['\"]?(?:agents\.)?lib\b"
                  r"|agents/lib/[\w/]+\.py|"
                  + ENGINE_BINDING + ")")
BARE_ENGINE_CALL = re.compile(
    r"(?<!run\.sh )" + PY_WORD + r"[^`]*?" + _ENGINE_TARGET)
FENCED_ENGINE_CALL = re.compile(
    r"(?<!run\.sh )" + PY_WORD + r"[\s\S]*?" + _ENGINE_TARGET)

# The `NAME=value` prefix a command may carry before its command WORD. Stripped
# before the pattern above runs, because an assignment is not an executable and
# the pattern's only handle on routing is what sits adjacent to the interpreter.
# Both directions were wrong without it, on the same construct:
# `RUNNER=agents/lib/run.sh python3 -m agents.lib.ptet` runs the engine on a
# stock interpreter and the lookbehind read the assignment's `run.sh ` as the
# launcher that put it there, while `TAXPY=python3 sh agents/lib/run.sh -m
# agents.lib.ptet` is compliant and was reported, the assignment's `python3`
# pairing with the target the LAUNCHER reaches. A value is not a command word.
#
# It is not the whole of the answer and callers must not use it as one — see
# `_command_head`, which owns that. An assignment may sit behind an exec prefix,
# `env RUNNER=agents/lib/run.sh python3 -m agents.lib.ptet` being the same
# false clear as the first example above with four characters in front of it,
# and this anchored pattern does not reach past them.
#
# The unquoted value class excludes a BACKTICK, which is not tidiness: a
# substitution is a COMMAND, and swallowing one as a value deletes it. ``OUT=`
# python3 -m agents.lib.ptet` `` (written without the space) has no whitespace
# after the `=`, so the class ate `` `python3 `` up to the space and handed back
# ``-m agents.lib.ptet` `` as the head — an engine target with no interpreter in
# front of it, cleared by both audits. Excluding the tick makes the whole
# assignment fail to match, the head is the command entire, and the interpreter
# inside the substitution is read where it sits. That direction is the safe one
# on every input: a value NOT stripped can only leave more text for a pattern
# that already has to find an interpreter reaching an engine target.
ASSIGNMENTS = re.compile(
    r"""^\s*(?:[A-Za-z_]\w*=(?:'[^']*'|"[^"]*"|[^\s'"`]*)\s+)+""")

# The same shape CAPTURING and ANCHORED, for reading the leading assignment
# words one at a time — the one form no single command is: a variable that a
# LATER command expands. `CODE='from agents.lib import ptet'` runs nothing —
# `_command_head` strips it to the empty string, correctly — and `python3 -c
# "$CODE"` names no engine, so each half is clean and the pair is a bare engine
# call. `_bare_engine_units` records what each name holds and `_expand` puts the
# text back before the patterns above run.
#
# ANCHORED because the unanchored spelling this replaced was a SEARCH, and a
# search reads an assignment-shaped ARGUMENT as shell state: `echo
# CODE='from agents.lib import ptet'` prints a string and assigns nothing.
ASSIGNMENT_WORD = re.compile(
    r"""(?P<name>[A-Za-z_]\w*)=(?P<value>'[^']*'|"[^"]*"|[^\s'"`]*)""")

# The same shape as an OPENER, for the join `_join_units` performs. A quoted
# value spans however many physical lines it needs, exactly as an inline `-c`
# body does, and only the `-c` opener was ever looked for — so `CODE='` /
# `from agents.lib import ptet` / `'` arrived as three units, `ASSIGNMENT_WORD`
# read `CODE` as holding the empty string, and the `python3 -c "$CODE"` below it
# expanded to `python3 -c ""` and cleared. Engine source sitting in the block,
# both audits green: the pair form this file has a whole map to model, defeated
# by where the newlines fell.
#
# The name must stand where the shell reads an ASSIGNMENT, which is the start of
# a command — the same position `_assigned_values` requires, so a join and a
# binding cannot disagree about which text is one. An assignment-shaped ARGUMENT
# (`echo CODE='...'`) is one that function already declines to record; joining it
# would cost a longer unit and no verdict, since the pattern that judges the unit
# still has to find an interpreter reaching an engine target.
ASSIGNMENT_OPENER = re.compile(r"""(?:^|[\s;|&(])[A-Za-z_]\w*=(["'])""")


# One command ends and another begins at `||`, `&&`, `;`, `|`, a background `&`
# or a subshell `(`. The `&` is guarded on BOTH sides, because bash spells two
# redirections with one: `2>&1` puts it after a `>` and `&>/dev/null` puts it
# before one. Only the first was excluded, so `python3 &>/dev/null -m
# agents.lib.ptet` — one command bash runs exactly as written — was cut in two,
# and neither half held an interpreter AND an engine target. Both audits
# reported it clean, which is the fail-open direction: a split invents a command
# boundary the shell does not have, and every predicate downstream is asking
# about text that is not a command. `&>>` is the same operator appending and is
# covered by the same lookahead.
# `|&` is bash's pipe-with-stderr and is ONE operator. Read as `|` followed by
# `&`, the next command begins with an ampersand — so `cat <<'PY' |& python3 -`
# has no interpreter at its head, `_heredoc_runner` follows the pipe to a command
# word that is not one, and the body goes unscanned. Listed before the single
# `|` because the alternation is ordered and the shorter branch would win.
SEPARATOR = re.compile(r"\|\||\|&|&&|[;|(]|(?<![<>])&(?!>)")


# A fenced-block delimiter, with the two things that decide whether it CLOSES
# one: which character it is made of, and how many. Both were thrown away by a
# rule that toggled on any line opening with three of either.
FENCE = re.compile(r"^\s*(?P<mark>```+|~~~+)(?P<info>.*)$")


def _fence_state(lines):
    """(inside, boundary) per line: is this line fenced CONTENT, and is it a
    fence delimiter?

    A toggle on every fence line reads the standard way to quote a fenced block
    in markdown — an outer fence of FOUR backticks around an inner one of three —
    exactly backwards. The inner ```` ``` ```` flips the state back to "outside",
    so everything the outer block contains is read as document prose: an opener
    in there is judged by the PROSE tail rule, which rejects the ordinary
    `<<'PY' input.json` spelling, and the body is never scanned at all. Silence,
    on the one construct a document uses to write ABOUT fenced commands.

    So the closing rule is the one CommonMark states, and both halves of it earn
    their place. A closing fence is made of the same character as its opener and
    is at least as LONG — three backticks cannot close four, which is what makes
    the nesting work — and it carries no info string, since ```` ```sh ```` names
    a language and opens rather than closes.

    Two lists, because the callers ask two different questions of the answer.
    `inside` is about the text: a fence line is neither content nor prose, which
    is the reading the scanners already had. `boundary` is about the structure,
    and the heredoc body scan needs it: bounded by "matches FENCE" it would stop
    at an inner ``` that closes nothing, ending the body early — and a body that
    ends early is the silent pass, not the visible one.
    """
    inside, boundary, opener = [], [], None
    for line in lines:
        m = FENCE.match(line)
        if m is None:
            inside.append(opener is not None)
            boundary.append(False)
            continue
        mark = m.group("mark")
        if opener is None:
            opener = mark[0], len(mark)
        elif (mark[0] == opener[0] and len(mark) >= opener[1]
                and not m.group("info").strip()):
            opener = None
        else:
            # Too short, the wrong character, or carrying an info string: inside
            # the block this is ordinary content that happens to look like a
            # fence, and reading it as one is the inversion above.
            inside.append(True)
            boundary.append(False)
            continue
        inside.append(False)
        boundary.append(True)
    return inside, boundary


def _inert(text):
    """One flag per character: is this a place the shell reads no syntax?

    True inside a quoted string, on both characters of a backslash escape, and on
    the quote marks themselves. Stated ONCE, because two callers ask two
    different questions of it and a second copy of the rules is a second thing to
    keep true: `_commands` splits on separators that are NOT inert, and
    `_executes` asks whether a literal sits somewhere the shell would run it.

    Where the shell escapes is where they are marked. Outside quotes a backslash
    escapes anything; inside `"` it is the only thing that can hide the closing
    quote; inside `\'` there are NO escapes at all — `\'don\\\'t\'` closes on that
    quote and the backslash is a literal character — so a single-quoted body is
    left exactly as it is.

    `` ` ... ` `` is the SAME construct in the older spelling, and it was the
    half of it this rule did not have. A backtick inside a double quote opens a
    command substitution exactly as `$(` does — ``out="`python3 - <<'PY'`"`` runs
    the body — and with the backtick carrying no syntax of its own here, the
    double quote reached over it and marked the `<<` inert. Zero heredocs seen,
    the body never read: not a verdict, an absence, which is the shape this file
    keeps finding in itself. Outside a quote nothing changes that mattered — the
    text was active there already — and inside `'` the branch above still runs
    first, a backtick being literal in a single-quoted string.

    `$( ... )` inside a double quote is ACTIVE, and that is not a refinement: it
    is the one form the corpus actually writes. `out="$(python3 - <<\'PY\')"` is a
    command substitution, parsed as shell and with its own quoting, so a rule
    that called everything between the double quotes inert lost a real heredoc
    opener — a silent pass introduced by a guard against silent passes, caught by
    the test that pins that spelling. The nesting is tracked with a stack because
    it nests: a quote inside the substitution opens and closes there.

    A plain `(` inside the substitution nests too, and counting only `$(` was the
    same silent pass one bracket in. `out="$( (echo x); python3 - <<'PY'` closes
    its GROUP first, that `)` popped the substitution, and the `"` underneath it
    made every remaining character inert — delimiter, interpreter and all — so
    the opener vanished and the body was never read. Depth is the fix, and its
    errors run the safe way: an unbalanced `(` leaves the context OPEN, which
    keeps the rest of the line active and readable, where the early pop hid it.

    The `$(` itself is SYNTAX, not text, and used to take the flag of whatever
    surrounded it. Inside a double quote that marked the OPENER inert while
    everything it contains — its own `)` included — stayed active, and the
    asymmetry is what hid a substitution the shell really reads. `_commands`
    splits at an active `(`, so `echo "$(python3 - <<'PY')"` was one command
    whose head is `echo`: the heredoc scanner read a non-interpreter, counted no
    heredoc, and a bare engine import in the body passed both audits in silence.
    Unquoted, the same line has always split. The quote was deciding.

    An unbalanced quote marks the rest of the text inert. For `_commands` that
    MERGES commands rather than splitting one, which over-matches and can only
    cost a false accusation a reader sees. For `_executes` it hides a launcher
    that may have been real, which reads as unrouted — again an accusation, not a
    clearance. A backslash escape is marked inert in every context for the same
    reason, though outside quotes the shell does run the escaped character: it is
    what keeps `_commands` from splitting at an escaped `&&`, and it costs
    `_executes` only the same safe direction. Both errors run that way, and that
    is not luck — the two callers use the flag with opposite polarity.
    """
    return _quote_scan(text)[0]


def _quote_scan(text):
    """`(_inert`'s flags, the contexts still OPEN at the end of `text`)`.

    One walk, two answers, because they are the same walk. `_join_units` needs
    the second: it joins physical lines while an inline `-c` body is still open,
    and it decided that by counting quote characters — which an ESCAPE makes
    wrong in the direction that matters. `python3 -c "x = '\\"'` has two double
    quotes and one open string, so the parity said closed, the join stopped, and
    the interpreter and the engine import below it landed in different units,
    each clean read alone. Counting cannot see an escape; this walk already does,
    and writing a second quote model beside it is how the two would disagree.

    The stack holds the same tokens the loop pushes — `'`, `"`, `` ` ``, `$(`,
    `(` — so a caller asks about the one it opened rather than about depth.
    """
    flags, stack, i = [False] * len(text), [], 0
    while i < len(text):
        ctx = stack[-1] if stack else None
        quoted = ctx in ("'", '"')
        ch = text[i]
        if ch == "\\" and ctx != "'" and i + 1 < len(text):
            flags[i] = flags[i + 1] = True
            i += 2
            continue
        if ctx == "'":
            flags[i] = True
            if ch == "'":
                stack.pop()
            i += 1
            continue
        if text[i:i + 2] == "$(":
            stack.append("$(")
            i += 2
            continue
        # The backtick is its own opener AND its own closer, so it is matched
        # against the context rather than pushed unconditionally: pushing on
        # every one would make a balanced pair two nested contexts, and the
        # closing quote of the string outside them would never be reached.
        if ch == "`":
            stack.pop() if ctx == "`" else stack.append("`")
            i += 1
            continue
        if quoted:
            flags[i] = True
            if ch == ctx:
                stack.pop()
            i += 1
            continue
        if ch == "(" and ctx in ("$(", "("):
            stack.append("(")
        elif ch == ")" and ctx in ("$(", "("):
            stack.pop()
        elif ch in "'\"":
            flags[i] = True
            stack.append(ch)
        i += 1
    return flags, stack


def _commands(text):
    """`text` split into shell commands, blind to a separator inside quotes.

    Quote-tracking is not fussiness. `python3 -c \'import sys; from agents.lib
    import ptet\'` is ONE command, and a plain `SEPARATOR.split` cuts it at the
    `;` inside the body into a half that runs python and a half that names the
    engine. Every per-command pairing below then finds neither, and a bare
    interpreter running an engine import reads as clean — this file\'s recurring
    bug, a predicate that matches less than it is believed to, reintroduced by
    the very split meant to sharpen it.

    Escapes count, and the claim that stood here — that ignoring them can only
    ever MERGE commands, so at worst a false accusation — was wrong in the one
    direction that matters. `python3 -c "print(\\"a;b\\"); from agents.lib import
    ptet"` is a single valid command; untracked, the escaped quote reads as the
    closing one, the `;` two characters later is then outside quotes and splits,
    and the interpreter and the engine import land in different pseudo-commands.
    Every per-command pairing below finds neither, and the bare invocation this
    audit exists to catch goes green. Not a merge: a split, and a silent pass.
    """
    return [text[a:b] for a, b, _ in _command_spans(text)]


def _command_spans(text):
    """(start, end, separator-before) per command, which `_commands` is the
    text of.

    Split out because one caller needs more than the text. A heredoc's body goes
    to whatever the PIPELINE hands it to, so `cat <<'PY' | python3 -` feeds the
    engine to a bare interpreter — and answering that needs the separator between
    two commands and their ORDER, both of which `_commands` throws away. It also
    needs to know which command a `<<` at a given offset sits in, which is what
    the spans are for: the delimiter is a position, and matching on the text
    alone cannot tell two identical commands on one line apart.

    The separator recorded is the one BEFORE the command, so the first is `""`.
    `||` and `|` are different facts here — one is a fallback, the other a pipe —
    and `SEPARATOR` already prefers the longer, so they arrive distinguishable.
    """
    inert = _inert(text)
    spans, start, i, sep = [], 0, 0, ""
    while i < len(text):
        found = None if inert[i] else SEPARATOR.match(text, i)
        if found:
            spans.append((start, i, sep))
            sep = found.group()
            start = i = found.end()
            continue
        i += 1
    spans.append((start, len(text), sep))
    return spans


def _flatten_substitutions(text):
    """`text` with the brackets of every WORD-level substitution blanked, so
    what they enclose reads as part of the command that encloses them.

    `(` is a SEPARATOR, which is right about the command INSIDE a substitution
    and wrong about the one around it. `$( ... )` is a word constituent: the
    shell splices its output into the argv of the command it sits in, and there
    is no `)` in SEPARATOR to end the split, so the enclosing command is cut in
    half and never put back. `python3 "$(git rev-parse --show-toplevel)/agents/lib/tie_out.py"`
    arrived as `python3 "$` and `git rev-parse --show-toplevel)/agents/lib/...`,
    and every pairing below asks one question — is the interpreter reaching this
    engine target one the launcher put there — of two fragments that hold one
    each. Both audits cleared it. So did `python3 - <<<"$(cat agents/lib/ptet.py)"`,
    which is the same cut with the target on the other side of it.

    BLANKED to spaces rather than removed, so every offset in the result still
    names the character the original has there — `_piped_engine` measures a span
    on raw text for exactly that reason, and a length-changing rewrite would put
    the two audits on different coordinates.

    Only a substitution used as a WORD, which is what `$`, `<` or `>` in front
    of the bracket says. A bare `( ... )` is a SUBSHELL — a command boundary the
    shell really does have — and flattening one would join two commands that
    have nothing to do with each other, inventing the pairing this exists to
    stop inventing in the other direction.

    Nested brackets are matched with a stack, because `$(dirname "$(which
    python3)")` closes twice and pairing the first `(` with the first `)` would
    blank one bracket of each pair and leave the text making no sense either way.
    """
    flags = _inert(text)
    out, stack = list(text), []
    for i, ch in enumerate(text):
        if flags[i]:
            continue
        if ch == "(":
            stack.append((i, i > 0 and text[i - 1] in "$<>" and not flags[i - 1]))
        elif ch == ")" and stack:
            start, word = stack.pop()
            if word:
                out[start] = out[i] = " "
    return "".join(out)


def _substitution_commands(text):
    """The enclosing commands `_command_spans` cuts in half at a substitution.

    ADDITIVE, and returned separately rather than folded into `_commands`, which
    is what makes this safe to widen with. `_command_spans` answers three other
    questions — which command a heredoc's `<<` sits in, what the pipeline hands
    a body to, and which separator joined two stages — and every one of them
    wants the substitution split exactly as it stands: `echo "$(python3 -
    <<'PY')"` has `python3` at the head of its own span precisely because `(`
    ended the previous one. Rewriting the split to suit the pairing would trade
    this silent pass for that one; running the pairing over BOTH readings trades
    nothing, since a verdict is an OR and a command already reported stays
    reported.

    Empty when the text holds no word-level substitution, which is nearly every
    unit in the corpus — the flatten is one pass and the split it feeds is not
    paid at all where there is nothing to flatten. MEASURED on the same terms
    the widenings above are: across every markdown and workflow line in the tree
    it rejoins 42 of them and changes ZERO verdicts, the offences it is written
    for being shapes no document here currently writes.
    """
    flat = _flatten_substitutions(text)
    return [] if flat == text else _commands(flat)


# Words that mean "the command word is the NEXT one". `/usr/bin/env python3` is
# the standard portable spelling of an interpreter invocation, and read as a
# command word `env` is not an interpreter — so a heredoc fed to it opened
# nothing and its body was never audited. Its own options and `VAR=value`
# arguments are consumed with it; an assignment left behind is stripped by
# ASSIGNMENTS on the next pass anyway.
#
# The list is CLOSED, and each member means exactly this and nothing else: `env`
# execs its argument, `command` bypasses functions and builtins to exec it,
# `exec` replaces the shell with it. `xargs` is deliberately ABSENT although it
# also runs its argument — it builds argv from stdin, so a heredoc on that
# command is consumed by xargs and never reaches the interpreter, and unwrapping
# it would claim the opposite. Adding a fourth is a decision about what "runs
# this body" means, not a widening.
#
# An option's OPERAND is consumed with it, and `(?:-\S+\s+)*` alone did not: it
# ate `env -u ` and left `PYTHONPATH` standing where the command word goes, so
# `env -u PYTHONPATH python3 - <<'PY'` opened no heredoc and its body was never
# read. That is the silent direction, and it is the same defect as the two
# prefixes below seen from inside a prefix this file already had — an option is
# not always one token, and a regex that assumes it is hands back an ARGUMENT and
# calls it an executable.
#
# The operand-taking set is CLOSED for the same reason the words above are, and
# the closure is what keeps the widening safe: consuming a token that is NOT an
# operand would swallow the interpreter itself and clear the command. `-u`/
# `--unset` and `-C`/`--chdir` are env's, `-a` is exec's, and the `=` spellings
# need no arm — `--unset=X` is one token and the plain option branch takes it.
# `-S`/`--split-string` is absent from that set although it takes an operand:
# that operand IS the command (`env -S "python3 -x"`), so consuming it would hide
# the very interpreter this function exists to find. DETACHED, leaving it to the
# plain branch is enough — `-S ` is eaten as an option and the quoted word is the
# head, where `INTERPRETER` still reads it.
#
# ATTACHED it needs an arm of its own, and not having one was a silent pass on a
# spelling `env` documents and macOS accepts. `-\S+` is greedy to the next SPACE,
# so `env -S'python3 -m agents.lib.ptet'` handed the option branch `-S'python3`,
# then `-m`, and left `agents.lib.ptet'` standing where the command word goes:
# an engine target with no interpreter in front of it, cleared by every audit
# here. The arm consumes the option and its opening QUOTE and stops, so the head
# starts at the command the operand holds. It sits FIRST because the generic
# branch would otherwise take the whole token, and it cannot swallow a detached
# operand: `['\"]?` matches no space, so `-S 'python3 ...'` still falls to the
# plain branch and reads exactly as it did.
EXEC_PREFIX = re.compile(
    r"^(?:\S*/)?(?:env|command|exec)\s+"
    r"(?:(?:-[uCa]|--(?:unset|chdir))\s+\S+\s+"
    r"|(?:-S|--split-string=)['\"]?"
    r"|-\S+\s+)*")

# The same position filled by an ordinary PROGRAM rather than by shell grammar.
# Each of these execs the rest of its argv after its own options, so
# `sudo python3 - <<'PY'` and `timeout 60 python3 - <<'PY'` run python — and read
# as a command word `sudo` and `timeout` are neither an interpreter nor the
# launcher, so the heredoc scanner counted zero heredocs and never opened the
# body. That is EXEC_PREFIX's silent pass in a second vocabulary, and the one a
# runbook is likeliest to write: a documented command that has to be run as root,
# under a time limit, or detached.
#
# CLOSED, like the three above, and every member passes its stdin THROUGH to the
# command it execs — which is the whole of what makes unwrapping it true. `xargs`
# stays out for exactly that reason: it CONSUMES stdin to build argv, so the
# interpreter it runs never sees the heredoc, and the same argument excludes
# anything else that reads its input.
#
# Spelled PER WORD rather than as one merged option class, and that is the whole
# care in it: which short options take an operand differs between them, and the
# sets COLLIDE. `sudo -i` takes none while `stdbuf -i MODE` takes one; `timeout
# -s SIG` takes one while `sudo -s` takes none. Merged, every reading is wrong
# for some member — and both ways of being wrong are SILENT here, which is what
# rules out the usual "prefer the safe direction" escape. Consume an operand that
# is not one and the interpreter itself is swallowed, so the head lands past it;
# leave a real operand standing and the head is `alice`. Neither is an
# interpreter, and a head that is not an interpreter CLEARS the command.
#
# `timeout` alone requires its DURATION, an operand that is not an option at all.
# Written as a plain word it would be handed back as the executable; required, it
# also keeps `timeout` in prose from unwrapping anything, since English does not
# follow the word with a number and a command.
#
# The venv RUNNERS fill the same position with a SUBCOMMAND. `uv run python3 -
# <<'PY'` is what a modern runbook writes where it used to write the bare
# interpreter, and `pipenv`, `poetry`, `pdm`, `hatch` and `rye` spell it the
# same way; each execs the rest of its argv inside the project environment and
# passes stdin through, which is the property that admits it here and the only
# one. Read as a command word `uv` is neither an interpreter nor the launcher,
# so the scanner counted zero heredocs and never opened the body — `sudo`'s
# silent pass in the vocabulary that replaced it. `conda run` stays out for the
# reason `xargs` does: it CAPTURES the child's streams by default, so the body
# the shell wrote is not the body the interpreter reads. `tox` and `nox` select
# a session rather than exec an argv and are out too.
#
# Their option arm is GENERIC where the POSIX members' are enumerated, and the
# asymmetry is the point: these are third-party CLIs whose option sets move, so
# an enumeration is a guess that goes wrong the next time uv adds a flag —
# silently, and in whichever direction the guess was wrong. A generic arm can
# only stop EARLY (`uv run --with pandas python3 -` leaves `pandas` as the head,
# which is not an interpreter, so the body stays unread exactly as it does
# today) and the lookahead is what stops it running LONG: an option word that
# names an interpreter, `--python=python3.12`, is left standing where
# `BARE_ENGINE_CALL` still reads it, so this widening cannot turn a report into
# a clear. A version alone — `--python=3.12` — carries no interpreter to lose
# and is consumed, which keeps the routed command behind it reading as routed.
#
# What no arm here reaches is `uv run agents/lib/ptet.py`: a runner handed an
# engine module with no interpreter word anywhere on the command. That is a
# different class — execution by PATH, which `./agents/lib/ptet.py` and
# `xargs agents/lib/ptet.py` share — and `BARE_ENGINE_CALL`'s "interpreter …
# target" shape is the audit saying it does not cover it. Named rather than
# half-closed: a rule that fires on a bare engine path fires on every engine
# path in prose, and there are hundreds.
WRAPPER_PREFIX = re.compile(
    r"^(?:\S*/)?(?:"
    r"sudo\s+(?:(?:-[ugpChrtDR]|--(?:user|group|prompt|close-from|host|role"
    r"|type|chdir|chroot))[=\s]+\S+\s+|-\S+\s+)*"
    r"|nohup\s+"
    r"|nice\s+(?:(?:-n|--adjustment)[=\s]+\S+\s+|-\S+\s+)*"
    r"|timeout\s+(?:(?:-k|-s|--(?:kill-after|signal))[=\s]+\S+\s+|-\S+\s+)*"
    r"\d+(?:\.\d+)?[smhd]?\s+"
    r"|stdbuf\s+(?:(?:-[ioe]|--(?:input|output|error))[=\s]+\S+\s+|-\S+\s+)*"
    r"|setsid\s+(?:-\S+\s+)*"
    r"|ionice\s+(?:(?:-[cnp]|--(?:class|classdata|pid))[=\s]+\S+\s+|-\S+\s+)*"
    r"|(?:uv|pipenv|poetry|pdm|hatch|rye)\s+run\s+"
    r"(?:-(?!\S*python3)\S+\s+)*"
    r")")

# Shell RESERVED WORDS, which may stand where a command word is expected without
# being one. `if python3 - <<'PY'` is ordinary shell and runs python; read as a
# command word, `if` is neither an interpreter nor the launcher, so the heredoc
# scanner classified the opener as non-python, counted zero heredocs and never
# read the body. The unit audit cannot cover for it either — the interpreter is
# on the opener and the engine import is in the body, and neither line holds
# both — so a bare engine call behind any of these words passed BOTH audits.
#
# Closed, like EXEC_PREFIX, and for the same reason: each word here is one the
# shell PARSES away before the command word, never a program. `then`, `else`,
# `do` and `elif` are included because they follow a separator and so open a
# command of their own; `(` is not, being a SEPARATOR already, which makes a
# subshell its own command before this is ever asked. `fi`, `done` and `esac`
# CLOSE a construct rather than preceding a command and are deliberately out.
#
# This one is noisy on prose in a way the exec words are not — English sentences
# begin "if", "while", "do not", "else" — and that was MEASURED before adopting
# it: across every markdown unit in the tree it moves 375 command heads and
# changes ZERO verdicts, because a verdict also needs an engine call, and the
# heredoc verdict also needs a shell-active `<<`. Noise that cannot reach a
# verdict is not a false alarm, and the direction it is wrong in if it ever does
# is the visible one.
# `time` is the one member that takes an OPTION. `time -p python3 - <<'PY'` is
# ordinary shell — `-p` is the POSIX output format and the only option bash's
# `time` has — and stripping the word alone handed back `-p` as the executable,
# which is not an interpreter, so the heredoc was never counted and its body
# never read. The same silent pass EXEC_PREFIX's operand arm was written for,
# one prefix class along: an option is not always the last thing before the
# command word.
# ...and `function` is the other half of a construct this list already covers
# halfway. bash spells a definition two ways, `run() { ... }` and `function run
# { ... }`, and only the first arrives here in a shape anything strips: the `(`
# splits it and `LABEL_PREFIX` below eats the `)`. The keyword form has no
# parenthesis to split at, so `function` stood as the executable — not an
# interpreter, so the heredoc on it was neither counted nor scanned, and an
# engine import in its body stayed green. The NAME goes with the keyword, since
# a definition is `function` plus one word before the body opens, and `{` is
# already a member here to take the brace on the next pass.
# `coproc`, `case` and `select` join the list for the reason every other member
# is on it: each stands where a command word is expected and is not one, so a
# heredoc opened after it registered as no heredoc at all and its body was never
# read. `coproc python3 - <<'PY'` is the plain form and `coproc NAME { python3 -
# <<'PY'` the named one — the `{` is already here, so consuming the optional
# name is all the second needs. That name is taken only when a `{` FOLLOWS it,
# which is bash's own rule and not a nicety: `coproc\s+\w+` swallows the
# interpreter of the plain form, and a head of `- <<'PY'` names no interpreter
# at all, so widening the prefix would have re-created the silence it was added
# to remove.
#
# `case` and `select` are matched WITH their `in`, which is what keeps them off
# prose: the word alone is an ordinary English one and this pattern runs over
# markdown. What follows is the arm LABEL, which `LABEL_PREFIX` already strips —
# but only once something has consumed the `case $x in` in front of it, which is
# why an arm written inline (`case $x in yes) python3 - <<'PY'`) went unread
# while the same arm on a line of its own was caught.
KEYWORD_PREFIX = re.compile(
    r"^(?:!|time(?:\s+-p)?|if|elif|then|else|while|until|do|\{"
    r"|coproc(?:\s+[A-Za-z_]\w*(?=\s+\{))?"
    r"|(?:case|select)\s+[^\s;]+\s+in"
    r"|function\s+[^\s(){}]+)\s+")

# The remains of a compound-command LABEL, which stands where a command word is
# expected and is not one. `case` writes its arm as `yes) python3 - <<'PY'`, and
# a function definition as `run() { python3 - <<'PY'` — `_commands` has already
# cut that one at its `(`, so both arrive here as a single token ending in `)`.
# Read as a command word that token is neither an interpreter nor the launcher,
# so the heredoc scanner counted zero heredocs and never opened the body: the
# same silent pass KEYWORD_PREFIX was added for, one construct along. The unit
# audit cannot cover for it either, the interpreter being on the opener and the
# import in the body.
#
# `(` is excluded from the label, so this cannot reach across a subshell the
# splitter has already honoured, and the label is ONE token — `the launcher
# invokes) and ...`, prose left over from a `(` split, keeps its head. What
# remains is noisy on prose the way KEYWORD_PREFIX is, an enumerated `a)`
# wearing exactly the shape of a case arm, and it was MEASURED on the same terms
# before adoption: across every markdown and workflow unit in the tree it moves
# 3546 more command heads and changes ZERO verdicts. The direction it is
# wrong in, if it ever reaches one, is the visible one.
LABEL_PREFIX = re.compile(r"^[^()\s]*\)\s*")

# A leading REDIRECTION, which stands where a command word is expected and is not
# one. `[assignments] [redirections] word...` is the shell's own grammar for a
# simple command, so `2>/dev/null python3 - <<'PY'` runs python — but read as a
# command word `2>/dev/null` is neither an interpreter nor the launcher, so the
# heredoc scanner counted zero heredocs and never opened the body. That is the
# silent pass KEYWORD_PREFIX and LABEL_PREFIX were each added for, arriving at
# the one position in that grammar this file had no rule for at all.
#
# `<<` is excluded by lookahead and the exclusion is load-bearing: it is the
# heredoc OPERATOR, and consuming it here would eat the delimiter the scanner
# keys on. `<<<` goes with it, being one `<` further along.
#
# The TARGET must be ATTACHED to the operator and shaped like a path, a variable
# or a descriptor. Neither bound is grammar — POSIX allows `> file cmd` with a
# space, and any word as the target — and both are about the corpus this runs
# over, which is markdown. MEASURED across every markdown and workflow unit in
# the tree: the grammar-faithful spelling moves 140 command heads, 126 of them
# BLOCKQUOTES, `> ` at the start of a line being markdown's quote marker and the
# one class most likely to quote a command, which is how prose noise reaches a
# verdict. Requiring attachment leaves 14, all prose; requiring a path-shaped
# target as well leaves 2, both prose comparing numbers. ZERO verdicts change
# under any of the three, so the choice is about which noise to invite rather
# than about a result — and a space after the operator is a spelling no document
# writes, while `> ` at a line start is one every document writes.
REDIRECT_PREFIX = re.compile(
    r"^(?:\d*[<>]&[0-9-]+"
    r"|(?:&>>?|\d*(?:>>|>\||<>|>|<))(?!<)[/.~$A-Za-z_-][^\s<>|&;()]*)\s+")

# The grammar-faithful spelling of the same rule, for FENCED text only. POSIX
# allows a space between the operator and its target — `> out.txt python3 -
# <<'PY'` is one command that runs python — and the attachment requirement above
# is not grammar: it is a decision about MARKDOWN, where `> ` at the start of a
# line is a blockquote, the 126 of 140 moved heads the measurement found, and the
# one class most likely to quote a command. Inside a fence there are no
# blockquotes, so the noise the requirement buys does not exist there and the
# hole it leaves does. It is the same corpus split the opener patterns make.
#
# It does NOT reach the case the bound was written around: `> python3 - <<'PY'`
# consumes `> python3 ` and hands back `- <<'PY'`, whose head is `-` and not an
# interpreter, so a quoted opener stays unread whichever corpus it sits in.
#
# The TARGET may be quoted, which the path-shaped class excluded: `2>"$LOG"
# python3 - <<'PY'` is one command that runs python, and with the redirection
# left standing as the apparent executable the opener was never recognised. A
# quoted word is admitted here and not in the prose form above for the reason
# the space is — inside a fence the text is shell, and a quote is how shell
# spells a word with a space in it.
FENCED_REDIRECT_PREFIX = re.compile(
    r"^(?:\d*[<>]&[0-9-]+"
    r"|(?:&>>?|\d*(?:>>|>\||<>|>|<))(?!<)\s*"
    r"(?:'[^']*'|\"[^\"]*\"|[/.~$A-Za-z_-][^\s<>|&;()]*))\s+")


def _command_head(command, fenced=False):
    """`command` positioned at the word the shell would RUN.

    Leading assignments removed and the words the shell parses away with them,
    so what comes back STARTS with the executable. The four prefix rules are the
    shell's own grammar for what may precede a command word — assignments,
    redirections, reserved words, and the exec words that hand execution on —
    plus the compound-command LABEL that `_commands` leaves behind. Naming the
    grammar is what makes the list auditable: a position missing from it is a
    silent pass, and REDIRECT_PREFIX was one for as long as the other four
    existed. Everything else about routing
    follows from that one position, and asking anything weaker has now been wrong
    four times in a row: a launcher named in a comment, in a `-c` body, in an
    environment VALUE, and — the pair that produced this function — as an
    ARGUMENT. `echo python3 <<'PY'` names an interpreter it does not run, and
    was reported for every import in the body it feeds to `echo`; `python3 -
    sh agents/lib/run.sh <<'PY'` runs bare python with the launcher sitting in
    argv, and was cleared. A token search cannot tell an executable from an
    argument, because the difference is POSITION.

    It also collapses the two routes the scanner used to clear the launcher form.
    `sh agents/lib/run.sh - <<'PY'` was never an opener at all — the rule was
    about invoking an INTERPRETER and `run.sh` is not one — so its body went
    unscanned rather than cleared, and the `routed` branch could only be reached
    by the contrived `TAXPY=python3 <launcher>` spelling written to reach it. Now
    the launcher IS an opener, and the branch that clears it is the ordinary one.

    A command SUBSTITUTION is not unwrapped here, and a rule that did was the
    first answer to `echo "$(python3 - <<'PY')"` — a `$( ... )` that is not the
    leading token, whose head is `echo` and whose body therefore went unread.
    The rule matched only the first token, so widening it to any position is the
    obvious repair and it is wrong in the silent direction: this function returns
    ONE position, so entering the substitution hands back the inner command and
    discards the outer one, and `python3 -m agents.lib.ptet "$(date)"` becomes a
    `date)"` that calls no engine. A line holding a substitution holds two
    commands. Splitting it is `_commands`' job, and `_inert` was what stopped it
    inside a double quote — so that is where it is fixed, and there is no rule
    here to widen.

    Bounded by PROGRESS rather than by a count. It was `range(4)`, chosen when
    assignments and the exec words were the whole list, and `while ! env VAR=x
    python3` is four prefixes deep — one more word and the loop returned a head
    that is still a prefix, which reads as "not an interpreter" and CLEARS the
    command. Raising the constant only moves where that happens, and nothing
    tests a margin, so there is no constant now: every unwrapping removes at
    least one character, so the loop ends, and the guard says that in the code
    instead of leaving a reader to derive it from three regexes. An unbounded
    loop over text that may not be a command at all is how a linear scan becomes
    a hang — that is what the guard is for, not the depth.

    `fenced` says which corpus this text came out of, and it widens exactly one
    rule — see `FENCED_REDIRECT_PREFIX`. Defaulted to the PROSE reading, because
    that is the conservative one about markdown and because every caller that
    knows the answer passes it: a default that guessed "fenced" would apply shell
    grammar to sentences. `WRAPPER_PREFIX` needs no such flag — `sudo` is not a
    word markdown gives a second meaning to.
    """
    text = command.strip()
    redirect = FENCED_REDIRECT_PREFIX if fenced else REDIRECT_PREFIX
    while True:
        after = ASSIGNMENTS.sub("", text).lstrip()
        if after == text:
            m = (redirect.match(text) or KEYWORD_PREFIX.match(text)
                 or LABEL_PREFIX.match(text) or EXEC_PREFIX.match(text)
                 or WRAPPER_PREFIX.match(text))
            if not m:
                return text
            after = text[m.end():].lstrip()
        if len(after) >= len(text):
            return text                 # no progress: stop rather than spin
        text = after


def _blank_inert(text, only="") -> str:
    """`text` with inert characters replaced by NUL, so a pattern cannot match
    across them.

    Blanked rather than cut, so a literal cannot be assembled out of the text on
    either side of a quoted gap.

    `only` narrows it to the characters named, which the heredoc scanner needs
    and nothing else does: blanking a whole quoted region would destroy the
    DELIMITER, `<<'PY'` being quoted as a matter of heredoc syntax rather than of
    hiding anything. Masking only the `<` leaves `'PY'` readable while a `<<`
    inside `echo 'python3 - <<PY'` — which redirects nothing, the line printing a
    string — stops being an opener.
    """
    flags = _inert(text)
    return "".join("\0" if flag and (not only or ch in only) else ch
                   for ch, flag in zip(text, flags))


def _blank_comments(text, only="") -> str:
    """`text` with shell COMMENTS replaced by NUL, on `_blank_inert`'s terms.

    Separate from `_inert` on purpose. Inert asks where the shell reads no
    SYNTAX, and the heredoc scanner is the only caller that needs a comment to be
    such a place: it picks the LAST `<<` on the command, and a `<<` the shell
    never reads is not a candidate. Folding comments into `_inert` would also
    hand them to `_commands`, which splits PROSE as well as fenced shell — and
    every markdown heading opens with `#`, so half of every document would stop
    being scanned. That is the fail-open direction, and this fix is for one.

    A `#` opens a comment only at the start of a WORD and only outside quotes:
    `echo a#b` prints `a#b`, `'#'` is a character, and `cmd; #x` is a comment
    because a separator ends the word before it.

    Masking can only REMOVE candidates, never add one, so it cannot turn text
    into an opener — the direction that would indict a document for a command it
    does not run.
    """
    flags = _inert(text)
    out, i = list(text), 0
    while i < len(text):
        if (not flags[i] and text[i] == "#"
                and (i == 0 or text[i - 1].isspace() or text[i - 1] in ";|&(")):
            while i < len(text) and text[i] != "\n":
                if not only or text[i] in only:
                    out[i] = "\0"
                i += 1
        i += 1
    return "".join(out)


# Names that turn a STRING into source. A heredoc body that calls one of these
# is a body where a quoted engine import really does execute, so the blanking
# below stops at comments when one is present.
#
# CLOSED, and the first three take SOURCE TEXT. The two loaders take a module
# NAME instead, and were excluded on the ground that nothing here could read one
# — true while the target pattern was `ENGINE_IMPORT` alone, and the reason
# `python3 -c '__import__("agents.lib.ptet")'` was clean: the mask took the
# literal and no pattern wanted it back. `ENGINE_LOADER` reads that form now, so
# the exclusion had become the whole of the hole rather than a limit on the
# guard.
#
# The call has to be a BARE one, and `\b` was not that. `re.compile(` matched,
# so any body that builds a regex had every string literal in it read raw — and
# a body that builds a regex is what half this file's own examples look like. The
# consequence is the cry-wolf direction and the one this file has now written
# six times: `print("never: from agents.lib import ptet")` beside a
# `re.compile(...)` was reported as the engine reached from a bare interpreter,
# on a document quoting the rule correctly. `str.compile` and `self.eval` are
# excluded by the same character.
#
# `builtins.`, `importlib.` and `runpy.` are the dotted spellings admitted,
# because each is the SAME function under its module: excluding them would blank
# the literals of a body that really does execute one, which is the silent
# direction this pattern exists to hold shut. `import_module`, `run_module` and
# `run_path` are admitted bare as well, `from importlib import import_module`
# being the ordinary way to reach the first.
#
# ...and the IMPORT of either loader module is a member in its own right, which
# is the one arm here that is not a call. An ALIAS leaves nothing at the call
# site to recognise — `from importlib import import_module as im` then
# `im("agents.lib.tie_out")` loads the engine through a name this pattern can
# never enumerate — so the question is asked one statement earlier, where the
# body says which machinery it is reaching for. It is the same all-or-nothing
# trade the paragraph above makes and it costs the same thing: a body that
# imports `importlib` or `runpy` AND carries an unrelated literal quoting the
# rule is reported. Loud, rare, and visible in the message; the alternative is a
# loader reached under a name and read as prose.
#
# ALL-OR-NOTHING per body, and that is a refusal rather than an oversight. The
# narrower rule — keep only the literal PASSED to the dynamic call and mask the
# rest — is the one a reviewer asks for, and it cannot be written from the call
# site: `code = "from agents.lib import ptet"` followed by `exec(code)` puts the
# source at an ASSIGNMENT, which a rule reading arguments masks and a rule
# reading dataflow is an interpreter. That miss is silent, on the very construct
# `_bare_engine_units` exists for one language over. What the coarse rule costs
# is a body that calls any member AND carries an unrelated literal quoting the
# rule — loud, rare, and visible in the message. The trade runs the way this
# file's do. The two loaders take a NAME rather than source, so the same
# assignment bound is theirs too: `ENGINE_LOADER` reads the literal only where
# it sits IN the call.
DYNAMIC_EXEC = re.compile(
    r"(?<![\w.])(?:builtins\.)?(?:exec|eval|compile)\s*\("
    r"|(?<![\w.])(?:__import__|importlib\s*\.\s*import_module|import_module"
    r"|runpy\s*\.\s*run_(?:module|path)|run_module|run_path)\s*\("
    r"|(?<![\w.])(?:from\s+(?:importlib|runpy)\s+import\b"
    r"|import\s+(?:importlib|runpy)\b)")


def _blank_python(body):
    """`body` lines with python COMMENTS and STRING literals replaced by NUL.

    The heredoc body scan reads text the INTERPRETER runs, and a raw search over
    it reported an engine import that python executes no more than the shell
    executes `echo \'python3 - <<PY\'`. `# from agents.lib import ptet` and
    `print("from agents.lib import ptet")` were both offenders — a gate firing
    on a document that quotes the rule accurately, which this file has now
    written five times, and the failure mode that teaches a reader to reason
    past a gate rather than read it.

    TOKENIZED rather than pattern-matched, because a comment and a string are
    exactly what python\'s own lexer says they are and nothing weaker gets `#`
    inside a literal, a literal spanning three lines, or an f-string right. It
    is stdlib and it is the same lexer that would run the body.

    A body the tokenizer REFUSES is read raw — an unterminated literal, a
    fragment with an ellipsis where code should be — which is the fail-closed
    direction and exactly today\'s behaviour, so a template that is not python
    loses nothing it had.

    LENGTHS are preserved, never cut, for the reason `_blank_inert` blanks
    rather than cuts: the caller joins continuation lines by column, so a masked
    line has to sit where the original did.
    """
    # `exec("from agents.lib import ptet")` is a bare engine call wearing a
    # string, and blanking strings unconditionally would clear it. The three
    # names are the only way a literal in this body becomes code, so their
    # ABSENCE is what licenses the blanking — and absence is asked of the CODE,
    # never of the raw text. A body whose only `exec(` sits in a comment or a
    # literal executes nothing, and searching the text read it as one that does:
    # every string in the body then survived the mask, so an unrelated
    # `print("from agents.lib import ptet")` beside a sentence ABOUT `exec(code)`
    # was reported as the engine on a bare interpreter. Two passes over the same
    # lexer rather than one, because the question the first answers is what the
    # second needs.
    literal = {tokenize.COMMENT, tokenize.STRING}
    # 3.12 split f-strings into their own tokens, so the literal text of one
    # stopped being a STRING. Read as absent on that interpreter the mask would
    # simply not cover f-strings — a version-dependent verdict, which is worse
    # than either answer.
    #
    # 3.14's t-string is the same token split arriving a second time, and it is
    # the version-dependent verdict this comment was already about: on 3.12
    # `t"..."` lexes as NAME + STRING and the mask covers it, on 3.14 it lexes as
    # TSTRING_MIDDLE and the mask did not — so the same document was clean on one
    # CI leg and an offender on the other, and this suite runs both. Named as a
    # LIST rather than one lookup because the next split will arrive the same
    # way, and a `getattr` per name is what keeps a module that has neither from
    # raising.
    for name in ("FSTRING_MIDDLE", "TSTRING_MIDDLE"):
        middle = getattr(tokenize, name, None)
        if middle is not None:
            literal.add(middle)
    try:
        masked = _mask_python(body, literal)
        if DYNAMIC_EXEC.search("\n".join(masked)):
            return _mask_python(body, {tokenize.COMMENT})
        return masked
    except (tokenize.TokenError, SyntaxError, ValueError):
        return list(body)               # not python: read it as it stands


def _mask_python(body, kinds):
    """`body` with every token of a type in `kinds` replaced by NUL.

    Raises whatever the tokenizer raises; the caller decides what a body that is
    not python means.
    """
    out = [list(line) for line in body]
    for tok in tokenize.generate_tokens(
            io.StringIO("\n".join(body) + "\n").readline):
        if tok.type not in kinds:
            continue
        (first, lo), (last, hi) = tok.start, tok.end
        for row in range(first, min(last, len(out)) + 1):
            chars = out[row - 1]
            begin = lo if row == first else 0
            stop = hi if row == last else len(chars)
            for col in range(begin, min(stop, len(chars))):
                chars[col] = "\0"
    return ["".join(chars) for chars in out]


# The python an interpreter runs from ARGV rather than from stdin. Same text and
# the same interpreter as a here-document, read two different ways: the body fed
# in on stdin was tokenized before being searched and the inline one was searched
# raw, so `python3 -c 'print("from agents.lib import ptet")'` was an offender and
# the heredoc spelling of it was not.
#
# The operand is taken as the shell hands it over — single quotes have no
# escapes, double quotes do — and an UNQUOTED or unterminated one matches
# nothing and is read raw, which is the fail-closed direction.
#
# A short-option CLUSTER carries the `c`, and requiring it to stand alone put
# this one letter out of step with `_interpreter_role`, which has read a cluster
# whole since it was written. `python3 -uc '...'` — the unbuffered spelling a
# runbook writes when the output goes through `tee` — was a command with no
# masked operand, so a document quoting the rule inside one was reported for the
# string it prints. That is the cry-wolf direction, and the two audits disagreed
# about one command over one character. The cluster must END at the `c`, which
# is python's own reading: after `-c` the remainder of the argument is the
# program, so `-cu 'x'` runs `u` and is not this shape.
#
# `INLINE_C`, not a second copy of it — see there. The cluster rule was written
# here first and `INLINE_PY` was left on the detached spelling, so the audit that
# decides a command HAS an inline body and the mask that reads what is IN it
# parted company over one character.
INLINE_SCRIPT = re.compile(
    INLINE_C + r"""(?:'(?P<sq>[^']*)'|"(?P<dq>(?:\\.|[^"\\])*)")""")


def _blank_inline_script(head):
    """`head` with the python inside a `-c` operand masked by `_blank_python`.

    `python3 -c 'print("from agents.lib import ptet")'` reaches the engine no
    more than `# from agents.lib import ptet` does, and it was reported — the
    seventh time this file has written that sentence, and the first where the fix
    is a POSITION the sibling path already reads correctly rather than a new
    rule. A body on stdin and a body in argv are the same text; the reason they
    got different answers is that only one of them was ever handed to a lexer.

    Rebuilt rather than spliced in place, because a double-quoted operand carries
    SHELL escapes python never sees and removing them changes the length. What
    comes back is fed to a `search` and to nothing that cares where a character
    sits, which is why this may do what `_blank_inert` and `_blank_python` — both
    read by column — may not.

    `_blank_python`'s guards carry over intact and are the whole of the safety
    argument: a body calling `exec`, `eval` or `compile` keeps its string
    literals, and a body the tokenizer refuses comes back as it stands. Both hold
    the silent direction shut, and the second is what covers an operand that is
    shell rather than python.

    Applied only where the `-c` is PYTHON'S, which is the position rule this file
    keeps re-learning. `-c` is a shell option too, and masking it as a python body
    read `sh -c 'python3 -c "from agents.lib import ptet"'` — a nested
    interpreter reaching the engine — as `sh -c` with the whole inner command
    blanked: no engine target left, cleared, while the `-m` spelling of the same
    line was reported. The head STARTS at the word the shell runs, so the
    question is answerable here and costs one match: only an interpreter's own
    operand is python, and a `-c` belonging to anything else is shell text that
    stays exactly as written. Left standing it can only ADD an offence, which is
    the direction a masking rule has to be wrong in.
    """
    word = re.match(r"\S+", head.lstrip())
    if not (word and INTERPRETER.search(word.group())):
        return head

    def mask(m):
        body = m.group("sq")
        if body is None:
            body = re.sub(r"\\(.)", r"\1", m.group("dq"))
        return "-c '" + "\n".join(_blank_python(body.split("\n"))) + "'"
    return INLINE_SCRIPT.sub(mask, head)


# Commands that copy stdin to stdout, so a heredoc fed to one arrives wherever
# the PIPELINE sends it next. `cat <<'PY' | python3 -` is the canonical way to
# write a here-document into an interpreter, and read as a command owned by `cat`
# it opened nothing at all: zero heredocs seen, the body never scanned, an engine
# import in it clean away. That is the silent direction, on the spelling a
# runbook is most likely to use.
#
# CLOSED, and the membership test is one thing: does this command write its INPUT
# on unchanged. `cat` does with no file operand and `tee` does with any. A filter
# that TRANSFORMS — `sed`, `grep`, `head` — is deliberately out: what reaches the
# interpreter is not what the body says, so forwarding the verdict through one
# would be a guess, and a guess in the accusing direction. `xargs` is out for the
# reason it is not an exec prefix either — it consumes stdin to build argv, so
# the body reaches no interpreter at all.
PASSTHROUGH = re.compile(r"^(?:\S*/)?(?:cat(?:\s+-\S*)*|tee(?:\s+\S+)*)\s*$")


# Every redirection a command word may carry, operand included. Removed rather
# than cut AT, which is what this was: `re.split(r"[<>]", head, 1)[0]` on
# `cat 2>/dev/null <<'PY'` hands back `cat 2`, no longer a pass-through, so the
# pipe was not followed and a body reaching a bare interpreter went unread. A
# descriptor is part of the OPERATOR, and cutting at the angle bracket leaves it
# behind as an argument.
REDIRECTION = re.compile(
    r"\s*(?:\d*(?:>&|<&)[0-9-]+"
    r"|&>>?|\d*(?:<<-?|>>|>\||<>|>|<))\s*[^\s<>|&;()]*")


def _passes_through(head):
    """Does `head` write its stdin on unchanged?

    Redirections removed before asking, because the heredoc itself is part of
    the command: `cat <<'PY'` read whole is `cat` with an argument that is not a
    file, so the strict test refused it and the pipe form it exists to catch
    stayed invisible.

    Strict about the OPERANDS either side of that cut, which is what keeps the
    claim true rather than convenient. `cat file.txt <<'PY'` reads the FILE and
    ignores stdin, so the body reaches nothing downstream — forwarding a verdict
    through it would be an accusation about a command that never sees the text.
    `tee` takes file operands and still writes stdout, so it keeps them.

    A MASKED region is whitespace here, never an operand. `_heredoc_written`
    hands over a comment-blanked head, `PASSTHROUGH` ends anchored on trailing
    WHITESPACE, and NUL is
    not a space — so `cat > gen.py <<'PY'  # generator` stopped being a
    pass-through, the generated-script pair was never recorded, and an
    engine-importing script run on a bare interpreter was cleared by ADDING a
    comment to the line that writes it. `tee` did not have the bug, its operand
    arm eating the NULs, so the two members of one closed class disagreed about
    the same line. A comment at the end of a command is exactly whitespace as far
    as the command goes, which is what this replacement says.
    """
    return bool(PASSTHROUGH.match(
        REDIRECTION.sub(" ", head).replace("\0", " ")))


def _command_index(text, at):
    """Which command of `text` the offset `at` falls in."""
    return next((k for k, (a, b, _) in enumerate(_command_spans(text))
                 if a <= at < b), 0)


def _substitution_start(text, at, floor):
    """Where the command holding the offset `at` begins, once a backtick
    substitution around it is entered.

    `$( ... )` needs no such rule — `(` is a SEPARATOR, so `_command_spans` has
    already made the substitution its own command and `echo "$(python3 -
    <<'PY')"` arrives with `python3` at the head. The backtick spelling gets
    none of that: it carries no bracket, so the span is the whole line, the head
    is `echo`, and the body of a heredoc the shell really does hand to python
    went unread. Putting a backtick in SEPARATOR was the obvious repair and is
    the wrong one, MEASURED: it moves 6244 command splits across the tree and
    turns `python3 -c "# writes `result.json`" ... from agents.lib import x`,
    which this suite pins as an offence, into three fragments that name no
    engine — one silent pass traded for a wider one.

    So the entry is made HERE, where the question is asked, and only for the
    scanner that asks it. The rule comes off `_inert` rather than from a second
    walk of the text: an ACTIVE backtick is exactly a substitution delimiter —
    escaped, single-quoted and otherwise literal ones are marked inert there —
    and they alternate, so an odd count before `at` means `at` sits inside one.

    `floor` is the span start and it WINS when it is later, which is the case a
    separator inside the substitution makes: ``out="`a; python3 - <<'PY'`` is
    split at the `;` by the ordinary rule, and the command that runs the body is
    the one after it, not the one the backtick opened.
    """
    flags = _inert(text)
    opens = [i for i, ch in enumerate(text)
             if ch == "`" and not flags[i] and i < at]
    return max(floor, opens[-1] + 1) if len(opens) % 2 else floor


def _heredoc_runner(text, at, fenced=False):
    """The command in `text` that RUNS the body of the heredoc opened at `at`.

    Routing is a property of a command, never of the line it sits on, and the
    two audits in this file ask that question about different things. The
    activation audit asks it of an INVOCATION, which is `_unactivated`'s
    per-command pairing. A heredoc opener asks it of a REDIRECTION, and needs
    its own answer: the body's engine imports belong to whichever command
    carries the `<<` — not to whichever carries the interpreter.

    `python3 -c '...' || {launcher} - <<PY` is the form that makes the two
    questions come apart: the interpreter and the delimiter sit in DIFFERENT
    commands, and it is the launcher's that runs the body. Keyed on a bare
    interpreter this reads as unrouted, which is how the delimiter got to be the
    key. No test here separates the two, and the note is the reason why:
    `RUNS_PYTHON` counts `run.sh` as an interpreter, so on every form the skills
    actually write the two keys agree, and only a contrivance like `{launcher}
    -c x | cat <<PY` pulls them apart. The delimiter is kept because it is the
    right question, not because a control can currently see the difference.

    By OFFSET and not by "the last command holding a `<<`", because a line may
    hold two heredocs and the last one is not the one being asked about — see
    `_heredoc_openers`, which asks about each in turn.

    Then the delimiter's owner is not always the runner. A pass-through hands its
    stdin to the next stage, so the walk continues down the PIPE for as long as
    each stage forwards its input, and stops at anything else: a `&&` is not a
    pipe, and a stage that is not a pass-through consumes the body itself.
    """
    spans = _command_spans(text)
    here = next((k for k, (a, b, _) in enumerate(spans) if a <= at < b), None)
    if here is None:
        return text
    head = _command_head(
        text[_substitution_start(text, at, spans[here][0]):spans[here][1]],
        fenced)
    while _passes_through(head) and here + 1 < len(spans):
        a, b, sep = spans[here + 1]
        if sep not in ("|", "|&"):
            break
        here += 1
        head = _command_head(text[a:b], fenced)
    return head


def _script_path(word):
    """A shell pathname word, normalised so two spellings of one file compare
    equal.

    Quotes removed and a leading `./` dropped, because `cat > "gen.py"` and
    `python3 ./gen.py` name the file that `cat > gen.py` and `python3 gen.py`
    name. Both spellings walked straight through the pair check below: the quoted
    operand was RECORDED as `"gen.py`, which no later command matches, and the
    `./` execution failed the word boundary the recorded name was searched with.
    Two silent passes, on the one construct that check exists for, and each of
    them the ordinary way to write the command.

    `sub/gen.py` is NOT normalised to `gen.py`. A different directory is a
    different file, and collapsing them indicts a document for running something
    it never generated.
    """
    word = word.replace("'", "").replace('"', "")
    while word.startswith("./"):
        word = word[2:]
    return word


# Interpreter options that take a value, so what follows one of them is not the
# script. `-c` and `-m` are deliberately absent: after either, python has no
# script operand at all and every remaining word belongs to the program it is
# already running.
INTERPRETER_VALUE_OPTS = ("-W", "-X", "-Q", "--check-hash-based-pycs")

# The short members of that set, as the LETTERS they are spelled with. python
# lets each take its value ATTACHED — `-Werror::DeprecationWarning`,
# `-Ximporttime` — and read as a short-option cluster such a word is a run of
# letters that happens to contain a `c` and an `m`. Both readers below asked
# `set("cm") & set(word[1:])` and answered "the program came from `-c`", so
# `python3 -Werror::DeprecationWarning - <<'PY'` opened no heredoc and its body
# was never scanned. The detached spelling, `-W error`, was read correctly, so
# the two spellings of one option disagreed — and the one that goes silent is
# the one a runbook writes to keep the command on one line.
INTERPRETER_ATTACHED_VALUE = "WXQ"


def _supplies_program(word):
    """Does the option word `word` hand python a program of its own?

    A short cluster is read LEFT TO RIGHT, which is python's own reading and the
    whole of the fix: `-c` and `-m` take the REST of the argument as their
    operand, so the first of them decides, and `-W`, `-X` and `-Q` do the same
    with a value, so a `c` or an `m` after one of those is inside that value and
    is not an option at all.

    A long option never supplies a program — `--check-hash-based-pycs` is the
    only one this file knows and it takes a value — and it is checked first
    because `--check-hash-based-pycs` contains both letters.
    """
    if word.startswith("--"):
        return False
    for ch in word[1:]:
        if ch in "cm":
            return True
        if ch in INTERPRETER_ATTACHED_VALUE:
            return False        # the rest of the word is this option's value
    return False


def _takes_next_word(word):
    """Does `word` consume the word after it as its VALUE?

    Only the DETACHED spelling does. `-Werror` and `--check-hash-based-pycs=always`
    carry their value inside the token, and consuming the next word after one of
    them would swallow the script operand — reading `python3 -Ximporttime gen.py`
    as an interpreter with no script, which is the opposite silent pass.
    """
    return word in INTERPRETER_VALUE_OPTS


def _interpreter_role(head, skip=1):
    """`(does python read its PROGRAM from stdin, the script FILE it runs)`.

    Two answers off one walk of the arguments, because they are one walk and the
    heredoc scanner needs both. python executes stdin when nothing else was given
    to execute: an explicit `-`, or options only. `-c` and `-m` bring a program
    of their own, and so does a script operand — with any of those, a heredoc on
    the command is DATA the program may read, not source python runs.

    Stated because reading it wrong is what shipped: `opens` was true whenever the
    command word was an interpreter, so `python3 -c '...' <<'PY'`, `python3 -m
    json.tool <<'PY'` and `python3 tool.py <<'PY'` had their bodies scanned and an
    engine import in one reported as a bare invocation. Nothing runs it. That is a
    gate firing on a correct document, and a gate that fires on correct data is how
    a reader learns to reason past a gate.

    `skip` is how many leading words belong to what LAUNCHES python rather than to
    its argv — one for an interpreter word, two for the launcher, which is `sh`
    plus a path and passes everything after itself along.

    Truncated at the first `<<`, which `_command_head` deliberately leaves
    standing because the heredoc verdict keys on it. Left in, the delimiter is the
    next word after the interpreter and `python3 <<'PY'` reads as a command
    running a script called `PY` — the one shape where stdin IS the program,
    turned into the one shape where it is not.

    Every OTHER redirection is removed here, and the claim that stood in its
    place — that `_command_head` had already removed them — was true only of the
    LEADING ones its grammar covers. A redirection may sit anywhere among the
    arguments, so `python3 2>&1 <<'PY'` handed this walk the words `2` and `1`,
    the first of them read as the script operand: not an opener, nothing counted,
    the body never read. `python3 >/dev/null <<'PY'` is the same silence with
    `/dev/null` standing where the script goes. Both are ordinary shell, and
    `python3 - 2>&1 <<'PY'` — the same command with an explicit dash — was read
    correctly all along, so the two spellings of one command disagreed.

    The BOUND, stated rather than papered over: a program of python's own may
    still read stdin and exec it. Where that program is a `-c` literal the text is
    right here and `DYNAMIC_EXEC` reads it at the call site; where it is `-m` or a
    file, it is somewhere no scanner in this module can look, and a body fed to it
    is not read. That is the same bound the audit either side of this one draws.
    """
    argv = REDIRECTION.sub(" ", head.split("<<", 1)[0])
    words = [w for w in re.split(r"[\s;|&()<>]+", argv) if w]
    rest = iter(words[skip:])
    for word in rest:
        if word.startswith("-") and word != "-":
            if _supplies_program(word):
                return False, None
            if _takes_next_word(word):
                next(rest, None)
            continue
        return word == "-", _script_path(word)
    return True, None


def _interpreter_operand(head):
    """The script FILE an interpreter command runs, or None.

    `python3 [options] script [args]`, so the operand is the first word after
    the interpreter that is neither an option nor an option's value. Asked as a
    position for the reason `_command_head` is asked as one: the pair check
    below matched the generated name ANYWHERE in the command, and
    `python3 -c 'print("gen.py")'` names the file while executing it exactly as
    much as `echo` does. A token search cannot tell a script from an argument.

    A short-option CLUSTER is read whole — `-uc 'code'` is `-u -c 'code'` — so
    one letter in the middle of a cluster still says there is no script.

    Half of `_interpreter_role`, and a wrapper rather than a second walk: the
    operand and the stdin question are answered by the same pass over the same
    arguments, and two passes would be two chances to disagree about `-m`.
    """
    return _interpreter_role(head)[1]


def _heredoc_written(text, at, fenced=False):
    """The `.py` file the body of the heredoc opened at `at` ends up in.

    Walked down the PASS-THROUGH chain rather than asked of the owner alone,
    which is the same traversal `_heredoc_runner` makes and the same reason:
    `cat <<'PY' | tee gen.py` writes the body one stage further along, so the
    owner names no file and the pair check saw nothing to pair. The chain stops
    at the first command that does not copy its stdin, which is what keeps
    `run.sh - <<'PY' > out.py` — the launcher writing its PROGRAM's output —
    from being read as engine source landing on disk.
    """
    spans = _command_spans(text)
    here = next((k for k, (a, b, _) in enumerate(spans) if a <= at < b), None)
    while here is not None:
        a, b, _sep = spans[here]
        if not _passes_through(_command_head(_blank_comments(text[a:b]),
                                             fenced)):
            return None
        script = _generated_script(text[a:b])
        if script:
            return script
        if here + 1 >= len(spans) or spans[here + 1][2] not in ("|", "|&"):
            return None
        here += 1
    return None


def _stdin_binding(head):
    """The variable a `read`/`mapfile` command binds its whole stdin to.

    `read -r -d '' CODE <<'PY'` is the ordinary way a runbook puts a
    here-document into a shell variable, and the scanner had no reading for it at
    all: `read` executes nothing, so the command is not an opener; it copies
    nothing on, so it is not a pass-through and writes no file. Nothing counted,
    the body never read — and the `python3 -c "$CODE"` three lines below names no
    engine, so the per-command audit is right to clear it. Between them the engine
    runs on a stock interpreter, which is the offence no single command carries
    and the third place this file has met it.

    Which OPTIONS take a value is spelled PER COMMAND rather than merged, for the
    reason `WRAPPER_PREFIX` spells its members separately: the sets COLLIDE.
    `read -t SECONDS` takes one and `mapfile -t` does not, so a merged reading is
    wrong for one of them — and being wrong here is silent either way, handing
    back an option where the name goes or swallowing the name itself.

    A short CLUSTER is walked LETTER BY LETTER, which is getopt's own reading and
    the reading `_supplies_program` already makes of python's arguments. Asked as
    a whole word, `read -rd '' CODE` — the commonest bash spelling of the very
    construct this function was written for, and the one every runbook uses to
    keep the command on one line — matched no member of `takes`, so `''` stood
    where the NAME goes, the binding was lost and the body went unread. The
    detached `read -r -d ''` was read correctly all along, so one command had two
    spellings and two verdicts. Within a cluster the value may be ATTACHED:
    `-dr` is `-d` taking `r` as its delimiter, so the next word is NOT consumed
    there, and only a value-taking letter in FINAL position reaches past the
    word.

    `read` with no NAME binds `REPLY`, which is bash's own default and the
    spelling a one-liner uses. `mapfile` has no such default and answers None.

    FIELD SPLITTING is the bound: `read A B` puts the first word in `A` and the
    rest in `B`, so a body reaching the engine may land in either. The first name
    is the one recorded, which is where a whole-body read (`-d ''`, or a
    single-line body) puts it; a split body is a shape this does not model, and
    it is named rather than half-covered.
    """
    words = [w for w in re.split(r"[\s;|&()<>]+", head.split("<<", 1)[0]) if w]
    if not words:
        return None
    name = re.sub(r".*/", "", words[0])
    if name not in ("read", "mapfile", "readarray"):
        return None
    takes = ({"-a", "-d", "-i", "-n", "-N", "-p", "-t", "-u", "-c", "-C"}
             if name == "read"
             else {"-d", "-n", "-O", "-s", "-c", "-C", "-u"})
    rest = iter(words[1:])
    for word in rest:
        if word.startswith("-") and word != "-":
            if word.startswith("--"):
                continue            # neither builtin takes one; `--` ends them
            for at, letter in enumerate(word[1:], start=2):
                if f"-{letter}" not in takes:
                    continue            # a flag: the cluster carries on
                if at == len(word):     # ...in final position: the next WORD
                    next(rest, None)
                break                   # ...otherwise the rest of this word
            continue
        return word if re.fullmatch(r"[A-Za-z_]\w*", word) else None
    return "REPLY" if name == "read" else None


# An assignment whose value is a command SUBSTITUTION, read from the span that
# ends where the substitution opens. `CODE=$(cat <<'PY'` is the other spelling of
# `_stdin_binding`'s construct, and `_commands` splits it at the `(` — correctly,
# a substitution being a command — so the heredoc's owner is the `cat` and the
# name it lands in sits in the span before.
SUBSTITUTION_ASSIGNMENT = re.compile(r"(?P<name>[A-Za-z_]\w*)=[\"']?\$$")


def _substitution_binding(text, at):
    """The name an assignment binds the body of the heredoc at `at` to."""
    spans = _command_spans(text)
    here = next((k for k, (a, b, _) in enumerate(spans) if a <= at < b), None)
    if not here:                       # None, or the first span: nothing before
        return None
    prev = text[spans[here - 1][0]:spans[here - 1][1]].rstrip()
    m = SUBSTITUTION_ASSIGNMENT.search(prev)
    return m.group("name") if m else None


def _heredoc_bound(text, at, fenced=False):
    """The VARIABLE the body of the heredoc opened at `at` ends up in.

    The third thing a body can be, beside a program python runs and a file on
    disk: the value of a name some later command expands into an interpreter.
    Both spellings are here — a builtin that reads stdin, and an assignment whose
    value is a substitution around a pass-through — and neither is an offence on
    its own, which is why this returns a name rather than a verdict. See
    `_bound_offenders`, which pairs it.

    The BACKTICK spelling of the substitution is not reached: a backtick is not a
    SEPARATOR, so `` CODE=`cat <<'PY'` `` is one span and there is no previous
    one to read the name out of. Named rather than half-closed, the way the audit
    either side of this one names its bounds.
    """
    spans = _command_spans(text)
    here = next((k for k, (a, b, _) in enumerate(spans) if a <= at < b), None)
    if here is None:
        return None
    a, b, _sep = spans[here]
    head = _command_head(_blank_comments(text[a:b]), fenced)
    name = _stdin_binding(head)
    if name:
        return name
    return _substitution_binding(text, at) if _passes_through(head) else None


def _generated_script(command):
    """The file `command` writes its heredoc body into, if it writes one.

    A redirection or a `tee` operand, which are the two ways a documented command
    turns a here-document into a file on disk.

    The extension used to be the whole of the test, on the ground that a body
    written to `notes.txt` is data. Python does not agree: `python3 gen` runs
    `gen`, and an extensionless generated script was a legal spelling of the
    offence that the pair check returned None on for a reason about the FILENAME.
    The discrimination that ground was reaching for is already made, one step
    later and better, by the pair itself — a body only becomes an offence when
    some later command puts that same path in an interpreter's SCRIPT position,
    which is the document saying it is python whatever it is called. So the
    extension was excluding real offences and excluding no prose, which is the
    one trade this file never takes.

    What replaces it as the bound is the DESCRIPTOR, and it has to: `.py` was
    also picking the right operand out of `cat <<'PY' 2>err.log > gen`, where an
    fd-blind search finds stderr's file first and records one the body never
    reaches. Only fd 1 carries the body, so only fd 1 is read — `&>` included,
    which sends stdout to the file as much as `>` does.

    WHICH command it is asked of matters as much as what it matches, and that is
    the caller's half — see the `_passes_through` gate at the call site. The
    operand alone reads `> file.py` as "the body lands there", which is true of
    `cat` and false of everything that does not copy stdin: `run.sh - <<'PY' >
    generated.py` sends the program's OUTPUT to the file, not its source, and
    `echo > gen.py <<'PY'` reads no stdin at all.

    It does NOT reach a generated script run by PATH — `chmod +x gen && ./gen`,
    where the shebang inside the body names the interpreter. That is the same
    class one further out, it is named here rather than half-covered, and the
    audit either side of this one draws the identical bound: an interpreter has
    to be WRITTEN somewhere for either of them to see one.
    """
    m = re.search(
        r"(?:(?<![\w>])1?>>?\s*|\btee\s+(?:-\S+\s+)*)([^\s<>|&;()]+)",
        command)
    return _script_path(m.group(1)) if m else None


def _terminates(line, term, dash):
    """Does `line` close a heredoc whose delimiter is `term`?

    The shell wants the delimiter ALONE on the line: `<<PY` is closed by `PY`
    and by nothing else, an indented `    PY` included. `<<-PY` is the single
    relaxation and it strips leading TABS only — a space is not a tab, so a
    space-indented line closes neither form.

    This was one `.strip()` for both, which closes on any indentation and on a
    trailing space. Every consequence of the over-match runs the same way: the
    body ends EARLY, the engine import three lines further down is read as
    ordinary text outside any heredoc, and the scan reports one heredoc seen and
    nothing wrong. That is a silent pass — the failure this module keeps finding
    in itself, arriving this time through the check written to prevent it.

    Matching exactly can only ever over-run: an unterminated body is scanned too
    FAR, and the cost of that is a false accusation someone reads, never a
    quiet clearance. A trailing space is included in that deliberately, because
    sh does not close on `PY ` either — a documented command written that way is
    broken as written, and being indicted for it is the correct outcome.

    Stripping the carriage return is about line ENDINGS, not shell syntax.
    Windows contributors are supported here, and a CRLF checkout would otherwise
    fail every comparison in this function and turn a clean corpus into an
    indictment of the whole of it.
    """
    line = line.rstrip("\r")
    return (line.lstrip("\t") if dash else line) == term


def _calls_engine(text):
    """Both invocation forms, because the skills use both.

    `agents/lib/tie_out.py` is a PATH; `python3 -m agents.lib.projection` is a
    MODULE, and the dots make it invisible to a path-only search. Matching only
    the path form silently excluded eleven skills — every planning skill, whose
    whole output is a number the taxpayer acts on, plus the two that move a
    prior year's files.

    The module form ends at a WORD BOUNDARY, not at a dot: `from agents.lib
    import tie_out` binds the same module as `from agents.lib.tie_out import ...`
    and requiring the dot read the first as prose. That verdict decides whether
    a skill is audited AT ALL — `_engine_calling_skills` is derived from it — so
    the spelling a skill happens to use decided whether its engine calls were
    checked for activation and whether it had to carry a preflight gate.

    A separator may be BACKSLASH-QUOTED, and the shell removes it:
    `agents\\.lib` and `agents\\/lib\\/` are the same two targets, so requiring
    the bare character left a skill that writes one UNENROLLED — never audited
    at all, which is the whole-file version of the clearance `_shell_unescape`
    fixes one command at a time. Matched here rather than unescaped, because
    this predicate is handed a whole SKILL and the quote tracker an unescape
    needs does not survive the first apostrophe in its prose. Over-enrolment is
    the safe direction anyway: a skill wrongly enrolled is audited and made to
    carry a preflight gate, while one wrongly left out is silence.
    """
    return (re.search(r"agents\\?/lib\\?/", text) is not None
            or re.search(r"agents\\?\.lib\b", text) is not None)


def _unactivated(unit, fenced=False):
    """Does `unit` hold a command that runs the engine off the launcher?

    Module-level so the audit and its control share ONE definition. Restated in
    the control, the control would pass against its own copy while the audit
    ran on something else — a green light for a predicate nothing tests.

    `BARE_ENGINE_CALL`, and not the three separate facts this stood on —
    RUNS_PYTHON, `_calls_engine`, and `LAUNCHER not in c`. The last of those is a
    substring search over the whole command, so `python3 -m agents.lib.ptet #
    use sh agents/lib/run.sh` cleared it while running the engine on whatever
    `python3` resolves to, and so did a launcher named inside a `-c` body. The
    pattern asks the positional question instead: is the interpreter reaching the
    engine one that `run.sh` put there. It is also what the documents audit has
    always used, so the two now share a predicate rather than agreeing by
    coincidence — the older pair disagreed about the package-import spelling for
    a whole round before anyone noticed there were two.

    Replacing the trio rather than tightening the launcher half was MEASURED, not
    preferred. Blanking quoted text before looking for the launcher — the obvious
    fix — turns README prose red: an apostrophe in "every planning skill's
    computation" opens a quote that never closes, the launcher named later in the
    same sentence disappears into it, and a paragraph ABOUT the rule is indicted
    for breaking it. Across the whole audited corpus that rule newly flagged one
    line and it was that one; this pattern newly flags none.

    `_command_head`, not `ASSIGNMENTS` alone, because a positional pattern needs
    the position and stripping assignments only finds it when nothing precedes
    them. `env RUNNER=agents/lib/run.sh python3 -m agents.lib.ptet` runs the
    engine on a stock interpreter with the launcher sitting in an environment
    VALUE the lookbehind reads as the thing that launched it — the construct
    ASSIGNMENTS was added for, wearing an exec prefix, and cleared by both audits
    for as long as the prefix was there. The compliant mirror,
    `env TAXPY=python3 sh agents/lib/run.sh -m agents.lib.ptet`, was reported.
    That makes THREE questions — this one, the heredoc opener's owner, and the
    documents scan — answered from one notion of which word the shell runs; they
    were three notions and disagreed pairwise, each time in whichever direction
    nobody had a test for. Measured over every markdown unit in the tree, the
    move changes no verdict on the real corpus.

    `fenced` reaches two rules through `_bare_engine_command` — which redirection
    spellings `_command_head` may unwrap, and whether the pattern may cross a
    backtick — and both are the same corpus question the opener patterns ask.

    The PAIR form, a variable holding engine source that a later command feeds to
    an interpreter, is not visible from one unit and lives in `_bare_engine_units`.
    This stays the per-unit predicate the controls are written against.

    `_substitution_commands` is read alongside `_commands` and never instead of
    it — see there. A command whose engine target reaches it through a `$( ... )`
    is one command to the shell and two to the splitter, and the reading that
    puts it back is additive: it can only add an offence, never clear one.
    """
    return any(_bare_engine_command(c, fenced)
               for c in _runnable(_commands(unit))
               + _runnable(_substitution_commands(unit)))


def _shell_unescape(text):
    """`text` with the backslashes the shell REMOVES actually removed.

    A backslash is a quoting character, and the target patterns read it as one
    more literal character in a module name. So `python3 -m agents\\.lib\\.ptet`
    and `python3 agents\\/lib\\/ptet.py` — both of which the shell hands to python
    as the ordinary spelling — matched nothing, and a document running the engine
    on a stock interpreter was CERTIFIED by the audit written to catch it. The
    same backslash carries the more ordinary member of the class: `python3 -m \\`
    and the module on the next line is how a long command gets wrapped, and
    `_join_units` joins those lines for exactly that reason — but it leaves the
    continuation IN, and no target pattern crosses one. That unit reached the
    audit enrolled as an engine caller and was cleared by it, which is the worse
    of the two shapes and the one nobody would have written on purpose.

    The three rules are the shell's, not a simplification of them, because the
    middle one is what keeps this from becoming a false accusation:

      - unquoted, a backslash quotes the next character and both are removed
        when that character is a NEWLINE, which is the continuation;
      - inside double quotes it is literal EXCEPT before ``$ ` " \\`` and a
        newline, so `python3 -m "agents\\.lib"` really does pass a backslash to
        python and really is not an engine call;
      - inside single quotes it is always literal, so `echo 'agents\\.lib'` prints
        what it says and names no module.

    Applied LAST, after `_command_head` and `_blank_inline_script`, and the order
    is load-bearing in both directions. Before them, an escaped `\\>` would reach
    the redirection stripper as a redirection and take the rest of the command
    with it — text removed is the silent direction. After them the `-c` operand
    is already masked and re-quoted with single quotes, so a python body is never
    unescaped: python has no shell backslash removal, and `import agents\\.lib` is
    a SyntaxError rather than a cleverly spelled invocation.

    An unbalanced quote leaves the tail read as quoted and its backslashes
    standing, which is exactly today's behaviour — this can fail to fire, never
    fire wrongly, on text the tracker cannot parse.
    """
    out, quote, i = [], None, 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and quote != "'" and i + 1 < len(text):
            nxt = text[i + 1]
            if quote is None or nxt in '$`"\\\n':
                # The newline goes with it: a continuation is removed ENTIRELY,
                # and `agents.\` / `lib.ptet` is one module name to the shell.
                if nxt != "\n":
                    out.append(nxt)
                i += 2
                continue
        if quote is None and ch in "'\"":
            quote = ch
        elif ch == quote:
            quote = None
        out.append(ch)
        i += 1
    return "".join(out)


def _bare_engine_command(command, fenced=False, values=None):
    """The same question asked of ONE command, which is where it is decided.

    The COMMENT is blanked first, and that is a fix for a gate crying wolf on a
    document that quotes the rule. `sh agents/lib/run.sh -m agents.lib.ptet  #
    never python3 -m agents.lib.ptet` is correctly routed and was reported: the
    pattern is a SEARCH over the whole command, so it found the interpreter in
    the trailing comment, and the lookbehind asking whether `run.sh` put it there
    passed because the word in front of it is `never`. Text the shell does not
    execute is not an invocation, which is the rule `_blank_inert` already
    applies to a quoted mention and `_blank_python` to a body's string literal.
    This file has now written that sentence six times, which is the argument for
    fixing the position rather than the spelling.

    The `-c` operand is masked on the same terms and for the same reason — see
    `_blank_inline_script`. A quoted body is text the SHELL does not execute and
    the interpreter reads as python, so what runs in it is what python's lexer
    says runs: a heredoc body has been read that way since the scanner existed,
    and the identical text in argv was searched raw.

    What the mask costs is stated rather than left to be discovered. A markdown
    HEADING opens with `#` at the start of a line, so a heading naming a bare
    engine call is now invisible to the documents audit. That is a trade, it was
    MEASURED — across every audited document and every skill file in the tree the
    blanking changes ZERO verdicts — and it is the right way round: a heading is
    a title, never an instruction to run anything, while the prose under it is
    still read in full.

    `values` is what the names in scope currently hold, and expanding them is
    the same rule reached from the other side: what the shell would RUN is the
    text after expansion, so that is the text to read. `_bare_engine_units` owns
    the map; the default is the empty one, which leaves every `$NAME` standing
    and is what the per-unit controls exercise.

    What this does NOT do is decide that a quoted ARGUMENT is inert. `echo
    'python3 -m agents.lib.ptet'` prints a string and is reported, and so is a
    prose sentence warning against that command inside a single code span. Both
    are false accusations; both were declined, because every rule that clears
    them clears more. The quote around an argument is the same character as the
    quote around a MODULE NAME, so blanking one blanks `python3 -m
    "agents.lib.ptet"` — a real invocation this pattern gained a round ago —
    and blanking only the arguments of commands that "do not execute" needs a
    LIST, where an omission costs a false alarm and a wrong entry costs a silent
    pass. In prose the two are not distinguishable at all: "run `X`" and "never
    run `X`" differ by a word no scanner reads. The corpus is a hand-listed
    tuple for exactly this reason — see `DOCUMENTS` — and the remedy for a
    document that must warn against the anti-pattern is to describe it rather
    than to spell it, which is the fail-CLOSED half of the trade.
    """
    pattern = FENCED_ENGINE_CALL if fenced else BARE_ENGINE_CALL
    text = _expand(_blank_comments(command), values)
    return bool(pattern.search(_normalise_paths(
        _shell_unescape(_blank_inline_script(_command_head(text, fenced))))))


def _assigned_values(command):
    """`{name: value}` for every name `command` assigns or unsets.

    A MAP and not the set of engine-valued names it was, because the caller
    needs to know that a name stopped holding engine source as much as that it
    started. A set only ever grows: `CODE='from agents.lib import ptet'` marked
    `CODE` for the rest of the document, so a later `CODE='print(1)'` — or an
    `unset` — still indicted the `python3 -c "$CODE"` under it. The last
    assignment is what the shell expands, and reporting anything else is a false
    accusation on a document that reads correctly top to bottom.

    The VALUE and not a verdict about it, for the reason `_expand` gives: a
    verdict has to pick a question, and every one of them is half the answer.

    A leading assignment on a command that RUNS something is scoped to that
    command and does not survive it, which is the difference between the shell's
    two spellings and was the difference this returned nothing for.
    `RUNNER=python3` then `RUNNER=<launcher> /usr/bin/true` leaves `RUNNER`
    holding `python3` — the second assignment lives and dies with `true` — so the
    `$RUNNER -m agents.lib.ptet` below it is a bare engine call, and a tracker
    that carried the temporary value forward CLEARED it as routed. Report to
    clear, on a value the shell never held: the direction that must not be wrong.
    So only an assignment-ONLY command updates the map, plus the assignment
    BUILTINS, whose whole operand list is assignments wherever the word sits.
    Judging the command this way costs nothing at its own site — `_command_head`
    strips a leading assignment from the head already, and `_bare_engine_units`
    records after it judges, so neither ever read the prefix as state.

    A trailing REDIRECTION is the bound: `LOG=x > out` assigns and runs nothing,
    so the shell keeps it and this reads the redirection as the command word and
    drops it. Left as it is because both ways of being wrong here are reachable
    from a dropped name — an unexpanded launcher accuses, an unexpanded
    interpreter clears — and the spelling is one no runbook writes.

    The bound this accepts is a BRANCH: `if x; then CODE=<engine>; else
    CODE=safe; fi` assigns twice on one path each, and straight-line reading
    takes the second. A markdown runbook that writes that and then expands it is
    the case this misses, and it is the trade the direction argument is about —
    the alternative marks every name that ever held engine source, which fires
    on the ordinary act of reusing a variable.

    A LOOP binds a name too, and reading only `NAME=value` was the hole. `for m
    in agents.lib.tie_out agents.lib.capabilities; do python3 -m "$m"; done` runs
    the engine on a stock interpreter twice; `;` puts the module names in one
    command and the interpreter in another, nothing recorded `m`, and `$m` stood
    unexpanded through a pairing that then found an interpreter reaching no
    target. The routed spelling of the same loop was clean under both readings,
    so this was pure loss and not a trade.

    ALL the words at once, joined, because the value is what `_expand` puts back
    and the audit asks whether ANY iteration reaches the engine. One iteration
    per name would be the faithful model and needs a second traversal to
    represent; the join reaches the same verdict from the same text, since a
    pattern that finds a target in one word finds it in the run of them.

    `while read NAME` binds from STDIN and is deliberately absent: the value is
    not in the text, so there is nothing to record and a name bound to nothing
    is what the map already means by an absent key.
    """
    values, text = {}, command.strip()
    # `for`/`select NAME in words` — recorded BEFORE the assignment walk, and
    # returned on its own, because a loop header is not an assignment-only
    # command and the rule below would drop it with the rest.
    loop = re.match(r"(?:for|select)\s+(?P<name>[A-Za-z_]\w*)\s+in\s+"
                    r"(?P<words>.+)$", text, re.S)
    if loop:
        return {loop.group("name"): loop.group("words").strip()}
    # The assignment BUILTINS, whose operands are assignments wherever they sit.
    builtin = re.match(r"(?:export|declare|local|readonly|typeset)\b\s*"
                       r"(?:-\S+\s+)*", text)
    if builtin:
        text = text[builtin.end():]
    # LEADING words only, which is where the shell reads an assignment. Searched
    # over the whole command, `echo CODE='from agents.lib import ptet'` — an
    # argument echo prints and assigns nothing — overwrote the model, and the
    # `python3 -c "$CODE"` under it was reported for a value the shell would
    # never expand.
    pos = end = 0
    while (m := ASSIGNMENT_WORD.match(text, pos)) is not None:
        values[m.group("name")] = _unquote(m.group("value"))
        end = m.end()
        gap = re.compile(r"\s+").match(text, end)
        if gap is None:
            break
        pos = gap.end()
    # Anything left after them is the command word, and its presence is what
    # makes the assignments temporary. Comments blanked first: `RUNNER=python3
    # # the host one` runs nothing, and reading the note as a command word would
    # drop a binding the shell keeps.
    if _blank_comments(text[end:]).replace("\0", "").strip():
        values = {}
    unset = re.match(r"\s*unset\s+(?:-[fv]\s+)*(?P<names>[^;|&]*)", command)
    if unset:
        for name in re.findall(r"[A-Za-z_]\w*", unset.group("names")):
            values[name] = ""
    return values


def _unquote(word):
    """A shell word with ONE layer of surrounding quotes removed.

    The quotes are part of what `ASSIGNMENT_WORD` captures and are no part of
    the value. Expanded back in with them, `CODE='from agents.lib import ptet'`
    reaches the `-c` operand as `'from agents.lib import ptet'`, which python's
    lexer reads as a STRING LITERAL and `_blank_python` then correctly masks —
    the value clearing itself on the way to being read.
    """
    if len(word) >= 2 and word[0] == word[-1] and word[0] in "'\"":
        return word[1:-1]
    return word


def _values_before(text, at, carried):
    """`carried` updated with every assignment `text` makes BEFORE offset `at`.

    Per POSITION and not per line, because a line is not a command. `PY=python3;
    $PY - <<'PY'` assigns and expands on one of them, and a map built from the
    whole line would either expand a name the shell had not yet bound — reading
    `PY=python3 $PY -m agents.lib.ptet`, which sh runs on whatever `$PY` held
    BEFORE the line, as an engine call — or, built after it, miss the ordinary
    two-command spelling entirely. `_bare_engine_units` gets the same ordering
    for free by judging each command before recording its own assignments; this
    is that rule for a scanner that works in offsets.
    """
    values = dict(carried)
    for start, end, _sep in _command_spans(text):
        if start >= at:
            break
        values.update(_assigned_values(text[start:end]))
    return values


def _expand(text, values):
    """`text` with `$NAME` and `${NAME}` replaced by what `values` says each
    name holds.

    SUBSTITUTION, and not a verdict about the value, because the model that
    asked for a verdict had to pick one question and picked half of it. It
    recorded whether a value held an engine TARGET, so `CODE='from agents.lib
    import ptet'` was tracked and `PYTHON=python3` was not — and `$PYTHON -m
    agents.lib.ptet` names no interpreter any pattern here can see, so the
    engine reached a stock python with both audits clean. The interpreter
    travels in a variable exactly as readily as the source does, and a rule that
    models one of them models the wrong one half the time.

    Putting the TEXT back answers both, and the compliant mirror with it:
    `RUNNER='sh agents/lib/run.sh'` expands to a launcher the lookbehind reads
    where it sits, so the routed spelling stays routed. A rule that learned to
    recognise "this value is an interpreter" would immediately have owed a
    second answer to "...and this one is the launcher" — the pairwise
    disagreement this file keeps arriving at from a new direction.

    A name with NO recorded value is left alone. `$PATH` is not empty, and
    deleting it would join the words on either side into one nobody wrote.
    `${VAR:-default}` and `$(cmd)` are not this form and are untouched for the
    same reason `_command_head` does not enter a substitution: a substitution is
    a COMMAND, and splitting it out is `_commands`' job.

    Expansion can only ADD text, so it can only add matches — the direction a
    widening belongs in, and the one it inherits from the literal spelling it
    now matches: `echo $PY -m agents.lib.ptet` is reported exactly as `echo
    python3 -m agents.lib.ptet` already was. Measured over the audited corpus
    and the whole skills tree it changes no verdict.
    """
    if not values:
        return text
    return re.sub(r"\$\{(\w+)\}|\$(\w+)",
                  lambda m: values.get(m.group(1) or m.group(2), m.group(0)),
                  text)


# A path-shaped WORD, for the one rewrite `_normalise_paths` performs.
PATH_WORD = re.compile(r"[\w.~$-]*(?:/[\w.~$-]*)+")


def _normalise_paths(text):
    """`text` with `./` and `x/../` collapsed inside every path-shaped word.

    The engine's PATH form is a run of word characters and slashes ending in
    `.py`, which cannot cross a dot segment — so `python3 ./agents/lib/../lib/ptet.py` names
    the same file the pattern was written for and read as prose, with both audits
    green. `_script_path` has dropped a leading `./` since it existed, for exactly
    this reason one construct along; the interior `..` had no reader at all.

    Guarded on `./` appearing in the word, and the guard is load-bearing rather
    than an optimisation: `normpath` also strips a TRAILING slash, and
    `_calls_engine` searches for `agents/lib/` WITH one. Normalising every path
    would quietly un-enrol a skill from the audit — the whole-file version of the
    clearance this function exists to remove.
    """
    def fix(m):
        word = m.group()
        if "./" not in word:
            return word
        fixed = posixpath.normpath(word)
        if word.endswith("/") and not fixed.endswith("/"):
            fixed += "/"
        return fixed
    return PATH_WORD.sub(fix, text)


def _assigned_aliases(command):
    """`{name: value}` for every alias `command` defines.

    An alias fills the same position a variable does and `_expand` could not
    reach it: `alias py3=python3` then `py3 -m agents.lib.ptet` runs the engine on
    a stock interpreter with no interpreter word written on the command that does
    it. The map is the one `_assigned_values` keeps, for the one construct that
    binds a COMMAND WORD rather than an expansion.

    One level, and it is a bound rather than an oversight: an alias whose value
    names another alias is resolved by the shell and not here, and the shape has
    no reading in this corpus. `unalias` is absent for the same reason — an alias
    that stops existing can only remove an accusation.
    """
    if "alias" not in command:
        return {}
    head = _command_head(_blank_comments(command)).strip()
    m = re.match(r"alias(?:\s+-\S+)*\s+", head)
    if not m:
        return {}
    out, pos = {}, m.end()
    while (w := ASSIGNMENT_WORD.match(head, pos)) is not None:
        out[w.group("name")] = _unquote(w.group("value"))
        gap = re.compile(r"\s+").match(head, w.end())
        if gap is None:
            break
        pos = gap.end()
    return out


def _dealias(command, aliases):
    """`command` with an aliased COMMAND WORD replaced by what it stands for.

    Positional, like everything else here that decides routing: only the word the
    shell would run is substituted, so an alias NAME appearing as an argument or
    inside a quoted body is left exactly as written.
    """
    if not aliases:
        return command
    text = command.strip()
    head = _command_head(text)
    word = re.match(r"\S+", head)
    if not word or word.group() not in aliases:
        return command
    return (text[:len(text) - len(head)] + aliases[word.group()]
            + head[word.end():])


def _chdir(cwd, command):
    """The working directory after `command`, which is `cwd` unless it is a `cd`.

    `""` is the repository root, a string is a path relative to it, and `None` is
    UNKNOWN — `cd` with no operand, an absolute or `$`-expanded target, or a walk
    that climbs out of the tree. Unknown disables `_relocate` rather than guessing
    at a root, because a guess there invents an engine target the document does
    not name.
    """
    if not re.match(r"\s*cd(?:\s|$)", command):
        return cwd
    head = _command_head(_blank_comments(command)).strip()
    m = re.match(r"cd(?:\s+-[\w-]+)*(?:\s+(?P<dir>[^\s;|&]+))?\s*$", head)
    if not m:
        return cwd
    target = m.group("dir")
    if cwd is None or target is None:
        return None
    target = _unquote(target).replace("\0", "")
    if not target or target.startswith(("/", "~", "$", "-")):
        return None
    joined = posixpath.normpath(posixpath.join(cwd, target))
    if joined.startswith(".."):
        return None
    return "" if joined == "." else joined


def _relocate(command, cwd):
    """`command` with its cwd-relative engine operands written repo-relative.

    `cd agents/lib && python3 ptet.py` runs the engine on a stock interpreter, and
    every pattern here reads its target as a REPOSITORY path — so the second
    command named no engine, the first ran nothing, and the pair passed both
    audits. The rewrite puts the two facts on one command, which is the same
    answer `_expand` gives for a value that travels in a variable.

    A rewrite happens only where the file it would name EXISTS in this tree, and
    that bound is what keeps it off the ordinary command: under `cd agents/lib`,
    `python3 -m pytest` would otherwise become `-m agents.lib.pytest` and read as
    an engine call. The audit is about this repository's engine, so this
    repository is what decides.

    Both operand forms, because the two halves of one rule disagreeing about one
    command is this file's recurring bug: a `.py` script operand and a `-m` module
    name are the same relocation seen from either side of `-m`.

    A script operand is a PATH and may carry directory components, which the
    bare-filename class it was written with could not hold: `cd agents &&
    python3 lib/ptet.py` and `cd agents/lib && python3 ./ptet.py` both run the
    engine on a stock interpreter, and both were rewritten to nothing and
    cleared — while `cd agents && python3 -m lib.ptet`, the same command through
    the `-m` half of this same rule, was reported. One command, two spellings,
    two verdicts, which is the shape this function exists to end.

    NORMALIZED after joining, because the join is what the engine pattern then
    reads: its path form is a run of word characters and slashes under
    `agents/lib/`, so an interior `./` or `..` left standing in the rewritten
    path matches nothing and the fix would clear exactly the spellings it was
    widened for.
    """
    if not cwd:
        return command

    def script(m):
        word = m.group()
        if not (REPO / cwd / word).is_file():
            return word
        return posixpath.normpath(posixpath.join(cwd, word))

    def module(m):
        base = REPO / cwd / m.group("name").replace(".", "/")
        if base.with_suffix(".py").is_file() or (base / "__init__.py").is_file():
            return f"{m.group('opt')}{cwd.replace('/', '.')}.{m.group('name')}"
        return m.group()

    out = re.sub(r"(?<![\w./-])(?:[\w.-]+/)*[\w.-]+\.py(?![\w/])",
                 script, command)
    return re.sub(r"(?P<opt>-m\s*[\'\"]?)(?P<name>[\w.]+)", module, out)


def _runnable(commands):
    """Each command as the shell would RUN it, in order.

    An ALIAS resolved and a `cd` applied to everything after it, which are the two
    kinds of state a command inherits from the ones before it that
    `_assigned_values` does not model. Kept in one place so the per-unit predicate
    and the sequence audit cannot come to disagree about it — see `_unactivated`,
    which is the control, and `_bare_engine_units`, which threads the same state
    across a whole scope instead of resetting per unit.
    """
    aliases, cwd, out = {}, "", []
    for command in commands:
        out.append(_relocate(_dealias(command, aliases), cwd))
        aliases.update(_assigned_aliases(command))
        cwd = _chdir(cwd, command)
    return out


def _runs_stdin(head):
    """Does `head` run an interpreter whose PROGRAM is its stdin?

    `python3 -`, and `python3` with no operand at all, which is the same thing
    spelled shorter. `python3 gen.py` and `python3 -m pytest` read stdin too, but
    what they run is the operand, so text arriving on the pipe is DATA to them
    and reporting it would be an accusation about a program nothing executes.

    The launcher answers False here because its first word is `sh`, which is the
    routed form and the whole point: `cat agents/lib/ptet.py | sh
    agents/lib/run.sh -` is compliant.

    REDIRECTIONS are removed before the walk, for the reason `_interpreter_role`
    removes them: they sit among the arguments and `_command_head` only strips
    the LEADING ones. `cat agents/lib/ptet.py | python3 2>/dev/null` is the
    engine piped into a stock interpreter, and the `2` was read as its script
    operand — a program of python's own, so the pipeline cleared. The same line
    written `| python3 -` was reported, which is the pairwise disagreement this
    file keeps arriving at: one command, two spellings, two verdicts.
    """
    word = re.match(r"\S+", head)
    if not (word and INTERPRETER.search(word.group())):
        return False
    argv = REDIRECTION.sub(" ", head[word.end():])
    rest = iter([w for w in re.split(r"[\s;|&()<>]+", argv) if w])
    for w in rest:
        if w == "-":
            return True
        if w.startswith("-"):
            if _supplies_program(w):
                return False           # -c / -m supplies the program instead
            if _takes_next_word(w):
                next(rest, None)
            continue
        return False                   # a script operand
    return True


def _piped_engine(text, spans, k, fenced, values):
    """Does an upstream pipeline stage hand engine source to command `k`?

    The offence no PAIR of commands carries either, and the third shape of the
    one this file keeps meeting: `cat agents/lib/ptet.py | python3 -` runs the
    engine on a stock interpreter, and each half is clean read alone — the left
    names a file and executes nothing, the right names no engine. `_commands`
    splits at the pipe, correctly and for a cry-wolf fix (`python3 -m pytest &&
    {launcher} -m agents.lib.ptet` is two correct commands), and the split took
    this with it.

    Answered with the SEPARATOR, which is why it takes spans rather than the
    command text: a pipe carries the data and `&&` does not. The walk goes as far
    upstream as the pipes reach, so `cat x.py | cat | python3 -` is the same
    offence one stage longer — the same traversal `_heredoc_runner` makes down
    the chain, made backwards.

    OUTSIDE A FENCE no BACKTICK may sit between the engine target and the
    interpreter, which is the guard `BARE_ENGINE_CALL` already applies for the
    same reason one line up: in prose `|` is a markdown TABLE delimiter at least
    as often as it is a pipe, and a row reading
    `` | `agents/lib/ptet.py` | `python3 -` | `` is two code spans in a table,
    not a command. A real prose pipeline is written inside ONE span, so nothing
    separates its halves and it still reads. Inside a fence there are no code
    spans and no tables, so the guard is dropped exactly where the engine
    pattern drops it.

    The span checked is measured on the RAW text, since `_expand` is the one
    step here that changes offsets. A target that exists only after expansion
    therefore has no position to measure from, and in prose that clears the
    pipeline rather than guessing at one — the cry-wolf direction, on the one
    combination (a variable holding an engine path, piped, in prose) that has no
    reading in the corpus at all.
    """
    downstream = text[spans[k][0]:spans[k][1]]
    if not fenced:
        # A prose command ENDS at the closing backtick, and the sentence after
        # it is not argv. Read whole, `` `cat x.py | python3 -` to check. ``
        # hands `_runs_stdin` the operand `to` and reads as an interpreter
        # running a script — clean, on the one prose spelling of this offence a
        # document would actually write.
        downstream = downstream.split("`")[0]
    if not _runs_stdin(_command_head(
            _expand(_blank_comments(downstream), values), fenced)):
        return False
    j = k
    while j > 0 and spans[j][2] in ("|", "|&"):
        j -= 1
        a, b, _sep = spans[j]
        raw = _blank_comments(text[a:b])
        if not re.search(_ENGINE_TARGET, _expand(raw, values)):
            continue
        if fenced:
            return True
        here = re.search(_ENGINE_TARGET, raw)
        between = text[a + here.end():spans[k][0]] if here else text[a:spans[k][0]]
        if "`" not in between:
            return True
    return False


def _bare_engine_units(units):
    """The `(scope, line, text, fenced)` units that run the engine off the
    launcher, as `(scope, line, text)`.

    A SEQUENCE rather than one unit at a time, because one form is not a property
    of any single command. `CODE='from agents.lib import ptet'` runs nothing —
    `_command_head` strips an assignment to the empty string, correctly — and
    `python3 -c "$CODE"` names no engine, so each half is clean and the pair is
    the engine on a bare interpreter. No per-unit predicate can see that, which
    is why both audits call this rather than `_unactivated` directly.

    Commands are judged BEFORE their own assignments are recorded, so a use that
    precedes its assignment is not read as one.

    The holder set is keyed on the SCOPE — a file, for the skills — and reset
    when it changes. A variable assigned in SKILL.md is not in scope in a
    reference, and carrying it across would indict a document for a name defined
    in another one.

    Within a scope it is a MAP of what each name currently holds, never a set of
    what one ever held — see `_assigned_values`. Reassignment and `unset` both
    clear, because both are what the shell would expand. The map carries the
    VALUE, so the pair form and the interpreter-in-a-variable form are one
    question asked once — see `_expand`.
    """
    holders, aliases, cwd, scope, offenders = {}, {}, "", object(), []
    for key, lineno, text, fenced in units:
        if key != scope:
            holders, aliases, cwd, scope = {}, {}, "", key
        hit = False
        spans = _command_spans(text)
        # The map as the unit is ENTERED, kept for the substitution reading
        # below. That reading has no position in the span order to be judged at,
        # and judging it against the map the unit ends with would expand a name
        # the shell had not yet bound — the ordering rule `_values_before` states,
        # reached from the one direction that has no offset to work from.
        rejoined = _substitution_commands(text)
        entering = dict(holders) if rejoined else None
        for k, (a, b, _sep) in enumerate(spans):
            command = text[a:b]
            # The ALIAS map and the working directory alongside the value map,
            # and carried across a scope the same way — see `_runnable`, which is
            # this state for a caller with no scope to carry it in.
            # `_piped_engine` is asked of the raw text, because it MEASURES a
            # span on it and a rewrite is the one step here that moves an offset.
            runnable = _relocate(_dealias(command, aliases), cwd)
            if (_bare_engine_command(runnable, fenced, holders)
                    or _piped_engine(text, spans, k, fenced, holders)):
                hit = True
            holders.update(_assigned_values(command))
            aliases.update(_assigned_aliases(command))
            cwd = _chdir(cwd, command)
        if not hit and rejoined:
            hit = any(_bare_engine_command(c, fenced, entering) for c in rejoined)
        if hit:
            offenders.append((key, lineno, text))
    return offenders


def _engine_calling_skills():
    """Skills whose text shells out to the engine, derived from the text itself.

    Deriving the list rather than hardcoding it is the point: a NEW skill that
    calls `agents/lib/` is covered the day it is written, with no second list to
    remember to update. A hardcoded roster would go quietly stale in exactly the
    direction that matters — a skill running the gates' scripts without first
    checking the gates exist.
    """
    found = set()
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name in CONTRIBUTOR_SKILLS:
            continue
        if _calls_engine(skill_text(skill_dir.name)):
            found.add(skill_dir.name)
    return found


def _skill_files(name):
    """A skill's body and its references, in reading order."""
    skill_dir = SKILLS_DIR / name
    return [skill_dir / "SKILL.md", *sorted(skill_dir.glob("references/*.md"))]


# Derived ONCE. Four `parametrize` decorators call this, and each call walked the
# whole skills tree and read every file of every skill before a single test body
# ran — then each of the eighty parametrized cases re-read the same files through
# `_shell_units`. This module is now in the hook's skill-edit selection path, so
# that cost is paid on every SKILL.md save.
ENGINE_CALLING_SKILLS = None


def _engine_calling_skills_cached():
    global ENGINE_CALLING_SKILLS
    if ENGINE_CALLING_SKILLS is None:
        ENGINE_CALLING_SKILLS = _engine_calling_skills()
    return ENGINE_CALLING_SKILLS


_UNITS_CACHE = {}


def _units(name):
    """`_shell_units` memoised per skill, as a list.

    The generator re-reads from disk on every consumption, and four assertions
    consume it per skill. Tests that monkeypatch SKILLS_DIR call `_shell_units`
    directly and are deliberately NOT routed through this — a fixture built in a
    tmp_path must not answer from a cache keyed on a real skill's name.
    """
    if name not in _UNITS_CACHE:
        _UNITS_CACHE[name] = list(_shell_units(name))
    return _UNITS_CACHE[name]


def _shell_units(name):
    """(path, lineno, text) per command, joining an inline `python3 -c` body
    and a `\\`-continued line.

    Units, not lines, because an inline `-c` command spans however many lines
    its quoted script needs. The unit ends at the line that closes the opening
    quote, so the whole command — `python3` and the engine import both — is one
    string to test.

    A trailing `\\` is the same problem in the other direction, and it produced
    the opposite error: a FALSE positive. `find … -print0 \\` / `| xargs -0
    python3 -m agents.lib.secure_fs` is ONE shell command, so the activation
    chained onto the `find` covers the `python3` two lines down — but read as
    two units the second one carries no activation and was reported unactivated.
    A predicate that matches less than it is believed to is the recurring bug in
    this file; here it cost a correct command a refusal rather than letting a
    wrong one pass, which is the better direction and still wrong.
    """
    for path in _skill_files(name):
        for lineno, text, fenced in _join_units(path.read_text().splitlines()):
            yield path.relative_to(REPO), lineno, text, fenced


def _join_units(lines):
    """(1-based line number, text, fenced) per shell command over `lines`.

    Split out of `_shell_units` because the skills are not the only corpus that
    needs it. The documents audit read one PHYSICAL line at a time, so a valid
    invocation written `python3 \\` / `-m agents.lib.ptet` had no line carrying
    both the interpreter and its target and matched nothing — a bare engine call
    in a document, green, in the test whose subject is bare engine calls in
    documents. The join is the same join; having two scanners over overlapping
    corpora disagreeing about where a command ends is what put it there.

    The fence state travels WITH the unit because two of the rules that judge it
    differ between a fenced command and a sentence about one — see
    `_command_head` and `BARE_ENGINE_CALL` — and where a unit sits is not
    something its text can say. Read off its FIRST line: a unit straddling a
    fence is a malformed block on either reading.

    Neither join crosses a fence DELIMITER, and the delimiter is the one
    `_fence_state` computed rather than a second opinion about what one looks
    like. Bounded by `startswith("```")` — which is what this was — the two
    readings disagreed in both directions at once: a `~~~` fence closed nothing,
    so an unterminated `-c` body ran past the end of its block and swallowed the
    correct commands below it into one accused unit, while inside a
    four-backtick fence an inner ``` ``` ``` — CONTENT, and marked as content
    three functions up — cut a real continued command in half and left both
    halves reading clean. One file, two fence rules, and the answer depended on
    which character the document happened to use.
    """
    inside, boundary = _fence_state(lines)
    i = 0
    while i < len(lines):
        start, unit = i, [lines[i]]
        while i + 1 < len(lines):
            # ONE loop, and the two joins re-examined after each other, because
            # a command can need them in either order. The `-c` opener used to be
            # looked for on the FIRST physical line and never again, so
            # `python3 \\` / `-c "` / an engine import — one valid command whose
            # opener straddles the continuation — was cut into units with the
            # interpreter in the first and the import in the third, and the audit
            # reported clean on a bare engine call. Continuations are removed
            # before the opener is looked for, the way the shell removes them,
            # or the opener still straddles a `\\` and a newline and is still
            # not found.
            flat = re.sub(r"\\[ \t]*\n", "", "\n".join(unit))
            # BOTH openers, and the quote state read ONCE. An inline `-c` body
            # and a quoted assignment VALUE are the same construct — a shell word
            # whose closing quote is on a later line — and only the first was
            # looked for, so the second was cut apart. Neither is preferred over
            # the other: a unit may carry a balanced `-c` and an open assignment
            # at once, and `or` between two searches would take the balanced one
            # and stop joining.
            openers = [m for m in (INLINE_PY.search(flat),
                                   ASSIGNMENT_OPENER.search(flat)) if m]
            still_open = _quote_scan(flat)[1] if openers else ()
            # BOUNDED at the fence that closes the block. Unbounded, an
            # opening quote that never balances — an unterminated example, a
            # doubled quote inside a `-c` body — runs to EOF and makes ONE
            # unit of the rest of the file. That unit contains an activation
            # somewhere, so the audit sees a single activated invocation and
            # passes over every genuine offender below it: the file's own
            # recurring bug, arriving this time as a join that matches more
            # than it should rather than less.
            # OPEN, not odd. Parity counts quote characters and an escaped
            # one is a character — `-c "x = '\\"'` has two and one open string
            # — so the join stopped mid-body and the import on the next line
            # became a unit of its own, with the interpreter left in the one
            # above. Both halves read clean; the command they belong to runs
            # the engine on a bare interpreter. `_quote_scan` already models
            # the escape for `_commands`, so the question is asked of it.
            if (any(m.group(1) in still_open for m in openers)
                    and not boundary[i + 1]):
                i += 1
                unit.append(lines[i])
                continue
            if unit[-1].rstrip().endswith("\\") and not boundary[i + 1]:
                i += 1
                unit.append(lines[i])
                continue
            break
        yield start + 1, "\n".join(unit), inside[start]
        i += 1



class TestEngineCheck:
    def test_a_full_clone_passes(self):
        # workspace=REPO, matching the guard above: with workspace=None this
        # resolves `.venv` through the CWD while the guard resolves it
        # through REPO, so running the suite from elsewhere left the venv
        # branch unexercised and `not blocking` true for the wrong reason.
        report = P.check(workspace=REPO)
        assert not report.blocking, P.render_markdown(report)
        assert report.probe_error == ""
        assert report.failures == ()

    def test_a_missing_module_blocks_and_names_its_role(self):
        report = P.check(modules=(("agents.lib.not_a_real_module", "the tie-out gate", "x"),))
        assert report.blocking
        assert len(report.failures) == 1
        failure = report.failures[0]
        assert failure.module == "agents.lib.not_a_real_module"
        assert "tie-out gate" in failure.role
        # The file check runs before the import, so an absent module reports as
        # a missing FILE — which is what the skills would hit, since they invoke
        # the engine by path rather than importing it.
        assert "FileNotFoundError" in failure.error
        # The refusal must say how to proceed, not merely that it refused.
        assert "git clone" in P.render_markdown(report)

    def test_an_importable_module_with_no_file_beside_us_still_blocks(
            self, monkeypatch, tmp_path):
        """The gate checks by IMPORT; the skills invoke by PATH.

        A tree holding preflight.py and nothing else, on a machine where the
        toolkit is `pip install -e`'d or on PYTHONPATH, imports every module
        happily — and then `tax-compute` Step 12.7 runs
        `python3 agents/lib/tie_out.py` and exits 127, a status its "act on 0 or
        2" branch does not enumerate. The tie-out gate becomes a silent no-op
        behind a green preflight, which is the whole failure this module exists
        to refuse.
        """
        monkeypatch.setattr(P, "_HERE", tmp_path)   # no engine files beside us
        report = P.check(modules=(("agents.lib.tax_computations", "the math", "compute_bracket_tax"),))
        assert report.blocking, (
            "an import that resolved from somewhere else was certified, while "
            "the path the skills actually run does not exist")
        assert "beside preflight.py" in report.failures[0].error

    def test_a_partial_copy_blocks(self):
        """One present module does not make an engine."""
        report = P.check(modules=(
            ("agents.lib.tax_computations", "the tax math", "compute_bracket_tax"),
            ("agents.lib.not_a_real_module", "the capability gate", "x"),
        ))
        assert report.blocking
        assert [f.module for f in report.failures] == ["agents.lib.not_a_real_module"]

    def test_a_leaf_module_no_one_imports_is_still_checked(self):
        """The gates are not the whole engine.

        `projection.py` IS what /tax-project does, and nothing imports it — so
        a copy keeping the four gate modules and dropping the leaves passed a
        gate-only check while the skill it powers had nothing left to run. That
        was real: the check reported OK on a copy missing thirteen of twenty-one.
        """
        checked = {e[0] for e in P.ENGINE_MODULES}
        for leaf in ("agents.lib.projection", "agents.lib.filing_package",
                     "agents.lib.crypto_importer"):
            assert leaf in checked

    def test_a_present_but_empty_module_blocks(self, tmp_path, monkeypatch):
        """PRESENT and IMPORTABLE is not USABLE.

        An emptied or stubbed file — an interrupted copy, a failed download, an
        LFS pointer stripped — imports perfectly and exposes nothing, and the
        gate reported "OK ... Gates available: the intake-to-result tie-out
        gate" over it. Downstream is worse: `python3 agents/lib/tie_out.py`
        then exits 0 with no output, which tax-compute Step 12.7 reads as TIED
        OUT, so a dropped 1099 sails through behind a green preflight — the
        exact CP2000 failure this module exists to prevent. Anything truncated
        mid-statement raises SyntaxError and was already caught; this is the
        shape that was not.
        """
        pkg = tmp_path / "hollow"
        pkg.mkdir()
        _complete_package(pkg)
        (pkg / "tie_out.py").write_text("")            # imports, exposes nothing
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.setattr(P, "_HERE", pkg)
        report = P.check(modules=(("hollow.tie_out", "the tie-out gate",
                                   "load_ledger"),))
        assert report.blocking, "an empty engine module was certified usable"
        assert "exposes no `load_ledger`" in report.failures[0].error

    def test_the_flagship_verdict_prints_one_remedy_not_two(self):
        """A skill copied away from the engine used to get BOTH the clone
        remedy and a second one asserting the files ARE present — under a
        headline saying they are missing."""
        failure = P.ModuleFailure(
            "agents.lib.tie_out", "the tie-out gate",
            "FileNotFoundError: no tie_out.py beside preflight.py", False, True)
        rendered = P.render_markdown(
            P.PreflightReport("3.12.0", True, (failure,), "", 21))
        assert "git clone" in rendered
        assert "files are present but at least one" not in rendered

    def test_every_roster_symbol_really_exists(self):
        """The required symbols are a second hand-written list, so they are
        pinned against the modules themselves — a rename that misses one would
        otherwise turn every run red with a false 'stubbed' verdict."""
        import importlib

        for name, _role, required in P.ENGINE_MODULES:
            module = importlib.import_module(name)
            assert hasattr(module, required), (
                f"{name} has no `{required}` — the roster names a symbol that "
                f"does not exist, so the gate reports a stub that is not one")

    def test_the_roster_lists_every_engine_module(self):
        """Written down rather than globbed, and pinned here.

        Deriving the roster from the directory at runtime would be
        self-fulfilling — a module that failed to travel would simply not be
        listed, and the check would pass over its absence. So the roster is
        explicit, and this test is what keeps it in step when a module is ADDED.
        `preflight` itself is excluded: it cannot report its own absence.
        `__init__` is excluded from THIS roster and checked by `_guard_error`
        instead, which is a separation and not a gap: the roster is keyed on
        file stems, `__init__.py`'s stem is not its module name, and
        `_cli_dispatch_error` would look for a `lib.py` beside preflight that a
        package does not have. The class below is what keeps that half honest.
        """
        on_disk = {p.stem for p in (REPO / "agents" / "lib").glob("*.py")}
        listed = {e[0].rsplit(".", 1)[-1] for e in P.ENGINE_MODULES}
        assert listed == on_disk - {"__init__", "preflight"}

    def test_the_package_itself_is_checked_somewhere(self):
        """The roster's exclusion above is only safe while this holds.

        Two exclusions sit on one line and they are not alike: preflight cannot
        report its own absence, and there is nothing to be done about that;
        `__init__.py` CAN be reported, and the line would read the same either
        way if nobody checked.
        """
        assert P._GUARD_MODULE == "agents.lib"
        assert P._GUARD_SYMBOL == "host_interpreter_defect"
        assert hasattr(P, "_guard_error")

    def test_every_module_names_what_is_lost_with_it(self):
        """A failure line is only actionable if it says what stopped working."""
        assert all(role.strip() and sym.strip() for _, role, sym in P.ENGINE_MODULES)
        assert set(P.GATE_MODULES) <= set(P.ENGINE_MODULES)

    def test_an_engine_that_computes_wrongly_blocks(self):
        """Importable is not the same as correct.

        A bare existence check passes here, which is why the known-answer probe
        exists: this is the shape of a stale or half-applied copy of the engine.
        """
        class WrongEngine:
            @staticmethod
            def compute_bracket_tax(income, brackets):
                return 999.99, []

        assert P._probe(WrongEngine()) != ""
        assert "not computing correctly" in P._probe(WrongEngine())

    def test_a_raising_engine_blocks_without_propagating(self):
        class ExplodingEngine:
            @staticmethod
            def compute_bracket_tax(income, brackets):
                raise RuntimeError("boom")

        assert "RuntimeError" in P._probe(ExplodingEngine())

    def test_a_module_calling_sys_exit_at_import_is_a_block_not_a_crash(
            self, tmp_path, monkeypatch):
        """`_load` catches SystemExit, which is NOT an Exception.

        The previous body asserted against a module that does not exist, so it
        exercised the missing-FILE branch and never reached the import at all —
        narrowing the handler to `except Exception` left it green. Uncaught, a
        top-level `sys.exit()` propagates out of `check()`, is caught by
        `_main`, and reports exit 1 ("the check could not run") for what the
        module docstring defines as exit 2 ("BLOCKED"): a broken engine read as
        a broken checker, which is the confusion this test is named for.
        """
        pkg = tmp_path / "exiting_pkg"
        pkg.mkdir()
        _complete_package(pkg)
        (pkg / "boom.py").write_text("import sys\nsys.exit(3)\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.setattr(P, "_HERE", pkg)     # the file check must pass
        module, (error, needs_redaction, _fm) = P._load("exiting_pkg.boom")
        assert module is None
        assert "SystemExit" in error, error
        assert needs_redaction, (
            "an exception's own text can name a path and must be redacted")

    def test_the_probe_uses_synthetic_brackets_not_real_tax_law(self):
        """A real bracket here would rot (AGENTS.md §2).

        It would also mislabel the failure: a stale figure would present as a
        broken engine, sending the reader to reinstall rather than to refetch.
        """
        rates = {b["rate"] for b in P._PROBE_BRACKETS}
        assert rates == {0.10, 0.20}
        assert P._PROBE_EXPECTED == 300.0


class TestInterpreter:
    """A verdict about the wrong interpreter is not a verdict.

    The skills chain `source .venv/bin/activate` into every engine command
    because some agents start a fresh shell per command. When that chaining
    does not happen, this check would certify one interpreter while
    `python3 -m agents.lib.projection` runs on another — on a stock macOS,
    /usr/bin/python3, a 3.9 this toolkit does not support.
    """

    def _intact_venv(self, root, built_at=None):
        """A `.venv` with nothing wrong with it but which we are not running in.

        The skeleton matters: a bare directory is a BROKEN venv, and most of
        these tests are about the other cause. `activate` names its own venv,
        as a real one does — an empty file would pass the relocation check by
        having no path in it to contradict, which is a weaker thing to assert.
        `built_at` overrides that to model a venv that has since been MOVED.
        """
        venv = root / ".venv"
        bin_dir = venv / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "activate").write_text(
            f'export VIRTUAL_ENV={built_at or venv}\n'
            'PATH="$VIRTUAL_ENV/bin:$PATH"\n')
        (bin_dir / "python3").write_text("")
        return venv

    def _pretend(self, monkeypatch, prefix, base="/usr", executable=None):
        monkeypatch.setattr(sys, "prefix", str(prefix))
        monkeypatch.setattr(sys, "base_prefix", base)
        if executable:
            monkeypatch.setattr(sys, "executable", str(executable))

    def test_no_workspace_venv_is_not_a_mismatch(self, tmp_path):
        """Nothing to be inconsistent WITH — the host interpreter is the one."""
        assert P._venv_error(tmp_path)[0] == ""

    def test_a_workspace_venv_this_interpreter_is_not_in_blocks(self, tmp_path,
                                                                monkeypatch):
        self._intact_venv(tmp_path)
        self._pretend(monkeypatch, "/usr", base="/usr")  # no venv at all
        error, _defect = P._venv_error(tmp_path)
        assert "no virtualenv" in error
        assert P.check(modules=(), workspace=tmp_path).blocking

    def test_a_different_virtualenv_blocks_and_names_it(self, tmp_path, monkeypatch):
        self._intact_venv(tmp_path)
        other = tmp_path / "some-other-venv"
        other.mkdir()
        self._pretend(monkeypatch, other)
        assert str(other) in P._venv_error(tmp_path)[0]

    def test_running_inside_the_workspace_venv_passes(self, tmp_path, monkeypatch):
        venv = tmp_path / ".venv"
        venv.mkdir()
        self._pretend(monkeypatch, venv)
        assert P._venv_error(tmp_path)[0] == ""

    def test_the_symlinked_executable_is_not_the_test(self, tmp_path, monkeypatch):
        """`sys.prefix`, never `sys.executable` — this was the trap.

        A venv's `bin/python3` is a symlink chain out to the base interpreter,
        so resolving the EXECUTABLE lands outside the venv. Testing that would
        have blocked every correctly activated run.
        """
        venv = tmp_path / ".venv"
        (venv / "bin").mkdir(parents=True)
        base = tmp_path / "elsewhere-python"
        base.write_text("")
        (venv / "bin" / "python3").symlink_to(base)
        self._pretend(monkeypatch, venv, executable=venv / "bin" / "python3")
        assert Path(sys.executable).resolve() == base  # escapes the venv
        assert P._venv_error(tmp_path)[0] == ""

    def test_the_remedy_is_activation_not_a_reclone(self):
        """A refusal states the remedy for the failure AT HAND.

        "Re-clone the toolkit" is useless advice to someone whose only problem
        is an unactivated virtualenv, and following it does not fix anything.
        The activation therefore comes FIRST — the fallback below it is for a
        reader who has already tried that, and inverting the two would answer a
        forgotten `source` with `rm -rf`.
        """
        report = P.PreflightReport("3.12.0", True, (), "", 20,
                                   "`.venv` exists but this check is running "
                                   "under no virtualenv")
        rendered = P.render_markdown(report)
        # The dot form, which is what the skills chain — `source` survives only
        # inside the rebuild block below, where a shell is already known.
        assert "sh agents/lib/run.sh" in rendered
        assert "git clone" not in rendered
        assert rendered.index("sh agents/lib/run.sh") < rendered.index(
            "rm -rf .venv")

    def test_the_activation_remedy_names_an_escape_from_its_own_loop(self):
        """The diagnosis enumerates defects, so an unenumerated one lands here.

        A reader who chained the activation and was refused in the same words
        has no route out of this remedy — `tax-setup` is none, it stops at this
        same gate. The condition is one they can check and this check cannot,
        so it is spelled out rather than left to be inferred.
        """
        report = P.PreflightReport("3.12.0", True, (), "", 20,
                                   "`.venv` exists but this check is running "
                                   "under no virtualenv")
        rendered = P.render_markdown(report)
        assert "ALREADY chained it" in rendered
        assert "rm -rf .venv" in rendered

    def test_a_broken_engine_still_gets_the_reclone_remedy(self):
        report = P.check(modules=(("agents.lib.nope", "the tie-out gate", "x"),))
        assert "git clone" in P.render_markdown(report)

    def test_an_unusable_venv_is_told_to_rebuild_not_to_activate(self, tmp_path,
                                                                 monkeypatch):
        """The two causes need opposite advice, so they are told apart.

        `.venv` present but with no activate script: the chaining DID run and
        `source` failed. Answering that with "chain the activation" sends the
        reader round the same loop, and `tax-setup` is no escape — it stops at
        this same gate before reaching the step that would rebuild.
        """
        (tmp_path / ".venv").mkdir()
        self._pretend(monkeypatch, "/usr", base="/usr")
        report = P.check(modules=(), workspace=tmp_path)
        assert report.venv_broken
        assert "cannot have succeeded" in report.venv_error
        rendered = P.render_markdown(report)
        assert "rm -rf .venv" in rendered
        assert "stops here too" in rendered

    def test_an_intact_venv_that_is_merely_unactivated_is_told_to_activate(
            self, tmp_path, monkeypatch):
        self._intact_venv(tmp_path)
        self._pretend(monkeypatch, "/usr", base="/usr")
        report = P.check(modules=(), workspace=tmp_path)
        assert not report.venv_broken
        rendered = P.render_markdown(report)
        assert "has been moved" not in rendered
        assert "neither relocatable nor repairable" not in rendered

    def test_a_dangling_interpreter_symlink_counts_as_broken(self, tmp_path,
                                                             monkeypatch):
        """The common defect: the base interpreter moved or was upgraded away.

        `bin/activate` is a real file and survives; `bin/python3` is a symlink
        into the base install and is left dangling. `.exists()` follows it,
        which is why the check uses that rather than `is_symlink`/`lexists`.
        """
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text("")
        (venv / "python3").symlink_to(tmp_path / "removed-by-an-upgrade")
        self._pretend(monkeypatch, "/usr", base="/usr")
        assert "no interpreter behind it" in P._venv_error(tmp_path)[0]
        assert P.check(modules=(), workspace=tmp_path).venv_broken


class TestRelocatedVenv:
    """A moved workspace: every file survives and activating it does nothing.

    Rename the directory holding a `.venv` and nothing looks wrong — `activate`
    is a real file, `bin/python3` is a symlink into a base interpreter that
    never moved. But `activate` exports the absolute path it was BUILT at and
    prepends a `bin` that no longer exists, so `source` returns 0, prints
    nothing, and leaves `python3` resolving to the host interpreter. Answering
    that refusal with "chain the activation" is a closed loop: the reader has
    already done exactly that.
    """

    def _venv(self, root, activate_body):
        bin_dir = root / ".venv" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "activate").write_text(activate_body)
        (bin_dir / "python3").write_text("")
        return root / ".venv"

    def test_a_moved_venv_is_told_to_rebuild_not_to_activate(self, tmp_path,
                                                             monkeypatch):
        self._venv(tmp_path, f"export VIRTUAL_ENV={tmp_path}/old-name/.venv\n")
        monkeypatch.setattr(sys, "prefix", "/usr")
        monkeypatch.setattr(sys, "base_prefix", "/usr")
        report = P.check(modules=(), workspace=tmp_path)
        assert report.venv_broken
        assert "has been moved since" in report.venv_error
        assert f"{tmp_path}/old-name/.venv" in report.venv_error
        assert "rm -rf .venv" in P.render_markdown(report)

    def test_the_verdict_does_not_deny_what_the_reader_just_saw(self, tmp_path,
                                                                monkeypatch):
        """`source` SUCCEEDS on a moved venv — it just changes nothing.

        The other defects make it fail, and share a "cannot have succeeded"
        clause. Reusing that here contradicts the reader's own terminal on the
        first line, and advice contradicted there does not get read to its
        second.
        """
        self._venv(tmp_path, f"export VIRTUAL_ENV={tmp_path}/gone/.venv\n")
        monkeypatch.setattr(sys, "prefix", "/usr")
        monkeypatch.setattr(sys, "base_prefix", "/usr")
        assert "cannot have succeeded" not in P._venv_error(tmp_path)[0]

    def test_an_activate_naming_this_very_venv_is_not_an_accusation(
            self, tmp_path, monkeypatch):
        venv = self._venv(tmp_path, f'VIRTUAL_ENV="{tmp_path}/.venv"\n')
        monkeypatch.setattr(sys, "prefix", "/usr")
        monkeypatch.setattr(sys, "base_prefix", "/usr")
        assert P._activate_target(venv / "bin" / "activate") == f"{tmp_path}/.venv"
        assert not P.check(modules=(), workspace=tmp_path).venv_broken

    def test_a_symlinked_path_to_the_same_venv_is_the_same_venv(self, tmp_path,
                                                                monkeypatch):
        """`samefile`, not string equality — the paths are allowed to differ.

        Reaching a workspace through a symlink, or on a case-insensitive
        filesystem, gives a spelling of the path that is not the one baked in
        while naming the same directory. Comparing text would call that a
        relocation and send a working setup to `rm -rf`.
        """
        real = tmp_path / "real"
        real.mkdir()
        self._venv(real, f"export VIRTUAL_ENV={real}/.venv\n")
        link = tmp_path / "link"
        link.symlink_to(real)
        monkeypatch.setattr(sys, "prefix", "/usr")
        monkeypatch.setattr(sys, "base_prefix", "/usr")
        assert not P.check(modules=(), workspace=link).venv_broken

    def test_a_computed_activate_path_is_not_read_as_a_literal(self, tmp_path):
        """3.14 writes a `$(cygpath …)` assignment above the plain one.

        A script that DERIVES its own location is by construction not broken by
        being moved, so an unparseable assignment must mean "no literal to
        contradict" and never "relocated".
        """
        venv = self._venv(tmp_path, 'VIRTUAL_ENV=$(cygpath /somewhere/else)\n')
        assert P._activate_target(venv / "bin" / "activate") is None
        assert P._relocated(venv, venv / "bin" / "activate") == ""

    def test_the_cygwin_line_does_not_mask_the_real_one(self, tmp_path):
        """Both forms are present in a real 3.14 activate, dynamic one first."""
        venv = self._venv(tmp_path, "VIRTUAL_ENV=$(cygpath /built/at/.venv)\n"
                                    "export VIRTUAL_ENV\n"
                                    "export VIRTUAL_ENV=/built/at/.venv\n")
        assert P._activate_target(venv / "bin" / "activate") == "/built/at/.venv"

    def test_unset_and_bare_export_are_not_assignments(self, tmp_path):
        venv = self._venv(tmp_path, "unset VIRTUAL_ENV\nexport VIRTUAL_ENV\n")
        assert P._activate_target(venv / "bin" / "activate") is None

    def test_a_real_venv_moved_on_disk_is_caught(self, tmp_path, monkeypatch):
        """Replay against CPython's own `activate`, not a hand-written one.

        The fixtures above encode what this suite BELIEVES that file looks
        like. Only building one and moving it can say whether the belief still
        holds on the interpreter running the suite — the quoting changed
        between 3.9 and 3.14, and nothing warns when it changes again.
        """
        import venv as venv_mod
        built = tmp_path / "before"
        built.mkdir()
        try:
            venv_mod.EnvBuilder(with_pip=False).create(built / ".venv")
        except (OSError, RuntimeError) as e:  # sandbox with no venv support
            pytest.skip(f"cannot build a virtualenv here: {e}")
        moved = tmp_path / "after"
        built.rename(moved)
        monkeypatch.setattr(sys, "prefix", "/usr")
        monkeypatch.setattr(sys, "base_prefix", "/usr")

        assert (moved / ".venv" / "bin" / "activate").exists()   # looks intact
        assert (moved / ".venv" / "bin" / "python3").exists()    # symlink lives
        report = P.check(modules=(), workspace=moved)
        assert report.venv_broken, "a real moved venv read as merely unactivated"
        assert str(built / ".venv") in report.venv_error

    def test_a_real_venv_left_alone_is_not_accused(self, tmp_path, monkeypatch):
        """The same build, unmoved — the check must stay silent about it.

        Built under a path with a SPACE in it, because that is where a parser
        reading a shell assignment back gets its false positive: CPython quotes
        the value when it needs to, and reading the quotes as part of the path
        would name a directory that cannot exist and send a working workspace
        to `rm -rf`. `~/My Documents/…` is an ordinary place to keep a return.
        """
        import venv as venv_mod
        root = tmp_path / "my tax workspace"
        root.mkdir()
        try:
            venv_mod.EnvBuilder(with_pip=False).create(root / ".venv")
        except (OSError, RuntimeError) as e:
            pytest.skip(f"cannot build a virtualenv here: {e}")
        monkeypatch.setattr(sys, "prefix", "/usr")
        monkeypatch.setattr(sys, "base_prefix", "/usr")
        assert not P.check(modules=(), workspace=root).venv_broken


class TestPythonVersionRemedy:
    def test_the_remedy_changes_the_interpreter_rather_than_reusing_it(self):
        """A remedy that cannot resolve what was reported is worse than none.

        Under 3.9 the engine remedy is `git clone` + `python3 -m venv`, and a
        venv inherits the version of the `python3` that builds it — so the
        reader follows it, lands on 3.9 again, and every skill stays blocked.
        """
        report = P.PreflightReport("3.9.6", False, (), "", 20)
        rendered = P.render_markdown(report)
        assert P.MIN_PYTHON_STR in rendered
        assert "inherits the version" in rendered
        assert "python3.12 -m venv" in rendered

    def test_an_old_python_does_not_get_the_reclone_remedy_at_all(self):
        """3.9 fails the engine's own imports — ten modules use PEP 604 — so an
        old interpreter PRESENTS as a missing engine.

        This used to assert only that the version remedy came FIRST and the
        clone remedy second. Ordering was the wrong fix: inside a complete
        checkout `git clone` fails on its first line ("destination path already
        exists") and `cd tax-prep-skills` on its second, under a sentence
        claiming the engine did not travel. The clone remedy is now reserved for
        a failure that is actually a missing FILE.
        """
        failure = P.ModuleFailure("agents.lib.tie_out", "the tie-out gate", "boom")
        rendered = P.render_markdown(
            P.PreflightReport("3.9.6", False, (failure,), "", 20))
        assert "inherits the version" in rendered
        assert "git clone" not in rendered, (
            "a complete clone was told to re-clone itself")

    def test_a_missing_file_does_get_the_reclone_remedy(self):
        """The counterweight, and the case the clone remedy is for."""
        failure = P.ModuleFailure(
            "agents.lib.tie_out", "the tie-out gate",
            "FileNotFoundError: no tie_out.py beside preflight.py", False, True)
        rendered = P.render_markdown(
            P.PreflightReport("3.12.0", True, (failure,), "", 20))
        assert "git clone" in rendered
        assert "separated from the code it runs" in rendered
        # And NOT the interpreter caveat: the files are gone, which no version
        # change alters. That paragraph over a list of FileNotFoundErrors is
        # what sent a reader to upgrade Python and come back to the same block.
        assert "symptoms of the interpreter" not in rendered

    def test_the_minimum_is_stated_once(self):
        """Derived, not retyped: the remedy and the failure line cannot drift
        apart into telling the reader two different minimums."""
        assert P.MIN_PYTHON_STR == ".".join(str(p) for p in P.MIN_PYTHON)
        assert P.MIN_PYTHON_STR in P.python_remedy()


class TestCLI:
    def _run(self, cwd):
        return subprocess.run(
            [sys.executable, "agents/lib/preflight.py"],
            cwd=cwd, capture_output=True, text=True)

    def test_exit_zero_in_a_full_clone(self):
        result = self._run(REPO)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK" in result.stdout

    def test_a_runtime_path_in_a_failure_is_redacted(self):
        """`agents/lib/` is inside the redaction gate, and this module joined it.

        Every string here names a path — the workspace, its `.venv`, the
        interpreter — or carries an OSError that names one in its own text, and
        AGENTS.md §1 makes a document NAME the disclosure. The gate in
        `test_secure_fs.py` derives its module list from `agents/lib/*.py`, so
        it started covering `preflight.py` the day this branch added it.
        """
        report = P.PreflightReport(
            "3.12.0", True, (), "", 21,
            venv_error="`/w/W2_000-00-0000/.venv` exists but this check is "
                       "running under no virtualenv")
        out = P.render_markdown(report)
        assert "000-00-0000" not in out
        assert "[REDACTED]" in out

    def test_the_verdict_survives_a_missing_redactor(self):
        """The counterweight, and it is the reason redaction is not applied to
        the whole document.

        `secure_fs` is part of the engine being checked, so the scenario this
        module exists for — a skill copied away from `agents/lib/` — is exactly
        the one where there is nothing to redact WITH. Wrapping the rendered
        verdict withheld all of it: the block, the module list and the remedy
        are authored constants that never carried a path, and an agent that is
        told nothing cannot act on the refusal.
        """
        report = P.PreflightReport(
            "3.12.0", True,
            (P.ModuleFailure("agents.lib.tie_out", "the tie-out gate",
                             "ModuleNotFoundError: agents.lib.tie_out"),),
            "", 21)
        import builtins
        real_import = builtins.__import__

        def no_secure_fs(name, *a, **k):
            if name == "agents.lib.secure_fs":
                raise ModuleNotFoundError(name)
            return real_import(name, *a, **k)

        builtins.__import__ = no_secure_fs
        try:
            out = P.render_markdown(report)
        finally:
            builtins.__import__ = real_import
        assert "BLOCKED" in out
        assert "the tie-out gate" in out
        assert "How to proceed" in out and out.rstrip().endswith("."), (
            "the remedy was withheld with the diagnosis")
        assert "not the complete module" in out, out[-200:]

    def test_exit_two_when_the_engine_is_absent(self, tmp_path):
        """The realistic failure: a skill copied without its sibling engine."""
        lib = tmp_path / "agents" / "lib"
        lib.mkdir(parents=True)
        shutil.copy(REPO / "agents" / "lib" / "preflight.py", lib / "preflight.py")

        result = self._run(tmp_path)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "BLOCKED" in result.stdout
        assert "Do not compute" in result.stdout
        assert "git clone" in result.stdout

    def test_json_output_carries_the_verdict(self):
        import json
        result = subprocess.run(
            [sys.executable, "agents/lib/preflight.py", "--json"],
            cwd=REPO, capture_output=True, text=True)
        assert result.returncode == 0
        assert json.loads(result.stdout)["blocking"] is False


class TestSkillGate:
    def test_every_engine_calling_skill_carries_the_gate(self):
        missing = sorted(name for name in _engine_calling_skills_cached()
                         if GATE_HEADING not in skill_text(name))
        assert not missing, (
            f"these skills invoke agents/lib/ without first checking it exists: "
            f"{missing}. A skill copied away from the engine runs its gates as "
            f"commands that silently do nothing.")

    def test_the_roster_recognises_both_invocation_forms(self):
        """The predicate itself, driven directly.

        A roster that silently shrinks is the failure mode here, and shrinkage
        is invisible from the assertions above — they iterate whatever survives.
        The module form was the half that went missing.
        """
        assert _calls_engine("run `python3 agents/lib/tie_out.py --workspace .`")
        assert _calls_engine("run `python3 -m agents.lib.projection --year 2025`")
        assert not _calls_engine("read `summaries/` and write `answers.md`")

        # ...and the separator may be BACKSLASH-QUOTED, which the shell removes
        # before python is handed the argument. This is the whole-FILE version
        # of the same silence `_shell_unescape` fixes per command, and the worse
        # one: a skill that spells it this way is never enrolled, so its engine
        # calls are not audited for activation and it is not required to carry a
        # preflight gate at all.
        assert _calls_engine("run `python3 agents\\/lib\\/tie_out.py`")
        assert _calls_engine("run `python3 -m agents\\.lib\\.projection`")
        assert not _calls_engine("read the agents docs and the lib summaries")

    def test_the_gate_is_not_vacuous(self):
        """Guards the roster, not just the skills.

        If `_engine_calling_skills_cached()` ever returns nothing — a moved directory,
        a renamed path — the assertion above passes over an empty set and stops
        covering anything, which reads identically to full coverage.
        """
        found = _engine_calling_skills_cached()
        assert len(found) >= 20, f"roster collapsed to {sorted(found)}"
        assert "tax-compute" in found
        assert "tax-project" in found

    def test_the_launcher_is_what_makes_the_roster_universal(self):
        """This replaces a test whose premise the launcher retired.

        That test proved the roster still needed the MODULE form by finding
        skills that reached the engine only that way. There are none now: every
        invocation goes through `sh agents/lib/run.sh`, a PATH form, so the
        roster is universal for a different reason than it used to be.

        What is load-bearing now is the launcher's own universality — if a skill
        stops routing through it, `_calls_engine` is back to carrying the
        distinction alone, and `test_the_roster_recognises_both_invocation_forms`
        (which drives the predicate on raw strings, not on the tree) is the only
        thing that still catches a path-only regression.
        """
        without_launcher = []
        for name in _engine_calling_skills_cached():
            units = [u for _, _, u, _ in _units(name)
                     if RUNS_PYTHON.search(u) and _calls_engine(u)]
            if units and not any(LAUNCHER in u for u in units):
                without_launcher.append(name)
        assert not without_launcher, (
            f"{without_launcher} reach the engine without the launcher, so the "
            f"roster's coverage rests on _calls_engine matching both forms "
            f"again — see test_the_roster_recognises_both_invocation_forms")
        # And the module form still has to be matched: it is what the launcher
        # is HANDED (`run.sh -m agents.lib.projection`), and a future skill
        # could call it directly.
        assert _calls_engine("python3 -m agents.lib.projection --year 2025")

    def test_an_engine_free_skill_is_exempt_by_declaration_not_by_silence(self):
        """`ENGINE_FREE_SKILLS` membership is a claim, and this is the check.

        A skill with no engine call falls out of the derived roster on its own,
        so "engine-free by design" and "lost its engine call in an edit" are the
        same observation from the gate's side — both simply stop being iterated.
        The declaration exists to tell them apart, which only works if the
        declared property is asserted rather than assumed.

        Read the other way, the exemption is the obvious escape hatch: name a
        skill here and its gate requirement disappears. That is what this test
        forecloses — a declared skill that grows an engine call fails HERE, so
        the only way to add one is to remove the declaration and take the gate.

        Deliberately whole-text and NOT per-`_shell_units`, unlike every positive
        audit in this file. `_shell_units` exists because an inline `-c` body puts
        `python3` and the engine import on different LINES, so a per-line read of
        a command that spans lines sees neither half — a blind spot when PROVING
        a call is covered. Proving ABSENCE has no such spot: every unit is a
        substring of the concatenated text, so a unit-level failure implies a
        text-level one and the loop could never fire on its own. Iterating it
        anyway would look like a third form was being checked separately when it
        was not.
        """
        for name in sorted(ENGINE_FREE_SKILLS):
            skill_dir = SKILLS_DIR / name
            assert skill_dir.is_dir(), f"{name} is declared engine-free but does not exist"
            assert not _calls_engine(skill_text(name)), (
                f"{name} is declared engine-free but its text reaches the engine "
                f"by path or module. Either drop the declaration and carry the "
                f"preflight gate, or drop the engine call.")

    def test_the_engine_free_declaration_cannot_overlap_the_two_other_rosters(self):
        """Three sets, three meanings, no double citizenship.

        Overlap with the derived roster is a contradiction — a skill cannot both
        call the engine and be declared not to — and it is the shape the escape
        hatch would take, so it fails as a set operation regardless of what the
        per-skill assertion above finds. Overlap with `CONTRIBUTOR_SKILLS` is
        different and still wrong: those are exempt because they run from a clone
        that HAS the engine, not because they avoid it, and collapsing the two
        exemptions would let a contributor skill's engine calls go unchecked.
        """
        assert not (ENGINE_FREE_SKILLS & _engine_calling_skills_cached())
        assert not (ENGINE_FREE_SKILLS & CONTRIBUTOR_SKILLS)

    def test_the_three_rosters_partition_the_skills_tree(self):
        """What makes the declaration load-bearing: a statement about the TREE.

        Every assertion in this class ranges over a set that a regression
        SHRINKS, and shrinkage is the failure mode. A skill whose last engine
        call is deleted leaves the derived roster silently — the gate assertions
        simply stop iterating it, `test_the_gate_is_not_vacuous`'s floor of 20
        absorbs the loss, and `ENGINE_FREE_SKILLS` never hears about it. Deleting
        a name FROM that declaration is the same hole entered from the other
        side: nothing then requires the skill to be either gated or declared, so
        the exemption can be dropped without the gate arriving to replace it.

        Neither hole is closeable by talking about either set, because both are
        satisfied by a smaller tree. Closing them takes coverage of the whole
        directory: every skill either reaches the engine (and carries the gate,
        per the assertions below), or is declared engine-free (and is checked to
        stay that way, per the assertion above), or is contributor tooling. A
        skill can then only leave one roster by joining another, and every such
        move is an edit to `tests/skill_text.py` rather than a silent effect of
        editing a skill.
        """
        on_disk = {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()}
        assert on_disk, "the skills tree did not resolve — every roster is empty"
        classified = (_engine_calling_skills_cached() | ENGINE_FREE_SKILLS
                      | CONTRIBUTOR_SKILLS)
        assert not (on_disk - classified), (
            f"{sorted(on_disk - classified)} neither reach the engine nor are "
            f"declared in ENGINE_FREE_SKILLS. A skill that lost its last engine "
            f"call drops out of the gate with no other signal: either restore "
            f"the call or declare it engine-free and take the assertion that it "
            f"stays engine-free.")
        assert not (classified - on_disk), (
            f"{sorted(classified - on_disk)} is declared in a roster but is not "
            f"a skill directory — a rename left the declaration behind, and a "
            f"declaration naming nothing exempts nothing while looking current")

    def test_the_engine_free_assertion_has_teeth_on_an_empty_declaration(self):
        """The positive control, because the set above starts and may return empty.

        Iterating an empty `ENGINE_FREE_SKILLS` passes while proving nothing,
        and reads exactly like a set of skills that were all checked. This drives
        the predicate the assertion depends on over all three invocation forms,
        so the check keeps a failing case even at zero members.
        """
        assert _calls_engine("sh agents/lib/run.sh agents/lib/preflight.py")
        assert _calls_engine("sh agents/lib/run.sh -m agents.lib.projection")
        assert _calls_engine('run.sh -c "\nfrom agents.lib.tie_out import x\n"')
        assert not _calls_engine("multiply line 24 by 1.10 and show your work")

    @pytest.mark.parametrize("skill", sorted(_engine_calling_skills_cached()))
    def test_the_gate_names_the_command_and_fails_closed(self, skill):
        text = skill_text(skill)
        assert GATE_COMMAND in text
        # The load-bearing sentence: the script cannot report its own absence,
        # so "the command did not run" must itself be the blocking condition.
        # Without this an agent reasonably reads a missing file as a step to
        # skip, and every hard gate below it vanishes silently.
        assert "including the command failing because the file does not" in text
        assert "IS the blocking condition" in text

    @pytest.mark.parametrize("skill", sorted(_engine_calling_skills_cached()))
    def test_the_gate_lives_in_the_skill_body_not_a_reference(self, skill):
        """A gate the agent can decline to load is not a gate.

        The assertions above read `skill_text()`, which concatenates `SKILL.md`
        with every `references/*.md` — so a gate MOVED into a reference file
        satisfies all of them while the agent only opens the reference if it
        chooses to. That is not hypothetical pressure: `tax-parse` sits at 485
        of the 500-line cap and this gate costs 14 lines, so the next size
        squeeze points straight at it.

        The repo already settled this one way. `test_web_dependent_skills_
        carry_a_stale_data_degrade` requires its STOP sentence in `SKILL.md`
        itself for exactly this reason, and progressive disclosure is for step
        DETAIL — never for the precondition that decides whether the step runs.
        """
        body = (SKILLS_DIR / skill / "SKILL.md").read_text()
        assert GATE_HEADING in body, (
            f"{skill}'s engine-preflight gate is not in SKILL.md — a gate in "
            f"references/ is one the agent can skip by not reading it")
        assert GATE_COMMAND in body
        assert "IS the blocking condition" in body

    @pytest.mark.parametrize("skill", sorted(_engine_calling_skills_cached()))
    def test_the_gate_precedes_the_first_engine_invocation(self, skill):
        """Ordering, which nothing else pins.

        A gate that runs after the first engine command has already let that
        command run against an engine nobody checked. It holds today in all
        twenty skills; without this it holds by habit.
        """
        body = (SKILLS_DIR / skill / "SKILL.md").read_text()
        gate_at = body.index(GATE_COMMAND)
        engine_at = [body.index(u) for _, _, u, _ in _units(skill)
                     if RUNS_PYTHON.search(u) and _calls_engine(u)
                     and "preflight" not in u and u in body]
        # A bare `if engine_at:` would let this test go quietly vacuous per
        # skill: the comprehension drops units from `references/*.md`, so a skill
        # that moves its last SKILL.md engine command into a reference — which
        # tax-parse, at 485 of the 500-line cap, is under pressure to do — would
        # pin nothing while still reporting green. That is the same vacuity shape
        # this file documents three times, in a test added to close a vacuity
        # finding. The skill either has an in-body engine call to order against,
        # or it says so where a reader can see it.
        assert engine_at or skill in GATE_ONLY_SKILLS, (
            f"{skill} has no engine call in SKILL.md, so this test pins nothing "
            f"for it — add it to GATE_ONLY_SKILLS if that is intended")
        if engine_at:
            assert gate_at < min(engine_at), (
                f"{skill} invokes the engine before its preflight gate")

    @pytest.mark.parametrize("skill", sorted(_engine_calling_skills_cached()))
    def test_every_engine_invocation_activates_the_workspace_venv(self, skill):
        """The preflight can only vouch for the interpreter it runs on.

        So the engine has to run on that same one, and the activation is
        chained into EVERY invocation rather than stated once at the top of a
        block: some agents start a fresh shell per command (AGENTS.md §7), and
        an earlier `source` does not survive it. One unchained call is a
        computation running on an interpreter nothing checked.

        Chained into the invocation, which is narrower than "present in the
        unit": both halves of the question are asked of ONE command, so a
        launcher on the far side of a `||` no longer vouches for a bare one.

        Asked of the unit, the two halves float free of each other and the
        answer is wrong in both directions at once. `{launcher} -m
        agents.lib.tie_out && python3 -m pytest tests/ -q` indicts a correct
        command, the engine call and the bare interpreter having nothing to do
        with one another; `python3 -m agents.lib.tie_out && {launcher} -m pip
        --version` clears a real offender, a launcher that runs no engine
        vouching for a bare interpreter that does. Neither is a near miss — each
        is the audit reading one command's launcher against another's engine
        call.

        A command counts as an invocation when it both RUNS python and names the
        engine — prose about `agents/lib/` carries no `python3`, and the line
        warning against inline `-c` strings names no engine module.
        """
        offenders = [f"{path}:{n}"
                     for path, n, _ in _bare_engine_units(_units(skill))]
        assert not offenders, (
            f"engine invoked without activating the workspace venv: {offenders}. "
            f"Route each through `{LAUNCHER}`.")

    def test_the_activation_audit_reads_the_command_not_the_line(self):
        """The control on `_unactivated`, which the audit above IS. It runs over
        a corpus that is currently CLEAN, so on its own it cannot tell a corpus
        with no offender from a predicate that finds none.

        The failures come in opposite directions and the mixed unit hits both at
        once. Asked of the whole unit, "runs python", "calls the engine" and
        "carries the launcher" are three independent facts about a LINE, and a
        line is not what runs — so a launcher belonging to one command answers
        for another command's engine call, and a pytest run answers for a
        routed one. Asked per command they are one fact about one thing.

        `_calls_engine` gets its own controls because it decides whether a skill
        is audited AT ALL: too tight, it stops at a dot, the package-level
        import makes the skill not an engine caller, and it gets neither an
        activation audit nor a required preflight gate.
        """
        for bare in (
                # The launcher's own fallback: the engine runs on `python3`
                # exactly when `run.sh` fails, which is the stock-interpreter
                # host this gate exists to catch.
                f"{LAUNCHER} -m agents.lib.preflight || python3 -m agents.lib.tie_out",
                # A launcher that runs no engine, vouching for a bare
                # interpreter that does.
                f"python3 -m agents.lib.tie_out && {LAUNCHER} -m pip --version",
                # The separator inside the quoted body is not a separator.
                "python3 -c 'import sys; from agents.lib import ptet'",
                # ...and an ESCAPED quote does not end the body. Untracked, this
                # one splits at the `;` — the interpreter on one side, the engine
                # import on the other, neither command an invocation, and the
                # audit clean on a bare engine call. A split, not the merge the
                # splitter was documented as being able to make.
                'python3 -c "print(\\"a;b\\"); from agents.lib import ptet"',
                # The launcher MENTIONED rather than run, in the two places a
                # document actually puts it. Both cleared the audit while the
                # engine ran on whatever `python3` resolves to, because the
                # predicate searched the command for the literal instead of
                # asking which interpreter reaches the engine.
                f"python3 -m agents.lib.ptet   # use {LAUNCHER} instead",
                f"""python3 -c 'print("{LAUNCHER}"); from agents.lib import ptet'""",
                # An environment VALUE is not a command word. This runs the
                # engine on a stock interpreter while the launcher sits in an
                # assignment, which is the mention above with the literal moved
                # somewhere a lookbehind mistakes for the executor.
                "RUNNER=agents/lib/run.sh python3 -m agents.lib.ptet",
                # ...and the same assignment behind an exec prefix, which is
                # where stripping assignments STOPS finding it: `ASSIGNMENTS` is
                # anchored, `env` is not an assignment, and four characters in
                # front of the case above put the whole construct back.
                "env RUNNER=agents/lib/run.sh python3 -m agents.lib.ptet",
                "/usr/bin/env RUNNER=agents/lib/run.sh python3 agents/lib/ptet.py",
                # bash spells a redirection with the character that separates
                # two commands. `&>` is ONE operator, so this is ONE command —
                # cut at the `&`, the interpreter lands in one half and the
                # engine target in the other, and neither half is an invocation.
                "python3 &>/dev/null -m agents.lib.ptet",
                "python3 &>>run.log -m agents.lib.ptet",
                # A command SUBSTITUTION is a command, and the unquoted value
                # class swallowed one: with no whitespace after the `=` the
                # class ate ``\u0060python3 `` up to the space and handed back
                # ``-m agents.lib.ptet\u0060`` — an engine target with no
                # interpreter in front of it, cleared by both audits.
                "OUT=`python3 -m agents.lib.ptet`",
                # A SUBPACKAGE path. `agents/lib/\\w+\\.py` cannot cross a
                # slash, and there is no such directory today — which is what
                # makes the bound invisible rather than absent.
                "python3 agents/lib/sub/tool.py",
                # The module form under the prefix the IMPORT form has always
                # admitted. With `agents` on the path these load one package,
                # and only one of the two spellings was an engine target — the
                # same rule disagreeing with itself about the same command.
                "PYTHONPATH=agents python3 -m lib.ptet",
                # ...and the same name as the shell word it is: quoted either
                # way, or attached to the option. `_calls_engine` reads all
                # three as engine calls, so a skill spelling it like this was
                # selected for audit and then cleared BY that audit.
                'python3 -m "agents.lib.ptet"',
                "python3 -m 'agents.lib.ptet'",
                "python3 -magents.lib.ptet",
                'PYTHONPATH=agents python3 -m "lib.ptet"',
                # The `-c` mask may not clear an import that RUNS, and these are
                # the three ways it could. Code beside a masked literal; a body
                # that turns a literal back into code, where `_blank_python`
                # keeps every string; and an operand that is not python at all,
                # which the tokenizer refuses and the scan therefore reads raw.
                """python3 -c 'print("x"); from agents.lib import ptet'""",
                """python3 -c 'exec("from agents.lib import ptet")'""",
                "sh -c 'python3 -m agents.lib.ptet'",
                # A `$( ... )` is a WORD, and `(` is a separator with no `)` to
                # end it — so the enclosing command was cut in half, the
                # interpreter left in one piece and the engine target in the
                # other. Both spellings: the target after the closing bracket,
                # and the target inside it.
                'python3 "$(git rev-parse --show-toplevel)/agents/lib/tie_out.py"',
                'python3 - <<<"$(cat agents/lib/ptet.py)"',
                # The stdlib's OTHER loaders, which a closed list of callee
                # names did not have. Each binds the engine exactly as
                # `__import__` does, and `_blank_python` masked the literal
                # naming it because `DYNAMIC_EXEC` did not read the body as one
                # that loads anything.
                """python3 -c 'import runpy; runpy.run_module("agents.lib.tie_out")'""",
                """python3 -c 'import runpy; runpy.run_path("agents/lib/tie_out.py")'""",
                # ...and the ALIAS, which is the case no list of names can hold:
                # the loader is reached under a name the document invents, and
                # the only thing that admits what the body is doing is the
                # import that created it.
                "python3 -c 'from importlib import import_module as im; "
                """im("agents.lib.tie_out")'""",
                # `runpy` as the module `-m` is given, which makes the NEXT word
                # the module that runs. The target pattern read the word after
                # `-m` and found a stdlib name.
                "python3 -m runpy agents.lib.tie_out",
                # A short-option CLUSTER, on the side that must stay reported:
                # the mask this cluster now reaches may not clear an import that
                # actually runs.
                "python3 -uc 'from agents.lib import ptet'"):
            assert _unactivated(bare), (
                f"an engine call on a bare interpreter passed the audit: {bare!r}")

        # Which quote decides whether a backslash escapes at all, stated against
        # the splitter directly because `_unactivated` cannot see the difference:
        # both spellings of the mistake merge or split around a command that is
        # correctly routed either way, and answer the same.
        assert len(_commands('python3 -c "print(\\"a;b\\")"')) == 1, (
            "an escaped double quote closed the body it is escaped inside")
        assert len(_commands("echo 'a\\' ; echo b")) == 2, (
            "a backslash escaped inside a SINGLE-quoted string, where the shell "
            "has no escapes and the quote closes")

        # Stated against the splitter for the same reason: through
        # `_unactivated` a wrong split and a right one can reach the same
        # verdict, the pattern being a SEARCH over whatever it is handed.
        # `&` is a separator, `&>` and `&>>` and `>&` are not, and one
        # character decides which — so all four are pinned, not just the pair
        # that was wrong.
        for one in ("python3 &>/dev/null -m agents.lib.ptet",
                    "python3 &>>run.log -m agents.lib.ptet",
                    "python3 -m agents.lib.ptet 2>&1"):
            assert len(_commands(one)) == 1, (
                f"a redirection was read as a command separator: {one!r} -> "
                f"{_commands(one)}")
        assert len(_commands(f"{LAUNCHER} -m agents.lib.ptet & wait")) == 2, (
            "a background `&` stopped separating two commands, which is what "
            "makes a launcher on one vouch for a bare interpreter on the other")

        for routed in (f"{LAUNCHER} -m agents.lib.secure_fs answers.md || exit 1",
                       f"mkdir -p x && {LAUNCHER} -m agents.lib.secure_fs x || exit 1",
                       f"find . -print0 | xargs -0 {LAUNCHER} -m agents.lib.secure_fs",
                       # The engine call and the bare interpreter are different
                       # commands with nothing to do with each other.
                       f"{LAUNCHER} -m agents.lib.tie_out && "
                       "python3 -m pytest tests/ -q",
                       # The mirror of the assignment case above, and the reason
                       # both are stated: the same construct with the same shape
                       # holds the interpreter this time, and the command word is
                       # still the launcher. One rule has to get both right.
                       f"TAXPY=python3 {LAUNCHER} -m agents.lib.ptet",
                       # Both mirrors again behind `env`. The prefixed pair is
                       # stated on BOTH sides deliberately: a fix that reached
                       # only the false clear could be had by widening the
                       # lookbehind, and it would report this compliant command.
                       f"env TAXPY=python3 {LAUNCHER} -m agents.lib.ptet",
                       # The redirection above on a command that IS routed. A
                       # separator fix has to leave the compliant form alone,
                       # and it is the half a fail-open change never exercises.
                       f"{LAUNCHER} &>/dev/null -m agents.lib.ptet",
                       # A trailing COMMENT is not executed. This one is routed
                       # and was reported, the pattern being a search over the
                       # whole command: it found the interpreter in the comment,
                       # and the lookbehind asking whether `run.sh` put it there
                       # passed because the word in front of it is `never`. A
                       # gate firing on a document that quotes the rule
                       # accurately is how a reader learns to reason past it.
                       f"{LAUNCHER} -m agents.lib.ptet  # never python3 -m "
                       "agents.lib.ptet",
                       f"OUT=`{LAUNCHER} -m agents.lib.ptet`",
                       # The same sentence one position over. A `-c` operand is
                       # python, and what runs in it is what python's lexer says
                       # runs — a string literal and a `#` comment are text the
                       # interpreter no more executes than the shell executes
                       # `echo 'python3 -m agents.lib.ptet'`. Fed to the SAME
                       # interpreter on stdin this has been read through a lexer
                       # since the heredoc scanner existed; in argv it was
                       # searched raw, so a document quoting the rule accurately
                       # was an offender and its heredoc mirror was not.
                       """python3 -c 'print("from agents.lib import ptet")'""",
                       "python3 -c 'x = 1  # from agents.lib import ptet'",
                       # ...and through the shell escape that makes a
                       # double-quoted operand a different string from the one
                       # the shell hands over.
                       'python3 -c "print(\\"from agents.lib import ptet\\")"',
                       # ...and the same body behind a short-option CLUSTER,
                       # which is the spelling a runbook writes when the output
                       # goes through `tee`. `_interpreter_role` has read a
                       # cluster whole since it was written and the `-c` mask
                       # did not, so the two audits disagreed about one command
                       # over one character — and the disagreement fell on the
                       # side that reports a document for the string it prints.
                       """python3 -uc 'print("from agents.lib import ptet")'""",
                       """python3 -Ic 'print("from agents.lib import ptet")'""",
                       # The substitution rejoin must leave the routed forms
                       # alone, and both directions are stated: the launcher
                       # reached THROUGH a substitution, and a substitution
                       # sitting in the argv of a command that is already
                       # routed. A flatten that joined a subshell to whatever
                       # followed it would report the second.
                       f'sh "$(git rev-parse --show-toplevel)/agents/lib/run.sh"'
                       " -m agents.lib.ptet",
                       f'{LAUNCHER} -m agents.lib.ptet "$(date)"',
                       f"(cd x && python3 -c ok); {LAUNCHER} -m agents.lib.ptet",
                       f'RUN=$(command -v python3) && {LAUNCHER} -m agents.lib.ptet',
                       # `-m runpy` on the launcher is the routed spelling of
                       # the offence two lists up.
                       f"{LAUNCHER} -m runpy agents.lib.tie_out"):
            assert not _unactivated(routed), (
                f"a correctly routed command was read as bare: {routed!r}")

        for engine in ("python3 -c 'from agents.lib import tie_out'",
                       "python3 -m agents.lib.projection",
                       "python3 agents/lib/tie_out.py"):
            assert _calls_engine(engine), (
                f"an engine call went unrecognised, so nothing audits it: "
                f"{engine!r}")
        assert not _calls_engine("python3 -m pytest tests/ -q"), (
            "a non-engine command was read as an engine call")
        # The bound the optional prefix must not cross: `lib` is a word, so a
        # package merely STARTING with it is a different one.
        for other in ("python3 -m libcst.tool", "python3 -m lib2to3"):
            assert not _unactivated(other), (
                f"a package whose name begins with `lib` was read as the "
                f"engine: {other!r}")

        # A backtick inside a quoted `-c` body is shell SUBSTITUTION, not two
        # markdown code spans, and the exclusion that stops a sentence matching
        # across two spans cleared it. The question is about the corpus, so it
        # is asked of the corpus: inside a fence there are no code spans.
        ticked = ('python3 -c "\n# writes `result.json`\n'
                  'from agents.lib.tie_out import run\n"')
        assert _unactivated(ticked, fenced=True), (
            "a backtick inside a quoted body cleared a bare engine call")
        # ...and the prose the exclusion exists for, which keeps it. Two code
        # spans in one sentence are a sentence about two things. Read with
        # `fenced=True` this same text WOULD match, which is the cost of the
        # widening and the reason it is not applied to prose.
        assert not _unactivated(
            "the launcher's `python3` and `agents/lib/tie_out.py`"), (
            "a sentence naming two code spans was read as one command")

        # The comment mask is what clears the routed line above, and masking can
        # only ever REMOVE text — so the half that has to be checked is that it
        # does not remove an offence. A `#` opens a comment at a word boundary
        # and nowhere else.
        assert _unactivated("python3 -m agents.lib.ptet#notacomment"), (
            "a `#` inside a word was read as opening a comment")

    @pytest.mark.parametrize("skill", sorted(_engine_calling_skills_cached()))
    def test_an_inline_python_body_is_not_indented(self, skill):
        """`python3 -c "` inside an indented list item is a real IndentationError.

        Only 3.13+ auto-dedents the `-c` body. On 3.12 — the version the lock is
        compiled for and CI runs, and one the new preflight certifies as valid —
        an indented continuation line exits 1 with `IndentationError: unexpected
        indent`, so the whole step silently never runs. tax-fbar's foreign-payer
        cross-check shipped that way: a payer in `parse_ledger.json` and absent
        from the FBAR account table would never have been surfaced.
        """
        offenders = []
        for path, n, unit, _ in _units(skill):
            # INLINE_PY, not a second copy of it. This test carried its own
            # `python3 -c` regex, so when the launcher landed and every inline
            # call became `sh agents/lib/run.sh -c`, `_shell_units` joined the
            # bodies correctly and this test skipped every one of them — the
            # audit went vacuous while the pattern it was supposed to share had
            # already been widened.
            m = INLINE_PY.search(unit)
            if not m:
                continue
            # EVERY body line, not just the first. The `break` used to be
            # unconditional, so a block whose first line was flush and whose
            # second was indented passed the audit and still raised
            # IndentationError below 3.13 — the same tax-fbar failure this test
            # describes, one line further down.
            #
            # There is NO fence bound here, and removing the one that stood was
            # the fix rather than porting it. It read `startswith("```")` — the
            # toggle-on-three-backticks rule `_fence_state` was introduced to
            # retire — and this was the caller left on it: a `-c` body inside a
            # `~~~` block was scanned past its real closer, and a body inside a
            # four-backtick block was cut at an inner ``` that closes nothing,
            # so its indented continuation went unreported. One file, two fence
            # rules, and the answer depended on which character the document
            # used.
            #
            # None is needed, because `_join_units` never joins ACROSS a
            # delimiter — it checks `boundary` before every append — so a unit
            # holds no fence line at all and every ``` inside one is CONTENT.
            # The bound was cutting at content and only at content.
            for line in unit[m.end():].split("\n")[1:]:
                if line and line[0] in " \t":
                    offenders.append(f"{path}:{n}: {line[:40]!r}")
                    break
        assert not offenders, (
            f"inline python bodies are indented and raise IndentationError "
            f"below 3.13: {offenders}")

    @pytest.mark.parametrize("skill", sorted(_engine_calling_skills_cached()))
    def test_no_skill_names_an_interpreter_itself(self, skill):
        """The launcher is the ONLY thing that may name an interpreter.

        The audit before this one asserted the activation PREFIX was present
        and never what followed it, so five sites chained the prefix and then
        invoked a bare `python3` — running the host interpreter on a Windows
        venv while reporting as covered. There is no prefix to be present now,
        and this closes the other half: a skill that spells `python3` beside an
        engine call has gone around `run.sh`.
        """
        offenders = []
        for path, n, unit, _ in _units(skill):
            if not _calls_engine(unit):
                continue
            body = unit.replace(LAUNCHER, "")
            # `python3 -m venv` is the one legitimate exception: it CREATES the
            # virtualenv, so it cannot route through a launcher whose whole job
            # is to activate one that already exists.
            body = re.sub(r"python3? -m venv[^\n;&|]*", "", body)
            if re.search(r"\bpython3?\b", body):
                offenders.append(f"{path}:{n}")
        assert not offenders, (
            f"an interpreter is named outside the launcher: {offenders}. Route "
            f"the command through `{LAUNCHER}` instead.")

    def test_the_audit_sees_an_inline_engine_import(self):
        """The predicate itself, on the shape that slipped past it.

        Three commands ran unprefixed while the line-based audit passed: it
        matched only the path and `-m` forms, and `python3 -c "` puts the
        engine import on the next line. Shrinkage is invisible from the
        assertion above, which iterates whatever the predicate hands it.
        """
        inline = '\n'.join(['python3 -c "', 'import json',
                            'from agents.lib.tie_out import load_ledger"'])
        assert RUNS_PYTHON.search(inline) and _calls_engine(inline)
        assert INLINE_PY.search('python3 -c "')
        # `python`, not just `python3` — the same unchecked interpreter under
        # another name, and INLINE_PY already accepts both spellings.
        for bare in ('python -m agents.lib.projection --year 2025',
                     'python -c "\nfrom agents.lib.tie_out import load_ledger"'):
            assert RUNS_PYTHON.search(bare) and _calls_engine(bare), bare
        # The VERSION-qualified name is the same interpreter, and the two
        # predicates keyed on the character AFTER the `3` could not read it.
        # `INLINE_PY` is the costly one: without the opener there is no join, so
        # the `-c` body arrives as a unit holding an engine import and no
        # interpreter, and the interpreter arrives as a unit holding no engine
        # target. Both halves clean, one bare engine call — which is the exact
        # shape this test was written for, one spelling along. `_calls_engine`
        # and `INTERPRETER` never had the gap, `\b` falling between the `3` and
        # the `.`, and that is what left it to be found rather than to fail.
        versioned = '\n'.join(['python3.12 -c "', 'import json',
                                'from agents.lib.tie_out import load_ledger"'])
        assert INLINE_PY.search('python3.12 -c "')
        assert RUNS_PYTHON.search(versioned) and _calls_engine(versioned)
        assert RUNS_PYTHON.search("python3.14 -m agents.lib.projection")
        # ...and a version is a version of THREE. `python2.7` names an
        # interpreter that cannot run this engine at all, and a suffix loose
        # enough to admit it would put the audit's verdicts on a command that
        # was never going to execute.
        assert not RUNS_PYTHON.search("python2.7 -m agents.lib.projection")
        # ...and the two shapes that must NOT be read as engine invocations.
        assert not _calls_engine("Never interpolate paths into inline python3 -c strings.")
        assert not RUNS_PYTHON.search("these are scripts under `agents/lib/`, a sibling of")

    def test_an_unterminated_quote_cannot_swallow_the_rest_of_the_file(
            self, tmp_path, monkeypatch):
        """The join is bounded at the closing fence.

        Unbounded, an opening quote that never balances makes ONE unit of
        everything after it. That unit contains an activation somewhere, so the
        audit counts one activated invocation and passes over every genuine
        offender below — the same shrinkage failure as always, arriving as a
        join that matches too MUCH rather than too little.
        """
        skill = tmp_path / "demo"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "```bash\n"
            'sh agents/lib/run.sh -c "print(1)\n'          # quote never closed
            "```\n"
            "\n"
            "```bash\n"
            "python3 -m agents.lib.projection --year 2025\n"   # unactivated
            "```\n")
        monkeypatch.setattr("tests.test_preflight.SKILLS_DIR", tmp_path)
        monkeypatch.setattr("tests.test_preflight.REPO", tmp_path)
        offenders = [u for _, _, u, _ in _shell_units("demo")
                     if RUNS_PYTHON.search(u) and _calls_engine(u)
                     and LAUNCHER not in u]
        assert offenders, (
            "the unterminated quote absorbed the unactivated call below it")

    def test_the_audit_joins_a_backslash_continued_command(self, tmp_path, monkeypatch):
        """A `\\`-continued pipeline is ONE command, and was read as two.

        `/tax-amend` secures its as-filed snapshot with `find … -print0 \\` on
        one line and `| xargs -0 sh agents/lib/run.sh -m agents.lib.secure_fs`
        on the next — one shell command, two lines. Split into two units the
        second was reported as an engine call that bypassed the launcher. That is the
        safer direction to be wrong in and still wrong: an audit that cries
        wolf on correct data teaches the reader to reason past it.
        """
        skill = tmp_path / "demo"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "```bash\n"
            "find archive -print0 \\\n"
            "  | xargs -0 sh agents/lib/run.sh -m agents.lib.secure_fs || exit 1\n"
            "```\n")
        monkeypatch.setattr("tests.test_preflight.SKILLS_DIR", tmp_path)
        monkeypatch.setattr("tests.test_preflight.REPO", tmp_path)
        units = [u for _, _, u, _ in _shell_units("demo")]
        engine = [u for u in units if RUNS_PYTHON.search(u) and _calls_engine(u)]
        assert len(engine) == 1, f"the continuation was split: {units}"
        assert LAUNCHER in engine[0], engine[0]

    def test_the_dependency_probe_speaks_for_the_parsing_interpreter(self):
        """/tax-setup step 6 checks pdfplumber et al. are importable.

        Not an engine call, so the audit above does not reach it — but the same
        wrong-interpreter failure: unactivated it certifies the HOST while
        `/tax-parse` runs `parse_utils` inside `.venv`. The `[ -d .venv ]` guard
        keeps the first run, before any venv exists, behaving as it did.
        """
        for _, _, unit, _ in _shell_units("tax-setup"):
            if "import pdfplumber" in unit:
                assert LAUNCHER in unit
                break
        else:
            pytest.fail("the dependency probe moved or was renamed")

    def test_contributor_skills_are_exempt(self):
        """They work ON the toolkit, from a clone that by definition has it."""
        for name in CONTRIBUTOR_SKILLS:
            assert GATE_HEADING not in skill_text(name)


class TestRemediesCannotDestroyTheFix:
    """A remedy that cannot resolve what was reported is worse than none — and
    three of these could actively destroy the thing that would have fixed it."""

    def _report(self, **kw):
        base = dict(python_version="3.9.6", python_ok=False, failures=(),
                    probe_error="", modules_checked=21, venv_error="",
                    venv_broken=False, workspace="")
        base.update(kw)
        return P.PreflightReport(**base)

    def test_an_unactivated_venv_outranks_the_python_version(self, tmp_path):
        """The destructive ordering, and the reason it is destructive.

        On a stock macOS the host `python3` is 3.9, so a workspace holding a
        perfectly good 3.12 `.venv` that simply was not activated reports
        python_ok=False AND every PEP 604 module missing. The version remedy
        LEADS WITH `rm -rf .venv` — the reader deletes the one interpreter that
        would have fixed the failure.
        """
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text("VIRTUAL_ENV=" + str(tmp_path / ".venv"))
        (venv / "python3").write_text("")
        report = self._report(
            failures=(P.ModuleFailure("agents.lib.tie_out", "the tie-out gate",
                                      "SyntaxError: invalid syntax"),),
            venv_error="`.venv` exists but this check is running under no "
                       "virtualenv",
            workspace=str(tmp_path))
        out = P.render_markdown(report)
        how = out.index("## How to proceed")
        # SUPPRESSED, not merely demoted. Ordering alone was a half-fix and this
        # assertion is what makes that visible: `rm -rf .venv` must not appear at
        # all, under a sentence that is false here — the `.venv` IS the supported
        # interpreter, it was simply never activated.
        assert "Install or select Python" not in out[how:], (
            "the version remedy still prints, and it leads with `rm -rf .venv` "
            "under a sentence that is false here — the `.venv` IS the supported "
            "interpreter, it was simply never activated")
        assert "git clone" not in out[how:], (
            "a re-clone cannot be diagnosed from the wrong interpreter")
        assert LAUNCHER in out[how:]
        # The ONE `rm -rf` left is venv_remedy's own escape hatch, and it is
        # conditioned on "if you had ALREADY chained it" — a state the reader can
        # check and this gate cannot. That condition is what separates it from
        # the unconditional deletion above, so it must come after the condition.
        assert out.index("rm -rf .venv", how) > out.index("ALREADY chained it", how)
        assert "Activate the workspace venv and re-run" in out, (
            "the symptom note still points at the version")

    def test_an_old_python_and_an_unstattable_venv_do_not_contradict(
            self, tmp_path):
        """Two remedies on one report, giving opposite instructions — and the
        destructive one printed first.

        python_ok=False is the stock macOS 3.9; venv_uncomparable is a `.venv`
        whose stat failed on an unreadable parent or a mount that blinked.
        `rebuild_follows` is False there — the rebuild remedy is deliberately
        suppressed for an uncomparable venv — so the version remedy rendered its
        full `_rebuild_steps`, opening with `rm -rf .venv`, and `inspect_remedy`
        then appended **Do not delete it on this evidence.** underneath. A
        reader who follows the first destroys a working virtualenv, which is the
        jump `_relocated`, `_activate_path`, `_usable_venv` and
        `_exists_or_raises` each refuse in writing.
        """
        out = P.render_markdown(self._report(
            venv_error="`.venv` could not be compared against this "
                       "interpreter: [Errno 13] Permission denied",
            venv_uncomparable=True, workspace=str(tmp_path)))
        how = out.index("## How to proceed")
        assert "Do not delete it on this evidence" in out[how:]
        assert "rm -rf .venv" not in out[how:], (
            "the reader is told to delete `.venv` in the paragraph above the "
            "one telling them not to delete it on this evidence")
        assert "Install or select Python" in out[how:], (
            "the interpreter is still the root cause and still has to change — "
            "only the half that deletes waits for evidence")

    def test_an_old_python_alone_still_leads_with_the_version(self, tmp_path):
        """The counterweight: with no workspace venv there is nothing to
        activate, and the version really is the root cause."""
        out = P.render_markdown(self._report(
            failures=(P.ModuleFailure("agents.lib.tie_out", "the tie-out gate",
                                      "SyntaxError: invalid syntax"),),
            workspace=str(tmp_path)))
        how = out.index("## How to proceed")
        assert "python3.12 -m venv" in out[how:]
        assert "Fix the version and re-run" in out

    def test_a_windows_layout_venv_is_told_to_source_the_script_it_has(
            self, tmp_path):
        """`Scripts/`, not `bin/`. Under Git Bash the `bin/` spelling fails
        silently, the `;` runs the gate on the host interpreter, and the reader
        is advised to run the command that just no-oped — then the rebuild
        fallback recreates `Scripts/` and lands them back here, forever."""
        scripts = tmp_path / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "activate").write_text("VIRTUAL_ENV=" + str(tmp_path / ".venv"))
        (scripts / "python.exe").write_text("")
        assert P._venv_layout(tmp_path / ".venv") == "Scripts"
        out = P.render_markdown(self._report(
            python_ok=True, python_version="3.12.0",
            venv_error="`.venv` exists but this check is running under no "
                       "virtualenv",
            workspace=str(tmp_path)))
        assert ".venv/Scripts/python" in out
        assert ".venv/bin/python -m pip" not in out
        assert "Windows layout" in out

    def test_a_crlf_activate_is_not_accused_of_having_moved(self, tmp_path):
        """A CRLF `activate` must not read as a venv that has been MOVED.

        Reported as a regex bug; it is not one, and TWO independent things
        stop it: `Path.read_text()`'s universal-newline translation turns
        CRLF into `\\n` before the pattern sees it, and `_activate_target`'s
        `.strip()` would remove a carriage return that reached the capture
        anyway. Mutation-verified: this goes red only when BOTH are removed,
        which is exactly what a redundant-protection guard should do — it
        pins the outcome, not either mechanism, so a future edit may drop
        one but not the pair. Un-normalized on both paths, `/w/.venv\\r`
        survives the quote strip and the `$`/backtick filter, fails samefile
        and exists(), and earns a `rm -rf .venv` aimed at a working
        virtualenv. `EnvBuilder` writes LF on CI, so nothing built from the
        real thing covers this.
        """
        venv = tmp_path / ".venv"
        (venv / "bin").mkdir(parents=True)
        activate = venv / "bin" / "activate"
        activate.write_bytes(f'VIRTUAL_ENV="{venv}"\r\nexport VIRTUAL_ENV\r\n'
                             .encode())
        assert P._activate_target(activate) == str(venv)
        assert P._relocated(venv, activate) == ""

    def test_the_rebuild_procedure_has_one_source(self):
        """It was three near-copies, in a module arguing that a remedy which
        cannot resolve its failure is worse than none."""
        # The install names the interpreter BY PATH. The activate form silently
        # no-ops against a `Scripts/` venv under Git Bash, which is why README
        # and QUICKSTART moved off it — and this is the copy a blocked reader
        # actually follows.
        # CHAINED into one command. Separate lines let a failed `cd` — or a
        # failed venv creation — be followed by the next step anyway, and the
        # step after `cd` is `rm -rf .venv`.
        assert P._rebuild_steps("python3.12", "Scripts") == (
            "    rm -rf .venv &&\n"
            "      python3.12 -m venv .venv &&\n"
            "      .venv/Scripts/python -m pip install -r requirements.txt")

    def test_nothing_destructive_follows_a_cd_that_can_fail(self, tmp_path):
        """The `cd` and the deletion must be ONE command.

        `_cd_step` redacts the path it names, which it does precisely when the
        path is sensitive — so the rendered `cd` is sometimes one that cannot
        succeed. On separate lines, a reader pasting the block then runs
        `rm -rf .venv` in whatever directory they are actually in.
        """
        steps = P._rebuild_steps("python3.12", "bin", workspace=str(tmp_path))
        assert f"cd {tmp_path}" in steps
        head, _, rest = steps.partition("\n")
        assert head.rstrip().endswith("&&"), (
            f"the `cd` does not chain into what follows it: {steps!r}")
        for line in rest.splitlines()[:-1]:
            assert line.rstrip().endswith("&&"), (
                f"a step runs even when the one before it failed: {line!r}")
        # The remedies that BUILD a venv name one layout, so a `Scripts`
        # rendering must not leak `bin/`. `venv_remedy` is excluded on purpose:
        # it quotes the command the skills chain, which tries BOTH spellings —
        # that is the whole point of it, and asserting otherwise would pin the
        # single-branch form the layout work removed.
        for text in (P.python_remedy("Scripts"), P.rebuild_remedy("Scripts"),
                     P.clone_remedy("Scripts")):
            assert "/bin/activate" not in text, text[:80]
        # venv_remedy now quotes the LAUNCHER, which handles both layouts
        # itself, so neither spelling appears in it at all.
        assert LAUNCHER in P.venv_remedy("Scripts")


class TestTheRemedyQuotesTheFormTheSkillsRun:
    def test_the_activation_in_the_remedy_matches_the_skills(self):
        """The remedy told the reader to run a command different from the one
        the skill runs next — for a whole round, after the skills moved to the
        two-branch form and this copy did not. Nothing pinned them together.
        """
        remedy = P.venv_remedy("bin")
        assert LAUNCHER in remedy, remedy
        assert "TAXPY" not in remedy, (
            "the remedy still quotes the inlined prefix the launcher replaced")
        assert "fails silently here" not in P.venv_remedy("Scripts")


class TestTheDocumentedFormsMatchTheSkills:
    def test_the_activation_prefix_is_the_same_everywhere(self):
        """README and agents-compat each spell the prefix out for a reader.

        `LAUNCHER` pins the form inside the skills and `CLONE_URL` pins the
        clone command across the documents — this pair was pinned by nothing, so
        changing the form moved the skills and their tests together while both
        documents kept instructing readers to run a command the toolkit no
        longer uses.
        """
        for rel in ("README.md", "docs/contributing/agents-compat.md"):
            text = (REPO / rel).read_text()
            assert LAUNCHER in text, (
                f"{rel} does not route the engine through {LAUNCHER} — it has "
                f"drifted from the form the skills use")

    def test_the_skill_count_in_the_readme_matches_the_roster(self):
        """"All 20 taxpayer-facing skills" is an absolute nothing compared."""
        import re as _re

        text = (REPO / "README.md").read_text()
        claimed = {int(m) for m in _re.findall(
            r"All (\d+) taxpayer-facing skills", text)}
        assert claimed, "the README no longer states a skill count — drop this"
        assert claimed == {len(_engine_calling_skills_cached())}, (
            f"README says {claimed}, the roster has "
            f"{len(_engine_calling_skills_cached())}")


class TestTheDependencyCheck:
    """A new exit-2 condition for all twenty skills, and it had no tests."""

    def test_the_roster_matches_the_direct_dependencies(self):
        """Pinned against `requirements.in`, the human-edited list.

        A hand-written roster nothing compares is the self-fulfilling shape
        `test_the_roster_lists_every_engine_module` closed for ENGINE_MODULES
        and that was left open one constant below it: add a fifth direct
        dependency and this stays at four, so the verdict prints "Pinned
        dependencies importable." and exits 0 on a workspace missing it.
        """
        import re as _re

        raw = (REPO / "requirements.in").read_text()
        declared = set()
        for line in raw.splitlines():
            line = line.split("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            declared.add(_re.split(r"[<>=!\[ ]", line)[0].strip().lower())
        listed = {e[0].lower() for e in P.RUNTIME_DEPENDENCIES}
        assert listed == declared, (
            f"RUNTIME_DEPENDENCIES and requirements.in have diverged: "
            f"{sorted(listed ^ declared)}")

    def test_the_import_name_is_a_separate_field(self):
        """Distribution names and import names are different identifiers.

        `requirements.in` names DISTRIBUTIONS and the roster is pinned against
        it, while the probe IMPORTS — and pyyaml/yaml, Pillow/PIL,
        python-dateutil/dateutil differ. One field for both would make the next
        such dependency report "not installed" forever on a machine where it is
        correctly installed, with a remedy that changes nothing: a gate crying
        wolf on correct data, which is what this module's `blocking` comment
        argues against.
        """
        import importlib.util

        for dist, import_name, _role, _skills in P.RUNTIME_DEPENDENCIES:
            assert dist and import_name
            assert importlib.util.find_spec(import_name) is not None, (
                f"{dist} declares import name `{import_name}`, which does not "
                f"resolve — the two fields have been crossed")

    def test_every_dependency_names_what_is_lost_with_it(self):
        assert all(role.strip() and skills for _, _, role, skills in
                   P.RUNTIME_DEPENDENCIES), (
            "a dependency names no role or no skill that needs it")
        for _, _, _, skills in P.RUNTIME_DEPENDENCIES:
            assert all(sk.startswith("/tax-") for sk in skills), (
                "the third field must name SKILLS — it used to be derived by\n"
                "splitting the role on `(`, which printed prose as a skill name")

    def test_a_missing_dependency_warns_and_does_not_block(self, tmp_path,
                                                           monkeypatch):
        """WARNS. Blocking on it refused `/tax-calendar`, `/tax-scenario`,
        `/tax-ptet`, `/tax-archive` and `/tax-rollover`, none of which import
        any of the four — a STOP-gate firing on correct data, which is the
        shape the repo's own rule says teaches an agent to reason past a
        refusal. A missing package surfaces loudly at first use as an
        ImportError, never as a wrong number on a return.
        """
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES",
                            (("not_a_real_package_xyz", "not_a_real_package_xyz", "PDF parsing",
                              ("/tax-parse",)),))
        # A workspace with no `.venv`, not the repo: `blocking` is decided by
        # the venv check too, so run under a checkout the runner has not
        # activated and this asserts the venv verdict rather than the
        # dependency one. CI passes only because its checkout has no `.venv`.
        report = P.check(modules=(), workspace=tmp_path)
        assert report.missing_dependencies
        assert not report.blocking, (
            "a package five skills do not use refused all twenty")
        # ...and the verdict still says so, so the OK line does not claim more
        # than it checked.
        out = P.render_markdown(report)
        assert "warning(s)" in out
        assert "not_a_real_package_xyz" in out
        assert "pip install -r requirements.txt" in out

    def test_the_json_path_agrees_with_the_markdown_one(self):
        """Both renderers, on one report. `_report_to_dict` unpacked the
        dependency tuples as PAIRS after they gained a third field, so `--json`
        raised ValueError, `_main` caught it and returned 2 — a healthy engine
        missing one optional package hard-blocked every skill through the JSON
        path while markdown warned and exited 0.
        """
        report = P.PreflightReport(
            "3.12.0", True, (), "", 21,
            missing_dependencies=(("reportlab", "PDF generation",
                                   ("/tax-generate",)),))
        d = P._report_to_dict(report)
        assert d["blocking"] is False
        # Name, role and affected skills: the markdown bullets carry all three,
        # and a consumer given only names has to re-derive RUNTIME_DEPENDENCIES
        # to say which skills a missing package actually stops.
        assert d["missing_dependencies"] == [
            {"package": "reportlab", "role": "PDF generation",
             "skills": ["/tax-generate"]}]
        assert "pip install -r requirements.txt" in d["remedy"], (
            "the JSON consumer got a warning it cannot act on")
        assert "warning(s)" in P.render_markdown(report)

    def test_a_fresh_clone_is_told_to_build_the_venv_first(self, tmp_path,
                                                           monkeypatch):
        """No `.venv` yet: a bare `pip install` targets the system interpreter,
        which AGENTS.md §7 forbids and PEP 668 refuses, so the remedy has to
        create the environment it says to install into.

        It must NOT carry the "tax-setup stops here too" caveat, though — that
        was copied from the blocking remedies and is false here: a missing
        package only warns, so tax-setup reaches its own Step 6 perfectly well.
        A lying caveat is worse than a missing one.
        """
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES",
                            (("not_a_real_package_xyz", "not_a_real_package_xyz", "PDF parsing",
                              ("/tax-parse",)),))
        report = P.check(modules=(), workspace=tmp_path)
        assert not report.blocking
        # `_warning_remedy_text`, which is where a dependency remedy can now
        # occur at all: a blocking report cannot carry packages, so the blocking
        # renderer no longer has a dependency branch to reach.
        remedy = P._warning_remedy_text(report)
        assert "python3 -m venv .venv" in remedy, remedy
        assert "rather than reaching for `tax-setup`" not in remedy, (
            "a warning-path remedy claims tax-setup is blocked, which it is not")

    def test_the_warning_verdict_claims_no_arithmetic_it_never_probed(
            self, tmp_path, monkeypatch):
        """One missing package routed the verdict through a second copy of the
        OK line, and that copy hardcoded the claim.

        The plain-OK branch degrades to "(arithmetic NOT probed …)" when the
        roster did not include the math module. The warning branch — reached by
        any report carrying a missing dependency, which is one absent package —
        said "present and computing correctly" over the identical state, three
        lines from the fix. Nothing covered either branch's wording before.
        """
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES",
                            (("not_a_real_package_xyz", "not_a_real_package_xyz",
                              "PDF parsing", ("/tax-parse",)),))
        report = P.check(modules=(), workspace=tmp_path)
        assert report.missing_dependencies and not report.probe_ran
        out = P.render_markdown(report)
        assert "warning(s)" in out
        assert "computing correctly" not in out, (
            "the verdict asserts the tax math is right on a run that never "
            "executed it — the whole point of the probe")
        assert "arithmetic NOT probed" in out

        # The counterweight: with the math module on the roster the probe DOES
        # run, and the claim is then earned. Without this the assertion above is
        # satisfied by a branch that never makes the claim at all.
        probed = P.check(modules=(P.GATE_MODULES[0],), workspace=tmp_path)
        assert probed.probe_ran
        assert "computing correctly" in P.render_markdown(probed)

    def test_a_broken_venv_gets_the_rebuild_and_no_install(self, tmp_path,
                                                           monkeypatch):
        """A broken venv earns ONE remedy, and it is the rebuild.

        Driven through `check()`, which is the whole point. Two earlier versions
        of this test could not fail. The first asserted an ORDER — `rm -rf`
        before `pip install` — that `_rebuild_steps` hardcodes, so reverting
        `_usable_venv` to the `Path.is_dir()` it replaced left it green. The
        second hand-built a `PreflightReport` carrying both a venv error and
        missing dependencies, which `check()` cannot produce at all: it skips
        the dependency probe whenever the venv check failed, so the guard under
        test was being exercised against an impossible report. Build the
        WORKSPACE and let the module reach its own conclusion.
        """
        venv = tmp_path / ".venv"
        (venv / "bin").mkdir(parents=True)          # no activate script
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES",
                            (("not_a_real_package_xyz", "not_a_real_package_xyz",
                              "PDF parsing", ("/tax-parse",)),))
        report = P.check(modules=(), workspace=tmp_path)

        assert report.venv_broken
        assert not report.missing_dependencies, (
            "the dependency probe ran on an interpreter the same report "
            "disclaims — and the verdict then lists what it found as fact")
        remedy = P._remedy_text(report)
        assert "rm -rf .venv" in remedy
        assert "Install the pinned dependencies" not in remedy, (
            "a second install step, above a rebuild that discards it")
        assert remedy.count("pip install -r requirements.txt") == 1

    @pytest.mark.parametrize("blocking_cause", [
        {"venv_error": "`.venv` is broken"},
        {"python_ok": False},
        {"probe_error": "compute_bracket_tax returned 250.0, expected 300.0"},
    ])
    def test_packages_cannot_ride_a_blocking_report(self, blocking_cause):
        """The invariant every deleted branch rests on, stated once.

        A caveat in `render_markdown`, a guard in `_remedy_text` and a whole
        bullet section were removed because no report can reach them — `check()`
        skips the dependency probe as soon as any blocking cause is known. That
        is only safe while the combination stays unbuildable, so the dataclass
        refuses it, and this pins the refusal rather than the deleted branches.

        Parametrized over the causes because the rule widened: it used to be
        `venv_error` alone, and the probe was still paid for on every report
        that blocked for one of the other reasons.
        """
        fields = dict(python_version="3.12.0", python_ok=True, failures=(),
                      probe_error="", modules_checked=21,
                      missing_dependencies=(("pdfplumber", "PDF parsing",
                                             ("/tax-parse",)),))
        fields.update(blocking_cause)
        with pytest.raises(ValueError, match="BLOCKING report"):
            P.PreflightReport(**fields)

    def test_the_probe_is_not_paid_for_on_a_blocking_report(self, tmp_path,
                                                            monkeypatch):
        """It imports the four heaviest third-party packages on the first step
        of all twenty skills, and every blocking path discards the result."""
        calls = []
        monkeypatch.setattr(P, "_missing_dependencies",
                            lambda: calls.append(1) or ())
        monkeypatch.setattr(P.sys, "version_info", (3, 9, 6))
        report = P.check(modules=(), workspace=tmp_path)
        assert report.blocking and not calls, (
            "the dependency probe ran for a verdict that already had its cause")


class TestTheDiagnosisSurvivesTheRedactorBeingGone:
    def test_authored_failure_lines_are_not_withheld(self, tmp_path,
                                                     monkeypatch):
        """The flagship scenario collapses to twenty-one identical markers.

        When the engine is copied away, `secure_fs` is gone with it, so
        `_redact` withholds — and `_load`'s own "no X.py beside preflight.py"
        constant, whose only variable is a module basename, was withheld along
        with everything else. "The files are absent" then reads exactly like
        "the imports raised", which is the whole diagnostic value of the list.
        """
        monkeypatch.setattr(P, "_HERE", tmp_path)      # no engine files
        # `workspace=tmp_path` for the reason above: a venv verdict stops the
        # module checks before they run, so an ambient unactivated `.venv`
        # leaves this asserting on a report that never looked at a module.
        report = P.check(modules=(("agents.lib.tie_out", "the tie-out gate", "load_ledger"),),
                         workspace=tmp_path)
        import builtins
        real = builtins.__import__

        def no_secure_fs(name, *a, **k):
            if name == "agents.lib.secure_fs":
                raise ModuleNotFoundError(name)
            return real(name, *a, **k)

        builtins.__import__ = no_secure_fs
        try:
            out = P.render_markdown(report)
        finally:
            builtins.__import__ = real
        assert "beside preflight.py" in out, (
            "an authored, path-free line was withheld with the rest")
        assert "withheld" not in out

    def test_a_redactor_that_exits_at_import_still_blocks(self, tmp_path):
        """The FAIL-OPEN. `_redact` caught only Exception, and it runs from
        render_markdown — outside `_main`'s try — so a `secure_fs` calling
        `sys.exit(0)` at import terminated the process with status 0, which
        every skill's gate reads as "engine present, continue"."""
        lib = tmp_path / "agents" / "lib"
        lib.mkdir(parents=True)
        (tmp_path / "agents" / "__init__.py").write_text("")
        (lib / "__init__.py").write_text("")
        shutil.copy(REPO / "agents" / "lib" / "preflight.py", lib / "preflight.py")
        (lib / "secure_fs.py").write_text("import sys\nsys.exit(0)\n")
        r = subprocess.run([sys.executable, "agents/lib/preflight.py"],
                           cwd=tmp_path, capture_output=True, text=True)
        assert r.returncode == 2, (
            f"a broken engine exited {r.returncode}; every skill reads 0 as "
            f"'continue'")


class TestAStatThatFailedIsNeverEvidence:
    """Three functions reached a verdict from a failed stat, in three
    directions — and `Path.exists()`/`is_dir()` swallowing OSError is why."""

    def _unreadable_venv(self, tmp_path):
        venv = tmp_path / ".venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "activate").write_text(f'VIRTUAL_ENV="{venv}"\n')
        (venv / "bin" / "python3").write_text("")
        os.chmod(venv, 0o000)
        return venv

    def test_an_unstattable_venv_does_not_read_as_no_venv(self, tmp_path):
        """The FAIL-OPEN, and the worst kind in this module.

        `_venv_error` gated on `Path.is_dir()`, which answers False for EACCES
        as readily as for absence — so an unstattable `.venv` read as "there is
        no venv here", the interpreter comparison never happened, and the gate
        exited 0 certifying a host interpreter it never looked at.

        The PARENT is what has to be unreadable: stat-ing a directory needs
        execute permission on its parent, not read permission on itself, so a
        mode-000 `.venv` is still perfectly stattable — which is why the first
        version of this test passed against the bug it was written for.
        """
        parent = tmp_path / "workspace"
        (parent / ".venv" / "bin").mkdir(parents=True)
        os.chmod(parent, 0o000)
        try:
            report = P.check(modules=(), workspace=parent)
            assert report.blocking, (
                "an unstattable venv was read as no venv and the gate passed")
            assert report.venv_uncomparable
        finally:
            os.chmod(parent, 0o755)

    def test_an_unreadable_venv_is_not_told_to_delete_itself(self, tmp_path):
        """And the DESTRUCTIVE direction, from the same conflation one function
        over: `_venv_defect` probed with `.exists()`, so a permission problem
        was reported as "has no activate script" — whose remedy is `rm -rf`."""
        venv = self._unreadable_venv(tmp_path)
        try:
            report = P.check(modules=(), workspace=tmp_path)
            remedy = P._remedy_text(report)
            assert not report.venv_broken
            assert "rm -rf" not in remedy, (
                "an intact virtualenv is deleted over a permission error")
            assert "Do not delete it on this evidence" in remedy
        finally:
            os.chmod(venv, 0o755)

    def test_a_symlinked_venv_with_an_unstattable_target_blocks(self, tmp_path):
        """The fail-open again, one function over from where it was closed.

        `.venv` may legitimately be a symlink; lstat sees the link, so
        `_is_dir_through_link` follows it — and swallowing ITS OSError made an
        unreadable target read as "no venv here". The comparison never ran and
        the gate certified an interpreter it had not looked at.
        """
        target_parent = tmp_path / "store"
        (target_parent / "realvenv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv").symlink_to(target_parent / "realvenv")
        os.chmod(target_parent, 0o000)
        try:
            report = P.check(modules=(), workspace=tmp_path)
            assert report.blocking, (
                "a venv symlink with an unstattable target read as no venv")
            assert report.venv_uncomparable
        finally:
            os.chmod(target_parent, 0o755)

    def test_a_venv_that_really_has_no_activate_still_says_so(self, tmp_path):
        """The counterweight — the accusation must survive for the real case."""
        (tmp_path / ".venv").mkdir()
        report = P.check(modules=(), workspace=tmp_path)
        assert report.venv_broken
        assert "no activate script" in report.venv_error


class TestTheRelocationVerdictNeedsRealEvidence:
    def test_an_unreadable_baked_in_path_is_not_an_accusation(
            self, tmp_path, monkeypatch):
        """`Path.exists()` swallows EACCES and answers False, which reads as
        "demonstrably does not exist" — and that verdict's remedy is `rm -rf
        .venv` on an intact virtualenv whose parent merely became unreadable."""
        venv = tmp_path / ".venv"
        (venv / "bin").mkdir(parents=True)
        activate = venv / "bin" / "activate"
        activate.write_text(f'VIRTUAL_ENV="{tmp_path}/elsewhere/.venv"\n')

        def denied(path, *a, **k):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(P.os.path, "samefile", denied)
        monkeypatch.setattr(P.os, "lstat", denied)
        assert P._relocated(venv, activate) == "", (
            "an unstattable path was reported as a venv that had moved")

    def test_a_genuinely_absent_path_still_is_one(self, tmp_path):
        """The counterweight: the accusation must survive for the real case."""
        venv = tmp_path / ".venv"
        (venv / "bin").mkdir(parents=True)
        activate = venv / "bin" / "activate"
        activate.write_text(f'VIRTUAL_ENV="{tmp_path}/gone/.venv"\n')
        assert "moved" in P._relocated(venv, activate)


class TestTheDiagnosisSurvivesAnOldInterpreter:
    """The module's flagship scenario is an interpreter older than the project
    minimum, and the round of fixes before this one broke reporting in exactly
    that case."""

    def test_secure_fs_imports_below_the_project_minimum(self):
        """`secure_fs` is the redactor preflight reaches for before it can say
        anything, so it alone must import on an interpreter the rest of the
        engine legitimately rejects.

        Without it `_redact` falls back to withholding and the verdict replaces
        every module error — including the one line naming the real cause — with
        a marker, under a banner claiming the engine is missing when only the
        interpreter is old. Reproduced on a stock macOS `/usr/bin/python3`.
        """
        import ast

        # The PROPERTY, not the spelling. Grepping for the future import pins
        # one mechanism and misses every other way 3.9 can reject these files: a
        # `match` statement, a runtime `isinstance(x, int | str)`, PEP 604 in a
        # default value. `feature_version` makes the parser itself the judge, and
        # it covers the grammar half in CI, where no 3.9 interpreter exists.
        #
        # preflight.py is here for a reason the reported finding was right about:
        # it had no guard at all, and a PEP 604 annotation added to IT stops the
        # file PARSING on 3.9, so the gate prints nothing whatsoever — strictly
        # worse than the wrong verdict this pair exists to prevent.
        for rel in ("secure_fs.py", "preflight.py"):
            src = (REPO / "agents" / "lib" / rel).read_text()
            try:
                ast.parse(src, feature_version=(3, 9))
            except SyntaxError as e:
                pytest.fail(
                    f"agents/lib/{rel} no longer parses on 3.9 ({e}). It must: "
                    f"preflight runs on whatever interpreter the agent reached, "
                    f"and an old one is the failure it most often reports.")
            # Parsing is not enough. `X | None` in an annotation is valid 3.9
            # GRAMMAR and raises at def time, so the parse above accepts it —
            # the future import is what defers the evaluation, and both files
            # need it. What neither check covers is PEP 604 in a runtime
            # position (`isinstance(x, int | str)`); only the real-binary test
            # below sees that, and only where a 3.9 interpreter exists.
            assert "from __future__ import annotations" in src, (
                f"agents/lib/{rel} lost its future import — its annotations are "
                f"evaluated at def time without it, and this module has to "
                f"import on an interpreter the engine legitimately rejects")

    @pytest.mark.skipif(not pathlib.Path("/usr/bin/python3").exists(),
                        reason="no system interpreter to test the old path with")
    def test_the_real_cause_reaches_the_reader_on_an_old_interpreter(self):
        """Not a proxy for the above — the actual binary, actually run.

        Skipped rather than failed where no second interpreter exists: CI runs
        3.12 and 3.14 only, and a machine without /usr/bin/python3 cannot show
        this either way.
        """
        # Parsed as INTEGERS. Comparing the printed tuple as a string reads
        # "(3, 9)" >= "(3, 10)" as True — '9' sorts after '1' — which skipped
        # this test on the very interpreter it exists for.
        raw = subprocess.run(
            ["/usr/bin/python3", "-c",
             "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True).stdout.strip()
        version = tuple(int(part) for part in raw.split("."))
        if version >= P.MIN_PYTHON:
            pytest.skip(f"system interpreter is {raw} — nothing to diagnose")
        out = subprocess.run(["/usr/bin/python3", "agents/lib/preflight.py"],
                             cwd=REPO, capture_output=True, text=True).stdout
        assert "withheld" not in out, (
            "every diagnostic was suppressed on the one interpreter this "
            "module exists to report about")
        assert "older than the required" in out


class TestARefusalAlwaysCarriesItsWayOut:
    """`render_markdown`'s docstring says a refusal always states how to
    proceed. These pin that it is true on every channel and in every state."""

    def test_json_carries_the_remedy_not_only_the_diagnosis(self):
        report = P.check(modules=(("agents.lib.not_a_real_module", "the gate", "x"),))
        d = P._report_to_dict(report)
        assert d["blocking"]
        assert d["remedy"], (
            "a --json consumer got `blocking: true` and no way to act on it")
        assert "git clone" in d["remedy"]
        assert "workspace" in d

    def test_a_passing_report_carries_no_remedy(self):
        """Nothing to proceed from, so nothing to say — and a WARNING is
        something to proceed from.

        The two environments this suite runs in disagree about which case this
        is, and the assertion has to hold in both or it is measuring the runner.
        Locally the workspace has its virtualenv and the report is a clean pass.
        CI installs the pinned tree onto the system interpreter and never
        creates `.venv`, so the same call returns a report carrying the
        no-virtualenv warning — non-blocking, and the one state whose remedy
        was exported on the markdown channel and not this one.

        Version drift is listed for the same reason and NOT stubbed out: this
        test reads the real environment on purpose, and a contributor whose
        venv has slipped off the lock is a warning state, not a clean pass.
        Stubbing it here would be asserting about a tree nobody is running.
        """
        report = P.check(workspace=REPO)
        remedy = P._report_to_dict(report)["remedy"]
        if (report.no_workspace_venv or report.missing_dependencies
                or report.drifted_dependencies or report.dependency_lock_error):
            assert remedy, "a warning a --json consumer cannot act on"
        else:
            assert remedy == ""

    def test_an_uncomparable_venv_is_not_answered_with_activate_it(
            self, tmp_path, monkeypatch):
        """A failed stat is its OWN condition, and both other answers are wrong.

        Empty made it "merely unactivated" and answered with the activation the
        reader had already chained — a loop. Calling it a DEFECT was the
        overcorrection: that answer is `rm -rf .venv`, on a virtualenv whose
        only problem may be an unreadable parent, a mount that blinked, or a
        lock. `_relocated` refuses the same jump twenty lines below.
        """
        (tmp_path / ".venv").mkdir()

        def boom(a, b):
            raise PermissionError(13, "Permission denied")

        # The comparison is only REACHED from inside a virtualenv — outside one
        # the answer is already known and `samefile` is short-circuited away. So
        # pin that state instead of inheriting it from whoever ran pytest: this
        # test passed under an activated `.venv` and failed on CI, which runs on
        # the system interpreter, for four rounds. A test that measures the
        # runner's shell is not measuring the code.
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))
        monkeypatch.setattr(P.os.path, "samefile", boom)
        report = P.check(modules=(), workspace=tmp_path)
        assert report.blocking
        assert report.venv_uncomparable
        assert not report.venv_broken, (
            "a stat that merely failed was treated as evidence of a defect")
        remedy = P._remedy_text(report)
        assert "rm -rf" not in remedy, (
            "a good virtualenv is deleted on the strength of a failed stat")
        assert "source .venv" not in remedy, (
            "answered with the activation the reader has already chained")
        assert "Do not delete it on this evidence" in remedy

    def test_a_case_different_spelling_is_the_same_venv(self, tmp_path,
                                                        monkeypatch):
        """`resolve()` follows symlinks but does not canonicalise CASE, so on a
        case-insensitive filesystem `==` sees two spellings of one directory and
        refuses a correctly activated run. `samefile` compares st_dev/st_ino and
        answers for case, bind mounts and hardlinked prefixes alike — the rule
        `_relocated` one function lower already documents.

        Skipped where the filesystem cannot show the difference: on a
        case-SENSITIVE mount the two spellings really are different directories
        and both operators correctly say so. macOS and Windows, where this bites,
        are case-insensitive by default; Linux CI is not, hence the probe.
        """
        probe = tmp_path / "CaseProbe"
        probe.mkdir()
        if not (tmp_path / "caseprobe").exists():
            pytest.skip("case-sensitive filesystem — the defect cannot occur here")

        venv = tmp_path / ".venv"
        (venv / "bin").mkdir(parents=True)
        monkeypatch.setattr(P.sys, "prefix", str(venv))
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))
        # The workspace reached by a differently-cased path — exactly what an
        # agent gets from a shell that spelled the home directory differently.
        shouty = pathlib.Path(str(tmp_path).upper())
        error, _defect = P._venv_error(shouty)
        assert error == "", (
            f"a correctly activated venv was refused over a spelling: {error}")

    def test_the_clone_url_matches_the_documents(self):
        """The module's copy is the SOLE escape route for a reader whose skill
        was copied away from its engine, so a rename that updates the documents
        and misses this one leaves a remedy that 404s."""
        # Spelled as `REPO / "literal"` one per line, not built from a loop
        # variable: test_claude_hooks' watched-set coverage test locates each
        # module's inputs by matching exactly that form, so a loop made these
        # three invisible to it and their WATCHED_ROOTS entries unprotected —
        # `docs/QUICKSTART.md` could be dropped from the watch list and the
        # coverage test would still pass.
        for text in ((REPO / "README.md").read_text(),
                     (REPO / "docs" / "QUICKSTART.md").read_text(),
                     (REPO / "CONTRIBUTING.md").read_text()):
            assert P.CLONE_URL in text, (
                f"a document does not carry {P.CLONE_URL} — one of the copies "
                f"has drifted, and the module's is a stranded reader's only "
                f"escape route")


class TestTheWorkspaceIsReachable:
    def test_the_cli_accepts_a_workspace(self, tmp_path):
        """Every sibling gate takes `--workspace`; without it the interpreter
        check silently resolves `.venv` against the CWD, so a sub-task started
        in `summaries/` gets certified on the host interpreter."""
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text("VIRTUAL_ENV=" + str(tmp_path / ".venv"))
        (venv / "python3").write_text("")
        result = subprocess.run(
            [sys.executable, "agents/lib/preflight.py", "--workspace",
             str(tmp_path), "--json"],
            cwd=REPO, capture_output=True, text=True)
        import json as _json
        report = _json.loads(result.stdout)
        assert report["blocking"], "the foreign workspace venv was not noticed"
        # The venv path itself, not the substring `.venv` — every message
        # _venv_error produces interpolates the path, so `".venv" in venv_error`
        # is satisfied by a verdict resolved against the CWD and the assertion
        # could not fail if --workspace were ignored outright. The tmp_path root
        # is redacted only where it looks like PII, and pytest's tmp_path
        # carries no nine-digit run, so its tail survives verbatim.
        assert tmp_path.name in report["venv_error"], report["venv_error"]
        assert tmp_path.name in report["workspace"], report["workspace"]

    def test_the_defect_is_derived_once(self, tmp_path, monkeypatch):
        """`check()` used to call `_venv_defect` a second time to set
        `venv_broken`, which repeated two stats and a full read of `activate` —
        and could disagree with `_venv_error`, whose OSError branch never
        consults it at all."""
        calls = []
        real = P._venv_defect
        monkeypatch.setattr(P, "_venv_defect",
                            lambda v: calls.append(v) or real(v))
        (tmp_path / ".venv").mkdir()
        P.check(modules=(), workspace=tmp_path)
        assert len(calls) == 1, f"_venv_defect called {len(calls)} times"


class TestTheLauncherActuallyRuns:
    """`run.sh` was pinned only as a STRING — 106 call sites assert the skills
    name it, and nothing executed it. Every claim in its header was therefore a
    comment, including the one that had already gone stale."""

    def _run(self, *args, cwd=None, env=None, launcher=None):
        return subprocess.run(
            ["sh", str(launcher or REPO / "agents" / "lib" / "run.sh"), *args],
            cwd=str(cwd or REPO), capture_output=True, text=True, timeout=30,
            env=dict(os.environ, **(env or {})))

    def _workspace(self, root):
        """A workspace holding its own copy of the launcher.

        The launcher anchors `.venv` to its OWN location, two levels up, so a
        test that builds a virtualenv in `tmp_path` and then invokes the
        repository's `run.sh` is measuring the repository's `.venv`. Copy it in
        and the fixture is what a real workspace is: engine, launcher and venv
        in one tree.
        """
        lib = root / "agents" / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        launcher = lib / "run.sh"
        launcher.write_text((REPO / "agents" / "lib" / "run.sh").read_text())
        return launcher

    def test_no_arguments_states_its_usage_instead_of_hanging(self, tmp_path):
        """`exec python3` with nothing to run is an interactive REPL.

        In an agent's non-interactive tool call that blocks on a stdin which
        never closes, so a dropped path variable or a typo turns the launcher
        every skill goes through into a hang reported as a tool timeout —
        indistinguishable from a slow engine. AGENTS.md §5: an entry point run
        bare either self-tests or states its usage.

        `stdin=DEVNULL` is deliberately NOT used: it would let a REPL exit
        cleanly on EOF and the test would pass against the bug.
        """
        r = self._run(cwd=tmp_path)
        assert r.returncode == 2, (
            f"exited {r.returncode} with no arguments — a REPL exits 0 on EOF, "
            f"and a hang never gets here at all")
        assert "usage:" in r.stderr, r.stderr
        assert r.stdout == "", "usage went to stdout, where a caller parsing "\
                              "engine output would read it as data"

    def test_an_exported_taxpy_does_not_choose_the_interpreter(self, tmp_path):
        """The header's reset, executed rather than asserted in prose.

        An exported `TAXPY` would select the interpreter for every engine call
        in the toolkit — including preflight's own `sys.prefix` check, which
        would then certify exactly what was substituted.
        """
        r = self._run("-c", "import sys; print(sys.executable)",
                      cwd=tmp_path, env={"TAXPY": "/bin/echo"})
        assert r.returncode == 0, r.stderr
        assert "-c" not in r.stdout, (
            f"the caller's TAXPY ran instead of an interpreter: {r.stdout!r}")
        assert "python" in r.stdout.lower(), r.stdout

    def _stub_venv(self, root, layout, *names):
        """A venv whose interpreters announce themselves, and an activate that
        only exports PATH — the launcher's contract is that it sources the
        activate script and then runs an interpreter through what that left."""
        bindir = root / ".venv" / layout
        bindir.mkdir(parents=True)
        for name in names:
            stub = bindir / name
            stub.write_text(f"#!/bin/sh\necho RAN-{layout}-{name}\n")
            stub.chmod(0o755)
        (bindir / "activate").write_text(f'PATH="{bindir}:$PATH"\nexport PATH\n')
        return bindir

    def test_it_execs_the_workspace_venv_interpreter(self, tmp_path):
        """The whole purpose: the interpreter comes from `.venv`, not the host."""
        self._stub_venv(tmp_path, "bin", "python3")
        r = self._run("anything.py", cwd=tmp_path,
                      launcher=self._workspace(tmp_path))
        assert r.returncode == 0, (
            f"the launcher did not pass the interpreter's status back: "
            f"{r.returncode} {r.stderr!r}")
        assert "RAN-bin-python3" in r.stdout, (
            f"the host interpreter ran instead of the workspace one: "
            f"{r.stdout!r} {r.stderr!r}")

    def test_a_windows_layout_venv_reaches_python_not_python3(self, tmp_path):
        """The `Scripts/` half, which was the entire reason for `TAXPY`.

        A stdlib venv on Windows puts `python.exe` in `Scripts/` and NEVER
        `python3.exe` — CPython's `['python', 'python3', 'python3.X']` link loop
        is the POSIX branch only — so activating a Scripts venv and then running
        `python3` finds the HOST interpreter anyway, which is the silent
        wrong-interpreter run this launcher exists to make unrepresentable.

        Testable on POSIX because the layout is a directory NAME: create both
        interpreters, and the one that must not run is the one that proves it.
        Without `TAXPY=python` this test is the only thing that goes red — the
        three tests around it stay green while a Windows contributor's engine
        calls silently leave the virtualenv.
        """
        bindir = self._stub_venv(tmp_path, "Scripts", "python", "python3")
        # The host `python3` must not win either: PATH order decides, and a
        # `python3` sitting in Scripts/ would let a broken launcher look right.
        (bindir / "python3").write_text("#!/bin/sh\necho WRONG-python3\n")
        r = self._run("anything.py", cwd=tmp_path,
                      launcher=self._workspace(tmp_path))
        assert r.returncode == 0, r.stderr
        assert "RAN-Scripts-python" in r.stdout, (
            f"a Scripts/ venv did not reach `python`: {r.stdout!r} {r.stderr!r}")
        assert "WRONG-python3" not in r.stdout, (
            "`python3` ran inside a Scripts/ venv — on a real Windows host that "
            "name does not exist there and the HOST interpreter would answer")

    def test_an_incomplete_venv_is_refused_with_the_rebuild_recipe(self,
                                                                    tmp_path):
        """The third state, executed. `python3 -m venv .venv` on a system that
        packages the stdlib venv module separately fails inside `_setup_pip`,
        before `setup_scripts`, and leaves a `.venv/` with no activate script.
        The launcher used to fall through it to the HOST interpreter, and the
        guard's refusal then prescribed the launcher — the loop the branch
        exists to end. It shipped with no test executing it; the docs said one
        did.
        """
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        r = self._run("-c", "print('HOST')", cwd=tmp_path,
                      launcher=self._workspace(tmp_path))
        assert r.returncode == 2, (r.returncode, r.stderr)
        assert "HOST" not in r.stdout, "the host interpreter ran"
        assert "rm -rf .venv" in r.stderr, r.stderr
        assert "ensurepip" in r.stderr, "the rebuild recipe lost its hint"
        assert str(tmp_path) not in r.stderr, "the workspace path leaked"

    def test_a_dangling_venv_symlink_is_the_incomplete_state(self, tmp_path):
        """`-e` does not see a dangling link; the header says `-L` is tested
        too. A `.venv` pointing at an unmounted volume is present-but-unusable,
        not absent, and absent would exec the host interpreter."""
        (tmp_path / ".venv").symlink_to(tmp_path / "nowhere")
        r = self._run("-c", "print('HOST')", cwd=tmp_path,
                      launcher=self._workspace(tmp_path))
        assert r.returncode == 2, (r.returncode, r.stderr)
        assert "HOST" not in r.stdout, "a dangling .venv link ran the host"
        assert "rm -rf .venv" in r.stderr, r.stderr

    @pytest.mark.parametrize("denied, mode", [
        (".venv", 0o000),                 # the root itself cannot be searched
        (".venv/bin", 0o000),             # the directory holding the script
        (".venv/bin/activate", 0o000),    # the script exists and is unreadable
    ])
    def test_an_inaccessible_venv_is_not_told_to_delete_itself(
            self, tmp_path, denied, mode):
        """`-f` answers false for an activate script under a directory this
        process may not search, exactly as for one that is not there, so an
        intact virtualenv behind a permission problem was refused with `rm -rf
        .venv`. That is the destructive half of the conflation
        `_activate_path` already refuses one layer up — and preflight is
        reached through this launcher, so its "do not delete it on this
        evidence" verdict could never be shown for the state it was written
        for. Reported against PR #82.

        The unreadable-script case is the third row for a second reason: `-f`
        is TRUE there, `.` then aborts the shell with its own diagnostic, which
        names the file by its full workspace path and exits 1 — a path this
        script is written never to print, and a status no skill branches on.
        """
        self._stub_venv(tmp_path, "bin", "python3")
        target = tmp_path / denied
        os.chmod(target, mode)
        try:
            r = self._run("-c", "print('HOST')", cwd=tmp_path,
                          launcher=self._workspace(tmp_path))
        finally:
            os.chmod(target, 0o644 if target.is_file() else 0o755)
        assert r.returncode == 2, (r.returncode, r.stderr)
        assert "HOST" not in r.stdout, "the host interpreter ran"
        assert "RAN-bin-python3" not in r.stdout, (
            "the venv activated through a permission it does not have")
        assert "rm -rf" not in r.stderr, (
            f"an intact virtualenv is deleted over a permission error: "
            f"{r.stderr!r}")
        assert "Do not delete it on this evidence" in r.stderr, r.stderr
        assert "ls -ld .venv" in r.stderr, "no way offered to find out which"
        assert str(tmp_path) not in r.stderr, (
            f"the workspace path leaked through a shell diagnostic: "
            f"{r.stderr!r}")


class TestTheRemedyCannotBeTheCommandThatFailed:
    """`--workspace <root>` from a different directory is the one shape where
    the activation advice could hand back the exact command that just refused."""

    def test_a_remote_workspace_remedy_says_cd_first(self, tmp_path,
                                                     monkeypatch):
        """`run.sh` resolves `.venv` against the CWD; this module resolves it
        against `--workspace`. From `summaries/` the launcher finds no
        virtualenv, execs the host interpreter, and the verdict reports the
        workspace `.venv` as merely unactivated — whose remedy is the launcher.
        Re-running it verbatim is refused in the same words, and the only escape
        the remedy then offers is `rm -rf .venv` on an intact virtualenv.
        """
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text("VIRTUAL_ENV=" + str(tmp_path / ".venv"))
        (venv / "python3").write_text("")
        elsewhere = tmp_path / "summaries"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))

        report = P.check(modules=(), workspace=tmp_path)
        assert report.unactivated, report.venv_error
        remedy = P._remedy_text(report)
        assert f"cd {tmp_path} && sh agents/lib/run.sh" in remedy, (
            "the remedy for a run in the wrong directory is the same command "
            f"that produced it — a loop with `rm -rf` as its only exit: "
            f"{remedy[:200]!r}")

    def test_the_cd_is_omitted_when_it_would_be_a_no_op(self, tmp_path,
                                                        monkeypatch):
        """The counterweight. Every skill invokes the gate from the workspace
        root, which is the overwhelmingly common path, and a `cd` to where the
        reader already stands is noise that invites them to doubt the rest."""
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text("VIRTUAL_ENV=" + str(tmp_path / ".venv"))
        (venv / "python3").write_text("")   # or the venv reads as BROKEN, and
        monkeypatch.chdir(tmp_path)         # the rebuild remedy answers instead
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))

        remedy = P._remedy_text(P.check(modules=(), workspace=tmp_path))
        assert "sh agents/lib/run.sh" in remedy
        assert "cd " not in remedy, (
            f"a redundant `cd` into the directory the reader is in: {remedy!r}")


class TestTheLauncherIsAnchoredToItsOwnTree:
    """`.venv` was probed against the CALLER's directory, so an engine call from
    one level down silently ran the host interpreter."""

    def _workspace(self, root, layout="bin"):
        lib = root / "agents" / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        launcher = lib / "run.sh"
        launcher.write_text((REPO / "agents" / "lib" / "run.sh").read_text())
        bindir = root / ".venv" / layout
        bindir.mkdir(parents=True)
        for name in ("python", "python3"):
            stub = bindir / name
            stub.write_text("#!/bin/sh\necho WORKSPACE-PYTHON\n")
            stub.chmod(0o755)
        (bindir / "activate").write_text(f'PATH="{bindir}:$PATH"\nexport PATH\n')
        return launcher

    def test_a_call_from_a_subdirectory_still_reaches_the_workspace_venv(
            self, tmp_path):
        """`summaries/` is not a hypothetical: tax-parse dispatches sub-tasks
        that work there, and every skill's engine calls go through this script.

        Probed relatively, `[ -f .venv/bin/activate ]` is false one level down,
        so the launcher exec'd the host `python3` — on a stock macOS a 3.9 the
        toolkit does not support. `preflight.check()` resolves `.venv` against
        its own CWD too, so it found no workspace venv either, returned clean,
        and certified the interpreter it had never compared against. Both halves
        silent, in the one path all twenty skills share.
        """
        launcher = self._workspace(tmp_path)
        sub = tmp_path / "summaries"
        sub.mkdir()
        r = subprocess.run(["sh", str(launcher), "-c", "pass"], cwd=str(sub),
                           capture_output=True, text=True, timeout=30)
        assert "WORKSPACE-PYTHON" in r.stdout, (
            f"a call from `summaries/` left the workspace virtualenv and ran "
            f"the host interpreter: {r.stdout!r} {r.stderr!r}")

    def test_the_anchor_does_not_rewrite_the_caller_s_own_arguments(self,
                                                                   tmp_path):
        """Anchoring the venv probe, NOT the working directory.

        A `cd` into the root would silently change what every relative argument
        means — a document path handed to `secure_fs`, a `--workspace .` — so a
        call from the wrong directory must still fail on the file it cannot
        open. Loudly wrong beats quietly wrong: this is the case the relative
        probe used to answer by running the host Python instead.
        """
        launcher = self._workspace(tmp_path)
        sub = tmp_path / "summaries"
        sub.mkdir()
        (tmp_path / "target.py").write_text("print('root file')\n")
        # The stub interpreter echoes rather than running Python, so assert on
        # the ARGUMENT reaching it unchanged rather than on the exit status.
        r = subprocess.run(["sh", str(launcher), "target.py"], cwd=str(sub),
                           capture_output=True, text=True, timeout=30)
        assert "WORKSPACE-PYTHON" in r.stdout
        assert not (sub / "target.py").exists(), (
            "the fixture no longer distinguishes the two directories")


class TestEveryRemedyRunsWhereTheVenvIs:
    """`--workspace <root>` from another directory: one remedy learned to say
    `cd` and the other three kept naming `.venv` relatively."""

    def _remote(self, tmp_path, monkeypatch, **venv):
        """A workspace at `tmp_path`, with the reader standing in `summaries/`."""
        sub = tmp_path / "summaries"
        sub.mkdir()
        monkeypatch.chdir(sub)
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))
        return P.PreflightReport(
            python_version="3.12.0", python_ok=True, failures=(),
            probe_error="", modules_checked=21, workspace=str(tmp_path),
            **venv)

    def test_the_rebuild_deletes_the_right_venv(self, tmp_path, monkeypatch):
        """`rm -rf .venv` in the wrong directory is the worst of the four: it
        creates a stray virtualenv beside the reader, leaves the broken one
        untouched, and the next run refuses in identical words."""
        report = self._remote(tmp_path, monkeypatch,
                              venv_error="`.venv` has no activate script",
                              venv_broken=True)
        remedy = P._remedy_text(report)
        assert f"cd {tmp_path}" in remedy, (
            f"`rm -rf .venv` aimed at whatever directory the reader is in: "
            f"{remedy[:200]!r}")
        assert remedy.index(f"cd {tmp_path}") < remedy.index("rm -rf .venv"), (
            "the deletion comes before the `cd` that decides where it lands")

    def test_the_inspection_looks_at_the_right_venv(self, tmp_path, monkeypatch):
        """Worse than useless without it: `ls -ld .venv` in the wrong directory
        answers "No such file" about a virtualenv that was never the subject,
        under a heading saying not to delete it on this evidence."""
        report = self._remote(tmp_path, monkeypatch,
                              venv_error="`.venv` could not be compared",
                              venv_uncomparable=True)
        remedy = P._remedy_text(report)
        assert f"cd {tmp_path}" in remedy
        assert remedy.index(f"cd {tmp_path}") < remedy.index("ls -ld"), (
            "the inspection runs before the `cd` that decides what it inspects")

    def test_the_dependency_install_targets_the_right_venv(self, tmp_path,
                                                           monkeypatch):
        """A `pip install` one directory over installs into the host
        interpreter, or into a virtualenv nothing else will ever use."""
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        self._remote(tmp_path, monkeypatch)   # stand in `summaries/`
        report = P.PreflightReport(
            python_version="3.12.0", python_ok=True, failures=(),
            probe_error="", modules_checked=21, workspace=str(tmp_path),
            missing_dependencies=(("pdfplumber", "PDF parsing",
                                   ("/tax-parse",)),))
        remedy = P._warning_remedy_text(report)
        assert f"cd {tmp_path}" in remedy, remedy

    def test_a_venv_that_is_a_file_is_a_defect_not_an_absence(self, tmp_path,
                                                              monkeypatch):
        """The fail-open the lstat branch closed, reached by the other door.

        A `.venv` left as a regular FILE — a flattened archive, a stray `touch`,
        a `cp` without `-r` — is not "no workspace venv here". Answering clean
        skipped the interpreter comparison entirely, so the gate certified
        whatever host Python it was on while `run.sh`, finding no activate
        script beside that file, exec'd the same one.
        """
        (tmp_path / ".venv").write_text("not a directory\n")
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))
        report = P.check(modules=(), workspace=tmp_path)
        assert report.blocking, (
            "a `.venv` that is a plain file read as no venv at all, and the "
            "gate then certified an interpreter it never compared against")
        assert report.venv_broken and not report.venv_uncomparable
        # `rm -rf` FIRST is what makes this a defect rather than the
        # uncomparable state: `python3 -m venv .venv` fails on its first line
        # while the path is still occupied.
        remedy = P._remedy_text(report)
        assert remedy.index("rm -rf .venv") < remedy.index("-m venv .venv")

    def test_a_clone_and_a_rebuild_do_not_both_build_the_venv(self, tmp_path):
        """Two remedies, one report, the same three lines twice.

        `files_missing` with a defective `.venv`: the clone remedy already
        builds and installs inside the fresh checkout, and the rebuild remedy
        then printed `python3 -m venv .venv` and the same pip line again with an
        `rm -rf` between them. A reader cannot tell whether that is one
        procedure or two — the identical duplication `python_remedy` is
        suppressed for on the version path.
        """
        report = P.PreflightReport(
            python_version="3.12.0", python_ok=True,
            failures=(P.ModuleFailure("agents.lib.tie_out", "the tie-out gate",
                                      "no tie_out.py beside preflight.py",
                                      needs_redaction=False, file_missing=True),),
            probe_error="", modules_checked=21,
            venv_error="`.venv` has no activate script", venv_broken=True,
            workspace=str(tmp_path))
        remedy = P._remedy_text(report)
        assert remedy.count("-m venv .venv") == 1, (
            f"the virtualenv is built twice under two different paragraphs:\n"
            f"{remedy}")
        assert "git clone" in remedy, (
            "the remedy that restores the missing engine is the one to keep")


class TestTheRemedyNeverLeaksTheWorkspacePath:
    """`workspace` is redacted in the JSON field and was interpolated raw into
    the remedy text three fields later, on both channels."""

    def test_a_sensitive_workspace_is_redacted_in_the_remedy(self, tmp_path,
                                                             monkeypatch):
        """AGENTS.md §1: never log, echo or display a sensitive value.

        The structural detector in test_secure_fs walks `raise` and `print`
        nodes, so a value that leaves by `return` — into a string another
        function prints — is invisible to it. This is that shape.
        """
        # The allowlisted synthetic value, never a real-looking one.
        root = tmp_path / "000-00-0000"
        (root / ".venv" / "bin").mkdir(parents=True)
        (root / ".venv" / "bin" / "activate").write_text("VIRTUAL_ENV=x")
        (root / ".venv" / "bin" / "python3").write_text("")
        sub = root / "summaries"
        sub.mkdir()
        monkeypatch.chdir(sub)
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))

        report = P.check(modules=(), workspace=root)
        remedy = P._remedy_text(report)
        assert "000-00-0000" not in remedy, (
            "the raw workspace path reached the remedy, which both channels "
            "print verbatim — while the JSON `workspace` field redacts it")
        assert "cd " in remedy, "the `cd` was dropped along with the value"
        assert "000-00-0000" not in P.render_markdown(report)
        assert "000-00-0000" not in json.dumps(P._report_to_dict(report))

    def test_an_ordinary_path_survives_intact(self, tmp_path, monkeypatch):
        """The counterweight: only a path SHAPED like PII is touched, or the
        remedy stops being something the reader can paste."""
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "activate").write_text("VIRTUAL_ENV=x")
        (tmp_path / ".venv" / "bin" / "python3").write_text("")
        sub = tmp_path / "summaries"
        sub.mkdir()
        monkeypatch.chdir(sub)
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))
        remedy = P._remedy_text(P.check(modules=(), workspace=tmp_path))
        assert f"cd {tmp_path}" in remedy


class TestAWorkspaceThatIsNotThere:
    """`--workspace` naming a root that does not exist is a fact about the
    ARGUMENT, and it used to be answered by saying nothing at all."""

    def test_it_blocks_rather_than_certifying_the_host_interpreter(self,
                                                                  tmp_path):
        """The fail-open, arriving through the argument instead of the
        filesystem: `.venv` under a nonexistent root simply fails to stat, the
        ENOENT branch read that as "no workspace venv here", and the interpreter
        comparison never ran — so a typo'd or stale `--workspace` certified
        whatever host Python the check happened to be on.
        """
        report = P.check(modules=(), workspace=tmp_path / "not-a-real-root")
        assert report.blocking, (
            "a workspace that does not exist was answered with a clean bill "
            "of health for an interpreter nothing compared")
        assert report.workspace_missing
        assert not report.venv_broken and not report.venv_uncomparable, (
            "a missing root is not a statement about any virtualenv")
        assert not report.unactivated, (
            "answered with `run.sh`, which cannot conjure a directory")

    def test_the_remedy_is_about_the_argument_not_the_venv(self, tmp_path):
        """Every `.venv` remedy would run inside a directory that is not there:
        `rm -rf .venv` silently succeeds, `python3 -m venv .venv` fails, and the
        reader is left debugging a virtualenv that was never the subject."""
        remedy = P._remedy_text(
            P.check(modules=(), workspace=tmp_path / "not-a-real-root"))
        assert "--workspace" in remedy
        assert "rm -rf" not in remedy, (
            "a rebuild aimed inside a root that does not exist")
        assert "-m venv .venv" not in remedy


class TestTheAffectedSkillsAreNotGuessed:
    """The warning verdict says the skills it does NOT name are unaffected, so a
    short list is a false claim rather than a smaller one."""

    # The import a skill's own text reveals, and the package behind it. Only
    # packages whose use is visible in skill prose can be checked this way —
    # `reportlab` is reached through a generator the skill WRITES at runtime,
    # so it is out of scope here and stays hand-maintained.
    VISIBLE = {"pdfplumber": "parse_utils.py", "openpyxl": "openpyxl"}

    def test_the_affected_skills_include_every_caller(self):
        """`/tax-verify` runs `parse_utils.py extract` on the tax software's
        PDF, page by page, and was told a missing `pdfplumber` did not affect
        it. The tuple is hand-maintained and nothing tied it to the skills that
        actually make the call — so it drifted the moment a second skill started
        parsing documents.
        """
        listed = {package: set(skills)
                  for package, _import, _role, skills in P.RUNTIME_DEPENDENCIES}
        missing = []
        for package, marker in self.VISIBLE.items():
            callers = {f"/{name}" for name in _engine_calling_skills_cached()
                       if marker in skill_text(name)}
            assert callers, f"the marker {marker!r} no longer matches any skill"
            for caller in sorted(callers - listed[package]):
                missing.append(f"{package} is used by {caller}, which the "
                               f"verdict calls unaffected")
        assert not missing, missing

    def test_the_list_names_no_skill_that_does_not_exist(self):
        """The other direction: a renamed skill leaves the verdict pointing at
        one nobody can run, which is the same lie told backwards."""
        from tests.skill_text import SKILLS_DIR
        names = {f"/{d.name}" for d in SKILLS_DIR.iterdir()
                 if (d / "SKILL.md").is_file()}
        assert len(names) > 15, f"the skills tree moved: {sorted(names)}"
        for package, _import, _role, skills in P.RUNTIME_DEPENDENCIES:
            for skill in skills:
                assert skill in names, (
                    f"{package} names {skill}, which is not a skill in this "
                    f"repository")


class TestTheFlagshipVerdictLeadsWithWhatWorks:
    """A skill copied away from its engine, on a stock macOS 3.9, in a workspace
    whose `.venv` was never activated — all three conditions at once."""

    def _flagship(self, tmp_path, monkeypatch):
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text("VIRTUAL_ENV=" + str(tmp_path / ".venv"))
        (venv / "python3").write_text("")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))
        monkeypatch.setattr(P, "_HERE", tmp_path)          # no engine files
        monkeypatch.setattr(P, "_ENGINE_ROOT", tmp_path)
        report = P.check(
            modules=(("agents.lib.tie_out", "the tie-out gate", "load_ledger"),),
            workspace=tmp_path)
        assert report.files_missing and report.unactivated
        return report

    def test_the_clone_comes_before_the_launcher_that_is_missing(
            self, tmp_path, monkeypatch):
        """`venv_remedy` names `sh agents/lib/run.sh` — and on this report
        `agents/lib/` is exactly what is gone, so that first instruction exits
        127 in the reader's tree. The remedy that restores it has to lead."""
        remedy = P._remedy_text(self._flagship(tmp_path, monkeypatch))
        assert "git clone" in remedy
        assert remedy.index("git clone") < remedy.index("agents/lib/run.sh"), (
            "the verdict opens with a launcher it has just reported missing")

    def test_an_old_interpreter_is_named_even_when_the_clone_leads(
            self, tmp_path, monkeypatch):
        """`clone_remedy` builds with a supported interpreter silently. A reader
        on 3.9 who is not told why sees `python3.12` and uses their own
        `python3`, rebuilding the identical failure."""
        monkeypatch.setattr(P.sys, "version_info", (3, 9, 6))
        report = self._flagship(tmp_path, monkeypatch)
        assert not report.python_ok
        remedy = P._remedy_text(report)
        assert "Install or select Python" in remedy, (
            "the interpreter is the root cause and the verdict never says so")
        # NO deletion at all on this path. `venv_remedy`'s escape hatch ends in
        # `_rebuild_steps`, and the clone remedy above has already built the
        # environment inside the fresh checkout — so the hatch would tell the
        # reader to delete what they made two paragraphs earlier and build it
        # again, with an `rm -rf` between the two identical procedures.
        assert "rm -rf" not in remedy, remedy
        assert remedy.count("-m venv .venv") == 1, (
            f"the virtualenv is built twice on one report:\n{remedy}")


class TestTheLauncherRuleReachesBeyondTheSkills:
    """`test_every_engine_invocation_activates_the_workspace_venv` walks
    `.agents/skills/` only, so every other document naming an engine command is
    unchecked. `docs/ops/` IS in the hook's watched roots now — the acceptance
    record that lives there is an asserted artifact — so an edit to either file
    beneath it selects a module rather than passing unseen."""

    # Documents that instruct a HUMAN OR AGENT to run an engine command. Not a
    # glob over docs/: most of them discuss the engine without invoking it, and
    # a rule that fires on prose is one that gets weakened until it fires on
    # nothing.
    DOCUMENTS = ("docs/ops/season-open-runbook.md",
                 "docs/ops/review-subagent-acceptance.md",
                 "docs/QUICKSTART.md",
                 "README.md",
                 "CONTRIBUTING.md",
                 "AGENTS.md",
                 ".github/ISSUE_TEMPLATE/bug_report.yml")

    # An engine import in EVERY spelling: `agents.lib.x`, the `sys.path` `lib.x`,
    # and the PACKAGE-level `from agents.lib import x` / `from lib import x`.
    # That last pair is what a required dot after `lib` excluded — the same
    # module, bound the same way, one character short of being an engine call as
    # far as either check below was concerned.
    def test_no_document_invokes_the_engine_on_a_bare_interpreter(self):
        """The season-open runbook ran `python3 -c "from agents.lib..."` with no
        activation at all — on a stock macOS host `python3` (3.9) that dies with
        the PEP 604 TypeError this module exists to diagnose, and on a host with
        a stale global install it silently verifies source pinning for a return
        using the wrong tree. AGENTS.md §4 says every engine call goes through
        the launcher; nothing checked the documents that make them.
        """
        offenders = []
        for rel in self.DOCUMENTS:
            path = REPO / rel
            assert path.exists(), f"{rel} moved — this test now checks nothing"
            offenders += self._bare_engine_offenders(
                rel, path.read_text().splitlines())
        assert not offenders, (
            f"engine invoked on a bare interpreter outside the skills: "
            f"{offenders}. Route each through `{LAUNCHER}`.")

    def test_the_document_check_sees_every_import_spelling(self):
        """The controls the document check went in without. It runs over a corpus
        that legitimately contains no offender, so a pattern that matches nothing
        and a corpus that holds nothing are the same green — the vacuity the
        heredoc scanner below already has a pair of controls for.

        `python3 -c 'from agents.lib import ptet'` is the form that proved the
        point. It reaches the engine on whatever `python3` resolves to, and the
        check read it as prose because the import named the package rather than
        the submodule — one character's difference in a spelling the engine does
        not distinguish at all.
        """
        for form in ("python3 -c 'from agents.lib import ptet'",
                     "python3 -c 'from lib import tax_law_fetcher'",
                     "python3 -c 'import agents.lib.ptet'",
                     "python3 -c 'from agents . lib import ptet'",
                     "python3 -c 'import agents . lib . ptet'",
                     "python3 -c 'from agents.lib.ptet import _salt_cap'",
                     # The engine as one ITEM of an import list, which is valid
                     # python and read as an import of `json` alone: the module
                     # was required to follow the keyword IMMEDIATELY.
                     "python3 -c 'import json, agents.lib.ptet'",
                     "python3 -c 'import os, sys, lib.ptet'",
                     "python3 -c 'import json, agents.lib.ptet as p'",
                     "python3 -m agents.lib.preflight",
                     "python3 agents/lib/tie_out.py"):
            assert BARE_ENGINE_CALL.search(form), (
                f"an engine call on a bare interpreter went unseen: {form!r}")

        # The other side of the boundary, and the reason the pattern is not
        # simply "python3 near lib": routed calls carry no bare interpreter, and
        # `-m venv` / `-m pytest` are not engine calls however they are run.
        for clear in (f"{LAUNCHER} -c 'from agents.lib import ptet'",
                      f"{LAUNCHER} -m agents.lib.preflight",
                      "python3 -m venv .venv",
                      "python3 -m pytest tests/ -q",
                      "python3 -c 'import libraries'",
                      "python3 -c 'import json, os'",
                      "python3 -c 'import json, libraries'",
                      # ...and the reason each skipped item must be a dotted
                      # NAME rather than a run of characters: `lib` is admitted
                      # bare, so a loose middle would read any sentence holding
                      # "import" and a later "lib" as an engine call — and the
                      # corpus this pattern runs over is documentation.
                      "python3 -c 'x' to import json, then read the lib docs"):
            assert not BARE_ENGINE_CALL.search(clear), (
                f"a routed or non-engine command was read as an offence: "
                f"{clear!r}")

        # And the same forms written across LINES, which is how the audit now
        # reads them. Every one of these is a valid single command that no
        # physical line of holds whole — the fixture asserts that first, because
        # a wrapped form that happened to match per line would prove nothing
        # about the join.
        for wrapped in (["python3 \\", "  -m agents.lib.ptet"],
                        ["python3 \\", "  agents/lib/tie_out.py"],
                        ["python3 -c 'import sys",
                         "from agents.lib import ptet'"],
                        # The two joins in the order that needs BOTH: the `-c`
                        # opener straddles the continuation, so it is not on the
                        # first physical line and a scan that looks for it once,
                        # before joining, never sees it.
                        ["python3 \\", '-c "', "from agents.lib import ptet",
                         '"']):
            assert not any(BARE_ENGINE_CALL.search(line) for line in wrapped), (
                f"this fixture matches line by line, so it tests no join: "
                f"{wrapped}")
            assert len(self._bare_engine_offenders("synthetic.md", wrapped)) == 1, (
                f"a bare engine call written across lines went unseen — the "
                f"form the documents audit read one line at a time: {wrapped}")

        # ...and the join has to know when the `-c` body is still OPEN, which is
        # not the same question as how many quote characters it holds. An
        # ESCAPED quote is a character and closes nothing, so parity said the
        # body had ended, the join stopped mid-command, and the interpreter and
        # the engine import below it became separate units — each clean read
        # alone, the command between them running the engine on bare `python3`.
        for escaped in (['python3 -c "x = \'\\"\'', 'from agents.lib import ptet"'],
                        ["python3 -c 'x = \"", "from agents.lib import ptet'"],
                        ['python3 -c "s = \\"', 'from agents.lib import ptet"']):
            assert not any(BARE_ENGINE_CALL.search(line) for line in escaped), (
                f"this fixture matches line by line, so it tests no join: "
                f"{escaped}")
            assert len(self._bare_engine_offenders(
                "synthetic.md", escaped)) == 1, (
                f"an escaped quote inside a `-c` body ended the join early and "
                f"the engine import below it escaped: {escaped}")

        # The bound: a body that really has CLOSED must not swallow the line
        # under it. Joined anyway, an ordinary two-command block reads as one
        # unit and the launcher on the second line clears the interpreter on the
        # first — a silent pass bought by over-joining, which is the direction a
        # quote model gets wrong when it is too eager.
        closed = ["python3 -c 'print(1)'", f"{LAUNCHER} -m agents.lib.ptet"]
        assert len(list(_join_units(closed))) == 2, (
            f"a closed `-c` body swallowed the command below it: "
            f"{[u for _, u, _ in _join_units(closed)]}")

        # ...and the same forms carrying an ACTIVE BACKSLASH, which the shell
        # removes before python sees anything. `\\.` and `\\/` are the ordinary
        # targets spelled with a quoting character, and a continuation is the
        # same character doing the same job across a line — `_join_units` joins
        # those lines precisely so a command can be read whole, and then no
        # target pattern could cross the `\\` it left in the middle. That unit
        # arrived ENROLLED as an engine caller and was cleared by the audit it
        # was enrolled for, which is the worse of the two shapes.
        for escaped in (["python3 -m agents\\.lib\\.ptet"],
                        ["python3 agents\\/lib\\/tie_out.py"],
                        ["python3 -m \\", "  agents.lib.ptet"],
                        # SPLICED, so the two halves are one word: a
                        # continuation removes the backslash and the newline and
                        # nothing else, which is why the indented mirror below
                        # is two words and correctly clean.
                        ["python3 -m agents.\\", "lib.ptet"],
                        ["python3 agents/lib/\\", "tie_out.py"]):
            assert not any(BARE_ENGINE_CALL.search(line) for line in escaped), (
                f"this fixture matches line by line, so it tests nothing about "
                f"the escape: {escaped}")
            assert len(self._bare_engine_offenders(
                "synthetic.md", escaped)) == 1, (
                f"a bare engine call spelled with a backslash the shell removes "
                f"went unseen: {escaped}")

        # The three bounds, which are the shell's own rules and not a
        # simplification of them. Inside DOUBLE quotes a backslash before a dot
        # is literal, so python really is handed `agents\.lib` and really is not
        # running the engine; inside SINGLE quotes it always is; and an INDENTED
        # continuation splices nothing, leaving two words. Each would be a false
        # accusation on a correct document, which is what a rule that just
        # deleted every backslash would have produced.
        for kept in (['python3 -m "agents\\.lib.ptet"'],
                     ["echo 'python3 -m agents\\.lib\\.ptet'"],
                     ["python3 -m agents.\\", "  lib.ptet"]):
            assert not self._bare_engine_offenders("synthetic.md", kept), (
                f"a backslash the shell KEEPS was removed, indicting a command "
                f"that names no module: {kept}")

        # ...and the control that says it is the BACKSLASH doing that and not
        # the quote. The unescaped mirror of the single-quoted line above is
        # reported, and stays reported — see `_bare_engine_command` on why a
        # quoted argument is not treated as inert.
        assert self._bare_engine_offenders(
            "synthetic.md", ["echo 'python3 -m agents.lib.ptet'"]), (
            "the single-quoted bound above is clearing on the quote rather than "
            "on the backslash, so it is not the bound it claims to be")

        # ...and the direction a widening always threatens. These are two
        # commands, both correct, and read as one string the pattern runs from
        # the first interpreter straight across the separator into the launcher's
        # target — a document indicted for demonstrating the rule.
        compound = [f"python3 -m pytest tests/ -q && {LAUNCHER} -m agents.lib.ptet"]
        assert not self._bare_engine_offenders("synthetic.md", compound), (
            f"a compliant compound command was read as a bare engine call: "
            f"{compound}")

    def test_a_launcher_elsewhere_on_the_line_does_not_route_the_heredoc(self):
        """Routing was read off the WHOLE line, so any mention of the launcher to
        the left of the interpreter cleared the heredoc.

        The fallback is the case that matters, and the perverse one: in
        `<launcher> x.py || python3 - <<'PY'` the engine runs on bare `python3`
        precisely WHEN the launcher fails — the stock-interpreter host this
        module exists to diagnose — and that spelling was the one guaranteed to
        be read as routed. `;` and `&&` do the same thing without the irony.
        """
        body = ["import json",
                "from agents.lib.ptet import _salt_cap", "PY"]
        for cmd in (f"{LAUNCHER} missing.py || python3 - <<'PY'",
                    f"{LAUNCHER} -m agents.lib.preflight; python3 - <<'PY'",
                    f"echo 'use {LAUNCHER}' && python3 - <<'PY'"):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", cmd] + body + ["```"])
            assert seen == 1, f"not recognised as an opener: {cmd}"
            assert len(found) == 1, (
                f"a launcher to the LEFT of the interpreter cleared a heredoc "
                f"the interpreter owns: {cmd} -> {found}")

        # The mirror, so the fix cannot be "stop believing the launcher". A
        # separator BEFORE the launcher leaves the launcher owning the heredoc,
        # and this still registers as an opener — a control that clears by never
        # being looked at proves nothing. The env-assignment spelling is what
        # keeps it looked at: the launcher OWNING a heredoc is not an
        # interpreter invocation at all, so `python3 -c 'print(1)' || {launcher}
        # - <<'PY'` is the plain form reached by another road and is pinned
        # beside it, in the test that owns that claim.
        for cmd in (f"cd /tmp && TAXPY=python3 {LAUNCHER} - <<'PY'",):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", cmd] + body + ["```"])
            assert seen == 1, f"not recognised as an opener: {cmd}"
            assert not found, (
                f"the routed form was reported as bare: {cmd} -> {found}")

    def test_only_the_exact_launcher_routes_a_heredoc(self):
        """The test above asks WHERE the launcher sits; this one asks whether it
        is the launcher at all.

        Routing was decided by `startswith`, which is a question about a prefix
        rather than about a command word, so every executable the launcher\'s
        path is a prefix OF inherited its clearance. `sh agents/lib/run.sh.bak -
        <<\'PY\'` — a backup, a rename, a half-finished edit — was recorded as
        routed and its body waved through unread, though the one launcher
        AGENTS.md \u00a74 mandates never ran. A clearance is the direction that must
        not be loose: a body wrongly scanned is a false accusation someone reads,
        a body wrongly cleared is silence.

        Tightening the clearance alone is not enough, and that is the second
        half. With an exact boundary the impostor stops being routed, but its
        first word is `sh` rather than an interpreter, so it stops being an
        OPENER too — neither counted nor scanned, which is the same silence
        wearing a different name. A command whose executable carries the
        mandated launcher\'s path and is not it is a \u00a74 violation in the
        compliant form\'s clothes, so it is opened and read.
        """
        body = ["import json",
                "from agents.lib.ptet import _salt_cap", "PY", "```"]
        for impostor in (f"{LAUNCHER}.bak - <<\'PY\'",
                         f"{LAUNCHER}.orig - <<\'PY\'",
                         f"{LAUNCHER}2 - <<\'PY\'",
                         f"TAXPY=python3 {LAUNCHER}.bak - <<\'PY\'"):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", impostor] + body)
            assert seen == 1, (
                f"a near miss of the launcher opened nothing at all, so its "
                f"body was never read: {impostor}")
            assert len(found) == 1, (
                f"an executable that merely BEGINS with the launcher\'s path "
                f"routed the heredoc: {impostor} -> {found}")

        # The other side of the same boundary, so the fix cannot be "stop
        # believing the launcher". The exact spelling still clears, with and
        # without the assignment prefix that has to be stripped before the
        # first word is even visible.
        for routed in (f"{LAUNCHER} - <<\'PY\'",
                       f"TAXPY=python3 {LAUNCHER} - <<\'PY\'"):
            assert self._heredoc_offenders(
                "synthetic.md", ["```sh", routed] + body) == ([], 1), (
                f"the mandated launcher stopped routing its own heredoc: "
                f"{routed}")

        # And the third side, which the impostor branch opened when it was
        # written as a substring search. A near miss is a matter of POSITION, so
        # a command that merely CARRIES the launcher\'s path in argv is not one:
        # `echo` runs nothing at all, and its body was scanned and reported for
        # every import in it. The document such a line appears in is one
        # demonstrating the rule correctly, which is the last place a gate about
        # the rule may fire.
        for mention in (f"echo {LAUNCHER}.bak <<\'PY\'",
                        f"echo {LAUNCHER} <<\'PY\'",
                        f"cat > note.txt <<\'PY\'  # {LAUNCHER}"):
            assert self._heredoc_offenders(
                "synthetic.md", ["```sh", mention] + body) == ([], 0), (
                f"a heredoc fed to a command that executes nothing was read as "
                f"an impostor of the launcher: {mention}")

    def test_a_compound_command_label_is_not_a_command_word(self):
        """`case` and a function definition both put a token ending in `)` where
        the command word goes, and the shell parses it away before running
        anything. Read as the executable it is neither an interpreter nor the
        launcher, so `yes) python3 - <<\'PY\'` opened no heredoc at all: nothing
        counted, nothing scanned, and a bare engine call in the body clean away.

        The same silent pass KEYWORD_PREFIX closed for `if`/`while`/`do`, one
        construct along — and the unit audit cannot cover for either, because the
        interpreter is on the opener and the import is in the body, so no single
        line holds both. A function definition reaches here as a bare `) {`,
        `_commands` having already cut it at the `(` that makes a subshell.
        """
        body = ["import json",
                "from agents.lib.ptet import _salt_cap", "PY", "```"]
        for labelled in ("yes) python3 - <<\'PY\'",
                         "*) python3 - <<\'PY\'",
                         "[Yy]) python3 - <<\'PY\'",
                         "run() { python3 - <<\'PY\'",
                         # bash spells a definition two ways and only one of
                         # them has a parenthesis to be cut at. `function run {`
                         # left `function` standing as the executable, which is
                         # not an interpreter, so the heredoc was neither
                         # counted nor scanned — the same silence, one spelling
                         # along, in a construct this class already covers.
                         "function run { python3 - <<\'PY\'",
                         "function run() { python3 - <<\'PY\'",
                         "yes) TAXPY=1 python3 - <<\'PY\'"):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", labelled] + body)
            assert seen == 1, (
                f"a labelled command opened no heredoc, so its body was never "
                f"read: {labelled}")
            assert len(found) == 1, (
                f"a bare engine call behind a compound-command label went "
                f"unseen: {labelled} -> {found}")

        # Routing survives the label, so the fix is not "indict every arm".
        assert self._heredoc_offenders(
            "synthetic.md", ["```sh", f"yes) {LAUNCHER} - <<\'PY\'"] + body
            ) == ([], 1), "a labelled command stopped being routed by the launcher"

        # The keyword form takes the NAME with it and stops there: `function`
        # plus one word is the whole declaration, and a rule that ran further
        # would eat the command it is meant to expose.
        assert _command_head("function run { python3 - <<'PY'") == (
            "python3 - <<'PY'"), "the function declaration was not parsed away"
        # ...and it requires the NAME. `function {` is not a declaration bash
        # accepts, and admitting the keyword alone would strip the word
        # "function" off the front of any sentence that opens with it.
        assert _command_head("function { python3 -m agents.lib.ptet").startswith(
            "function"), (
            "a nameless `function` was parsed away as a declaration")

        # And the label is ONE token: prose left over from a `(` split keeps its
        # head, or every parenthetical in the tree becomes a command.
        assert _command_head("the launcher invokes) and one module") == (
            "the launcher invokes) and one module"), (
            "a multi-word parenthetical was read as a compound-command label")

        # ...and it stops at a `(`, so the label cannot disagree with the
        # splitter that has already honoured one. `_commands` cuts `(yes) cmd`
        # at the `(` and hands on `yes) cmd`; a label allowed to swallow the
        # parenthesis whole would make the unsplit text mean something else.
        assert _command_head("(yes) python3 -m agents.lib.ptet") == (
            "(yes) python3 -m agents.lib.ptet"), (
            "a compound-command label reached across a subshell opener")

    def test_an_exec_word_s_option_operand_is_not_the_command_word(self):
        """An option is not always ONE token, and the prefix that unwraps `env`
        assumed it was.

        `env -u PYTHONPATH python3 - <<'PY'` is the ordinary way a document
        shows the engine run with a variable cleared, and `-u` takes an operand:
        the prefix ate `env -u ` and handed back `PYTHONPATH` as the executable.
        Read as a command word that is neither an interpreter nor the launcher,
        so the heredoc opened nothing, its body was never read, and a bare engine
        call inside it passed both audits — the same silence the two prefixes
        above were written for, arriving through a prefix this file already had.

        The operand set is CLOSED because consuming a token that is NOT an
        operand is the mirror failure: it would swallow the interpreter and clear
        the command. `-S` is left out on exactly that ground, its operand being
        the command itself.
        """
        body = ["import json",
                "from agents.lib.ptet import _salt_cap", "PY", "```"]
        for wrapped in ("env -u PYTHONPATH python3 - <<'PY'",
                        "env --unset PYTHONPATH python3 - <<'PY'",
                        "env -u A -u B python3 - <<'PY'",
                        "env -i -u PYTHONPATH -C /tmp python3 - <<'PY'",
                        "/usr/bin/env -C /tmp python3 - <<'PY'",
                        "exec -a taxpy python3 - <<'PY'"):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", wrapped] + body)
            assert seen == 1, (
                f"an option operand stood where the command word goes, so the "
                f"heredoc opened nothing: {wrapped}")
            assert len(found) == 1, (
                f"a bare engine call behind an exec word's option operand went "
                f"unseen: {wrapped} -> {found}")

        # Routing survives the unwrapping, so the fix is not "indict every env".
        assert self._heredoc_offenders(
            "synthetic.md", ["```sh", f"env -u PYTHONPATH {LAUNCHER} - <<'PY'"]
            + body) == ([], 1), (
            "clearing a variable stopped the launcher from routing its heredoc")

        # The attached spelling is ONE token and must stay on the plain branch,
        # or the operand arm eats the interpreter behind it.
        assert _command_head("env --unset=PYTHONPATH python3 -m agents.lib.ptet"
                             ) == "python3 -m agents.lib.ptet", (
            "`--unset=X` was read as an option plus a separate operand")

        # ...and `-S`, whose operand IS the command. Consuming it would hide the
        # interpreter, so DETACHED it stays on the plain branch and the quoted
        # word keeps the head — where INTERPRETER still reads it.
        head = _command_head("env -S 'python3 -x' - <<'PY'")
        assert INTERPRETER.search(re.match(r"\S+", head).group()), (
            f"`env -S` consumed the command it was given: {head!r}")

        # ATTACHED it needs its own arm, and the plain branch could not be it:
        # `-\S+` runs to the next SPACE, so it took `-S'python3`, then `-m`, and
        # handed back `agents.lib.ptet'` — an engine target with no interpreter
        # in front of it, cleared by every audit here. A spelling `env`
        # documents and macOS accepts, in three forms.
        for attached in ("env -S'python3 -m agents.lib.ptet'",
                         'env -S"python3 -m agents.lib.ptet"',
                         "/usr/bin/env --split-string='python3 -m "
                         "agents.lib.ptet'"):
            assert _unactivated(attached), (
                f"`env -S` with an attached operand hid the interpreter it "
                f"holds: {attached!r} -> {_command_head(attached)!r}")

        # The half the arm must not take with it: the operand may hold the
        # LAUNCHER, and consuming one character too many would make it an
        # offence. It also must not reach a DETACHED operand — `['\"]?` matches
        # no space — which is what the assertion above this block pins.
        assert not _unactivated(f"env -S'{LAUNCHER} -m agents.lib.ptet'"), (
            "the launcher inside an attached `-S` operand was read as bare")
        assert self._heredoc_offenders(
            "synthetic.md", ["```sh", "env -S'python3 -' <<'PY'"] + body
            ) == ([f"synthetic.md:4: heredoc opened at line 2 reaches the "
                   f"engine: from agents.lib.ptet import _salt_cap"], 1), (
            "an attached `-S` operand opened no heredoc, so its body went "
            "unread")

    def test_no_document_feeds_the_engine_to_a_bare_interpreter_by_heredoc(self):
        """The check above reads one COMMAND at a time, so it sees an engine call
        only when the interpreter and the import are written into the same one. A
        heredoc splits them across two — `python3 - <<'PY'` is the command, and
        `from agents.lib...` three lines down is its INPUT, not part of it — and
        every form the docstring above calls "the three that actually reach the
        engine" walks straight through. Joining `\\`-continued lines closed the
        other way a command spans lines; this is the way that no join reaches,
        because the body is not the command.

        Found by writing one: the acceptance record's cache generator was drafted
        as a bare-`python3` heredoc, and this module stayed green while a document
        it explicitly covers told the reader to run the engine off whatever
        `python3` resolved to. A guard that is green because of how the offense
        was FORMATTED is worse than an absent one; it is the same reason the
        launcher rule exists at all.
        """
        offenders = []
        scanned = 0
        for rel in self.DOCUMENTS:
            path = REPO / rel
            assert path.exists(), f"{rel} moved — this test now checks nothing"
            found, seen = self._heredoc_offenders(
                rel, path.read_text().splitlines())
            offenders += found
            scanned += seen
        assert not offenders, (
            f"engine reached from a bare-interpreter heredoc: {offenders}. "
            f"Route the heredoc through `{LAUNCHER}` — it accepts `-` and "
            "puts the repository root on the import path.")
        # `scanned` is deliberately NOT asserted against the corpus: a document
        # set that legitimately contains no heredoc must not be a failure. What
        # stops this passing vacuously is the pair of controls below, which run
        # the same scanner over inputs whose answers are known.
        del scanned

    @pytest.mark.parametrize("skill", sorted(_engine_calling_skills_cached()))
    def test_no_skill_reaches_the_engine_through_a_heredoc(self, skill):
        """The same two scanners, over the corpus the launcher rule is FOR.

        They were written for the documents and only ever run there, and the
        per-skill audit next door is `_bare_engine_units` alone — which reads a
        command, never a here-document body. So a SKILL.md block spelling
        `python3 - <<\'PY\'` with an engine import under it passed both: the
        opener names no engine target and the body is not a command. Same for the
        generated-script pair, `cat > gen.py <<\'PY\'` and then `python3 gen.py`.

        AGENTS.md §4 makes no distinction between a skill and a runbook — every
        engine call goes through the launcher — and a skill is the corpus where
        it matters MORE, being the file an agent executes rather than one a human
        reads. The scanners live in this class because the documents needed them
        first; there is nothing document-shaped about the question.
        """
        offenders = []
        for path in _skill_files(skill):
            found, _seen = self._heredoc_offenders(
                str(path.relative_to(REPO)), path.read_text().splitlines())
            offenders += found
        assert not offenders, (
            f"engine reached from a bare-interpreter heredoc in /{skill}: "
            f"{offenders}. Route it through `{LAUNCHER}` — it accepts `-` and "
            "puts the repository root on the import path.")

    def test_the_skill_heredoc_audit_is_not_vacuous(self):
        """...and the control that says the audit above LOOKS at a skill.

        Its corpus is clean, so it cannot tell "no offender" from "no file was
        read" — the vacuity this module keeps meeting. `_skill_files` is the
        seam: return nothing for a skill and the loop iterates nothing while
        reporting a pass over it. So a skill\'s real files are fed to the real
        scanner with one offending block appended, and the offence has to come
        back named after that skill.
        """
        skill = sorted(_engine_calling_skills_cached())[0]
        files = _skill_files(skill)
        assert files, f"/{skill} has no files, so the audit reads nothing"
        lines = files[0].read_text().splitlines() + [
            "```sh", "python3 - <<'PY'", "from agents.lib import ptet", "PY",
            "```"]
        found, seen = self._heredoc_offenders("SKILL.md", lines)
        assert seen and found, (
            "the scanner the per-skill audit calls found nothing in a skill "
            f"body carrying a bare-interpreter heredoc: {seen} heredoc(s)")

    def test_the_heredoc_scanner_catches_a_known_offender(self):
        """The positive control. The check above is green when the corpus has no
        heredoc AND when the scanner is broken, and those two look identical from
        outside — the same argument the hook's own tests make about a gate that
        never fires."""
        offending = ["```sh", "python3 - <<'PY'", "import json",
                     "from agents.lib.ptet import _salt_cap", "PY", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", offending)
        assert seen == 1 and len(found) == 1, (
            f"the scanner missed a plainly offending heredoc: {found}, "
            f"{seen} heredoc(s) seen")

        # The `sys.path` + `from lib.x` spelling, which is how the real one got
        # past the per-line pattern in the first place.
        dodge = ["python3 - <<'PY'", 'import sys; sys.path.insert(0, "agents")',
                 "from lib.tax_law_fetcher import create_empty_tax_law", "PY"]
        found, _ = self._heredoc_offenders("synthetic.md", dodge)
        assert len(found) == 1, f"the sys.path spelling still slips through: {found}"

        # And the PACKAGE-level spelling of both. `from agents.lib import ptet`
        # binds the same module as `from agents.lib.ptet import _salt_cap`; the
        # dot the pattern used to require after `lib` is not what makes an import
        # an engine call, and requiring it read one of the two commonest
        # spellings as ordinary text.
        package = ["python3 - <<'PY'", "from agents.lib import ptet",
                   "from lib import tax_law_fetcher", "PY"]
        found, _ = self._heredoc_offenders("synthetic.md", package)
        assert len(found) == 2, (
            f"the package-level import spelling slips through: {found}")

    def test_the_heredoc_scanner_clears_the_routed_form(self):
        """The negative control. A scanner that flags everything is as useless as
        one that flags nothing, and it would make the rule impossible to satisfy.

        The launcher form is an OPENER whose body is read and cleared, and it
        used to clear by not being looked at: `run.sh` is not an interpreter, so
        `<launcher> - <<'PY'` matched nothing, counted nothing, and scanned
        nothing. Two states that a caller reading `found` cannot tell apart, and
        the `routed` branch — the one that actually encodes the rule — was
        reachable only through the contrived `TAXPY=python3 <launcher>` spelling
        written to reach it. Keying on the command WORD collapsed that: the
        launcher execs an interpreter, so it opens a heredoc like one, and the
        clearance is a verdict rather than an absence. `seen` is asserted here
        precisely because it is what separates the two.
        """
        body = ["import json",
                "from agents.lib.ptet import _salt_cap", "PY"]
        # The second spelling is the same claim with an interpreter earlier on
        # the LINE and the launcher still owning the delimiter — the leak that
        # let `cat <<PY` be indicted, seen from the compliant side.
        for cmd in (f"{LAUNCHER} - <<'PY'",
                    f"python3 -c 'print(1)' || {LAUNCHER} - <<'PY'",
                    # The launcher reached through an assignment prefix, which
                    # is not a command word.
                    f"TAXPY=python3 {LAUNCHER} - <<'PY'"):
            found, seen = self._heredoc_offenders("synthetic.md", [cmd] + body)
            assert (found, seen) == ([], 1), (
                f"the routed form must be SEEN and cleared, not skipped: "
                f"{cmd} -> {found}, {seen} seen")

        # And the launcher as an ARGUMENT is not the routed form. This runs bare
        # python with the launcher sitting in argv, which a token search reads as
        # routed and clears — the same mistake as a launcher in a comment, moved
        # to the one place left that a substring test cannot see.
        argument = [f"python3 - {LAUNCHER} <<'PY'"] + body
        found, seen = self._heredoc_offenders("synthetic.md", argument)
        assert (seen, len(found)) == (1, 1), (
            f"the launcher passed as an argument cleared a body bare python "
            f"reads: {found}, {seen} seen")

        # The mirror: an interpreter named as an argument is not an invocation.
        named = ["```sh", "echo python3 <<'PY'"] + body + ["```"]
        assert self._heredoc_offenders("synthetic.md", named) == ([], 0), (
            "`echo` was read as running the body it is fed")

        # `env` is not an executable the way `echo` is: it EXECS its argument, so
        # the interpreter after it is the one that reads the body. Read as a
        # command word it is neither an interpreter nor the launcher, and
        # `/usr/bin/env python3` — the standard portable spelling — opened
        # nothing at all.
        for prefix in ("/usr/bin/env ", "env ", "env -i "):
            wrapped = ["```sh", f"{prefix}python3 - <<'PY'"] + body + ["```"]
            found, seen = self._heredoc_offenders("synthetic.md", wrapped)
            assert (seen, len(found)) == (1, 1), (
                f"`{prefix}python3` was not read as running its heredoc: "
                f"{found}, {seen} seen")

            routed = ["```sh", f"{prefix}{LAUNCHER} - <<'PY'"] + body + ["```"]
            assert self._heredoc_offenders("synthetic.md", routed) == ([], 1), (
                f"`{prefix}{LAUNCHER}` is the routed form and must be seen and "
                "cleared")

        # A shell RESERVED WORD is the other thing that can sit in front of an
        # executable, and the same defect in a different vocabulary: read as a
        # command word `if` is not an interpreter, so `if python3 - <<'PY'` —
        # valid shell that runs python — opened nothing and its body went
        # unread. The unit audit cannot compensate: the interpreter is on the
        # opener and the import is in the body, and `BARE_ENGINE_CALL` needs
        # both in one command.
        for word in ("if", "elif", "then", "else", "while", "until", "do",
                     "time", "!", "{"):
            wrapped = ["```sh", f"{word} python3 - <<'PY'"] + body + ["```"]
            found, seen = self._heredoc_offenders("synthetic.md", wrapped)
            assert (seen, len(found)) == (1, 1), (
                f"`{word} python3` was not read as running its heredoc: "
                f"{found}, {seen} seen")

            routed = ["```sh", f"{word} {LAUNCHER} - <<'PY'"] + body + ["```"]
            assert self._heredoc_offenders("synthetic.md", routed) == ([], 1), (
                f"`{word} {LAUNCHER}` is the routed form and must be seen and "
                "cleared")

        # Stacked, and the reason `_command_head` counts progress instead of
        # iterations: SIX prefixes stand between the start of this command and
        # the word that runs, and under the `range(4)` this replaced the loop
        # returned a head that is still a prefix — which reads as "not an
        # interpreter" and CLEARS the command. A depth that reads as absurd is
        # the point: no honest document writes it, and the guard must not have a
        # depth at which it starts passing things.
        stacked = ["```sh", "while ! time env -i exec python3 - <<'PY'"]
        found, seen = self._heredoc_offenders("synthetic.md", stacked + body + ["```"])
        assert (seen, len(found)) == (1, 1), (
            f"a stacked prefix ran past the unwrapping bound: {found}, {seen} seen")
        # Stated against `_command_head` too, because through the scanner the
        # symptom is a COUNT and the cause is a truncated head. The first draft
        # of this case ended `TAXPY=python3 exec python3 …` under a short bound,
        # and `INTERPRETER` matched the `python3` in the assignment VALUE — so
        # the scanner reached the right verdict from a head that was still four
        # words of prefix, and a reverted bound passed. A control whose subject
        # is a position must assert the position.
        assert _command_head(
            "while ! time env -i exec python3 -m agents.lib.ptet"
        ) == "python3 -m agents.lib.ptet", (
            "the unwrapping stopped short of the word the shell runs")

        # And the other end of that loop: a head it cannot shorten must be
        # RETURNED, not spun on. Asserted against `_command_head` directly
        # because a hang has no failure message, only a hook that times out and
        # cancels itself silently.
        for degenerate in ("", "!", "$(", "python3", "if", "{"):
            assert isinstance(_command_head(degenerate), str)

        # The mirror for the reserved words, and the reason the list is closed:
        # a word that PRECEDES a command is unwrapped, a word that is a command
        # is not. `echo` is still `echo` with `if` in front of it.
        quoted = ["```sh", "if echo python3 <<'PY'"] + body + ["```"]
        assert self._heredoc_offenders("synthetic.md", quoted) == ([], 0), (
            "unwrapping a reserved word promoted an ARGUMENT to the executable")

        # And the bound on that list, which is what makes it a closed one rather
        # than "wrappers". `xargs` also runs its argument, and unwrapping it
        # would be a claim about stdin that is FALSE: xargs consumes the heredoc
        # itself, builds argv out of it, and the interpreter it then runs never
        # sees the body. Reporting an import in there would indict a command that
        # does not execute it.
        consumed = ["```sh", "xargs -0 python3 - <<'PY'"] + body + ["```"]
        assert self._heredoc_offenders("synthetic.md", consumed) == ([], 0), (
            "`xargs` was unwrapped as if it handed its stdin to the interpreter")

    def test_the_scanner_sees_an_opener_that_carries_shell_syntax(self):
        """An opener rarely ends at the terminator. Redirecting the body's output
        to a file is the commonest spelling in a runbook — it is how a fixture
        gets written — and the first pattern anchored `$` straight after the
        terminator, so every one of these was read as ordinary prose and its body
        never scanned. A guard that silently declines to look is the vacuity this
        class exists to rule out, so each tail gets its own case."""
        tails = ["> generated.json", ">> run.log", "2>/dev/null", "| jq .",
                 "&& echo done", "; echo done", "# write the fixture"]
        for tail in tails:
            lines = [f"python3 - <<'PY' {tail}", "import json",
                     "from agents.lib.ptet import _salt_cap", "PY"]
            found, seen = self._heredoc_offenders("synthetic.md", lines)
            assert seen == 1, f"tail {tail!r} was not recognised as an opener"
            assert len(found) == 1, (
                f"tail {tail!r} opened a heredoc whose body went unread: {found}")

        # A hyphenated delimiter is a valid shell word; `\\w+` stopped at the
        # hyphen, so the opener was not one and the body went unread.
        hyphen = ["python3 - <<'PY-CODE'", "import json",
                  "from agents.lib.ptet import _salt_cap", "PY-CODE"]
        found, seen = self._heredoc_offenders("synthetic.md", hyphen)
        assert (seen, len(found)) == (1, 1), (
            f"a hyphenated terminator went unscanned: {found}, {seen} seen")

        # Line-continued before the redirection. The continuation line is read as
        # the first body line, which is why the import below it is still found.
        cont = ["python3 - <<'PY' \\", "    > /tmp/fixture.json", "import json",
                "from agents.lib.ptet import _salt_cap", "PY"]
        found, seen = self._heredoc_offenders("synthetic.md", cont)
        assert (seen, len(found)) == (1, 1), (
            f"a line-continued opener went unscanned: {found}, {seen} seen")

        # Command substitution closes the line with `)`, not whitespace. Stated
        # BOTH ways round, because the two spellings disagreed: the prose tail
        # admits `)` directly while the fenced one — the permissive of the two by
        # design — required a space before any tail at all, so this exact line
        # inside a ```sh block, which is where a document actually writes shell,
        # matched nothing.
        subst = ['out="$(python3 - <<\'PY\')"', "import json",
                 "from agents.lib.ptet import _salt_cap", "PY"]
        for lines in (subst, ["```sh"] + subst + ["```"]):
            found, seen = self._heredoc_offenders("synthetic.md", lines)
            assert (seen, len(found)) == (1, 1), (
                f"a substituted heredoc went unscanned "
                f"{'inside' if lines is not subst else 'outside'} a fence: "
                f"{found}, {seen} seen")

    def test_a_group_inside_a_substitution_does_not_close_it(self):
        """`$( ... )` nests, and only the `$(` half of it was being counted.

        `out="$( (echo x); python3 - <<\'PY\'` is one command substitution
        holding a subshell, and the subshell\'s `)` popped the substitution. What
        sits under it is the double quote, so from that character on the line was
        marked inert — the `<<`, the delimiter and the interpreter with it — the
        opener matched nothing, no heredoc was counted and the body was never
        read. A silent pass, arriving through the very stack added to stop the
        double quote from swallowing a real opener.

        The quoted form is the whole of the bug and the reason it is worth a
        test: unquoted, `x=$( (echo y); ...` pops to NO context and every
        character stays active, so the same mistake costs nothing and shows
        nothing. Both are pinned, because a fix that only reached the visible
        half would look identical here.

        Depth errs the safe way. An unbalanced `(` inside the substitution now
        leaves the context OPEN, which keeps the rest of the line active and
        readable; the early pop hid it. That direction is the point — this
        scanner\'s recurring failure is the clearance nobody sees.
        """
        body = ["import json",
                "from agents.lib.ptet import _salt_cap", "PY", "```"]
        for opener in ('out="$( (echo x); python3 - <<\'PY\'',
                       'out="$( (cd /tmp && echo x); python3 - <<\'PY\'',
                       'out="$( (a); (b); python3 - <<\'PY\'',
                       'out="$( (echo "$(date)"); python3 - <<\'PY\'',
                       'x=$( (echo y); python3 - <<\'PY\''):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", opener] + body)
            assert seen == 1, (
                f"a group inside the substitution closed it, so the opener was "
                f"read as inert text: {opener}")
            assert len(found) == 1, (
                f"the body behind a nested group went unread: {opener} "
                f"-> {found}")

        # Routing survives the nesting, so this is not "indict every subshell".
        assert self._heredoc_offenders(
            "synthetic.md",
            ["```sh", f'out="$( (echo x); {LAUNCHER} - <<\'PY\''] + body
        ) == ([], 1), (
            "a subshell inside the substitution lost the launcher routing it")

        # The group\'s `)` must RESTORE the substitution, not merely fail to
        # close it. Pushed and never popped, the context is stuck on the group,
        # so the substitution\'s own `)` pops THAT, the closing `"` is read as
        # opening a quote rather than closing one, and everything after it —
        # here a whole second command — goes inert. Same silence, one command
        # later, and only a fixture whose substitution CLOSES can see it.
        closed = 'out="$( (echo x) )"; python3 - <<\'PY\''
        found, seen = self._heredoc_offenders(
            "synthetic.md", ["```sh", closed] + body)
        assert (seen, len(found)) == (1, 1), (
            f"the group swallowed the substitution\'s own `)`, so the command "
            f"after it was read as quoted text: {found}, {seen} seen")

        # And the flag itself, in both directions, since the two callers of
        # `_inert` read it with opposite polarity: the characters after the
        # group must stay ACTIVE, which is what `_commands` needs to split at
        # the `;` and what the mask needs to leave the `<<` alone.
        flags = _inert('out="$( (echo x); python3 - <<\'PY\'')
        assert not flags[16], (
            "the `;` after a nested group is still marked inert, so the "
            "substitution never reopened")

        # The SAME construct in its older spelling. A backtick inside a double
        # quote opens a command substitution exactly as `$(` does, and carrying
        # no syntax of its own here it let the quote reach over the `<<`: zero
        # heredocs seen and the body never read, which is the absence rather
        # than the verdict this whole scanner is built to avoid. The unquoted
        # form always worked, so only the quoted one shows the difference —
        # the same asymmetry the `$(` half of this test turns on.
        # ...and OFF THE FIRST TOKEN, which is the half `$(` needed a rule of
        # its own for and the backtick needed two. Its substitution carries no
        # bracket, so `_command_spans` never makes it a command and the head of
        # `echo "`python3 - <<\'PY\'`"` is `echo`, which runs nothing: the body
        # went unread whether or not the `<<` was masked. Putting a backtick in
        # SEPARATOR is the obvious repair and is measurably the wrong one — see
        # `_substitution_start`, which is where the entry is made instead.
        for opener in ('out="`python3 - <<\'PY\'',
                       'out="`(echo x); python3 - <<\'PY\'',
                       'out="prefix `python3 - <<\'PY\'',
                       'echo "`python3 - <<\'PY\'`"',
                       'echo `python3 - <<\'PY\'`',
                       # A separator INSIDE the substitution still decides, so
                       # entering one does not mean reading from its opener.
                       'out="`echo x; python3 - <<\'PY\''):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", opener] + body)
            assert (seen, len(found)) == (1, 1), (
                f"a backtick substitution inside a double quote left the "
                f"opener inert: {opener} -> {found}, {seen} seen")
        for routed in (f'out="`{LAUNCHER} - <<\'PY\'',
                       f'echo "`{LAUNCHER} - <<\'PY\'`"'):
            assert self._heredoc_offenders(
                "synthetic.md", ["```sh", routed] + body) == ([], 1), (
                f"the routed form inside a backtick substitution lost its "
                f"routing: {routed!r}")
        # The bound that keeps the entry out of `_command_spans`: a backtick
        # inside a quoted `-c` body is a substitution too, and splitting the
        # command there leaves the interpreter and the import in fragments that
        # name no engine. This offence is pinned by
        # `test_the_activation_audit_reads_the_command_not_the_line`; it is
        # restated here because it is the reason THIS rule is local.
        assert _unactivated(
            'python3 -c "\n# writes `result.json`\n'
            'from agents.lib.tie_out import run\n"', fenced=True), (
            "the backtick rule reached the engine-call pattern and split a "
            "single command into fragments that name no engine")
        # ...and the two places a backtick carries no syntax at all: escaped,
        # and inside a single quote. Both leave the opener inert, which is what
        # the shell does with them.
        for literal in ('out="\\`python3 - <<\'PY\'"',
                        "out='`python3 - <<\'PY\''"):
            assert self._heredoc_offenders(
                "synthetic.md", ["```sh", literal] + body)[1] == 0, (
                f"a backtick the shell reads as a character opened a "
                f"substitution: {literal!r}")

    def test_a_substitution_off_the_first_token_is_still_a_command(self):
        """`$( ... )` runs a command wherever it sits, and only a LEADING one
        was read as one.

        `echo "$(python3 - <<\'PY\')"` is the shape: the substitution is an
        ARGUMENT, so nothing in the first token names it. Its `$(` was marked
        inert with the double quote around it, `_commands` never split there,
        and the single command left has `echo` for a head — not an interpreter,
        so no heredoc was counted and the body was never read. A bare engine
        import inside it cleared the heredoc scan and the per-command audit at
        once: this file\'s recurring failure, in its worst direction, which is
        the clearance nobody sees.

        Unquoted, the same line has always split at the `(`, so the bug needed
        the quote — and the quoted spelling is the one a document writes, `"$(
        ... )"` being how a substitution survives word splitting. The rule that
        DID cover the leading form was deleted rather than widened, and
        `_command_head` carries the reason: widening it clears
        `python3 -m agents.lib.ptet "$(date)"`, which is asserted below.
        """
        body = ["import json",
                "from agents.lib.ptet import _salt_cap", "PY", "```"]
        for opener in ('echo "$(python3 - <<\'PY\')"',
                       'printf "%s" "$(python3 - <<\'PY\')"',
                       'echo "at $(date): $(python3 - <<\'PY\')"',
                       'msg="see $(python3 - <<\'PY\')"',
                       'echo "$(python3 - <<\'PY\')" >> log.txt',
                       '[ -n "$(python3 - <<\'PY\')" ] && echo ok'):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", opener] + body)
            assert seen == 1, (
                f"a substitution that is not the first token opened no heredoc, "
                f"so its body was never read: {opener}")
            assert len(found) == 1, (
                f"the body of an argument substitution went unscanned: "
                f"{opener} -> {found}")

        # Routing survives the position, so this is not "indict every argument".
        for routed in (f'echo "$({LAUNCHER} - <<\'PY\')"',
                       f'echo "at $(date): $({LAUNCHER} - <<\'PY\')"'):
            assert self._heredoc_offenders(
                "synthetic.md", ["```sh", routed] + body) == ([], 1), (
                f"the launcher inside an argument substitution lost its "
                f"routing: {routed}")

        # The repair NOT taken, pinned from the side that would have broken.
        # `_command_head` returns one position, so entering the substitution
        # would discard the command that OWNS it — and the engine call is on
        # that one. Both spellings, because the verdict must survive in each
        # direction: bare is reported, routed is not.
        for bare in ('python3 -m agents.lib.ptet "$(date)"',
                     'python3 agents/lib/tie_out.py "$(pwd)"'):
            assert _unactivated(bare), (
                f"an engine call carrying a substitution as an ARGUMENT is no "
                f"longer reported — the head was re-pointed into the "
                f"substitution and the command running the engine discarded: "
                f"{bare}")
            assert not _unactivated(bare.replace("python3", LAUNCHER, 1)), (
                f"the routed form of {bare} is indicted, so the audit now "
                "rejects the compliant spelling of the shape it just learned")

        # And the flag underneath all of it, stated as the DISCRIMINATION it is.
        # `$(` is shell syntax and is active inside a double quote; a literal
        # `(` in the same quote is text and stays inert. Marking every paren
        # active would split `echo "(literal)"` into pseudo-commands and merge
        # nothing back — the over-fix this pins the boundary against.
        text = 'echo "$(python3)" and "(literal)"'
        flags = _inert(text)
        assert not flags[text.index("$(") + 1], (
            "the `(` opening a substitution inside a double quote is still "
            "inert, so `_commands` cannot split at it")
        assert flags[text.index("(literal)")], (
            "a literal `(` inside a double quote was made active, so quoted "
            "prose is now split into commands")

    def test_a_prefix_the_shell_parses_away_is_not_the_command(self):
        """The two grammar positions this file still read as executables.

        `[assignments] [redirections] word...` is the shell\'s own shape for a
        simple command, and REDIRECTIONS had no rule at all: `2>/dev/null python3
        - <<\'PY\'` runs python, but read as a command word `2>/dev/null` is no
        interpreter, so no heredoc was counted and the body was never opened.
        `time` had a rule and it was one token short — `-p` is the only option
        bash\'s `time` takes, and stripping the word alone left `-p` standing
        where the executable goes. Both are the silent direction, and both are
        the same mistake EXEC_PREFIX\'s operand arm was written for: what stands
        before a command word is not always one token.

        The bounds are the other half, and they are about MARKDOWN rather than
        about sh. `> ` at the start of a line is a blockquote — 126 of them in
        this tree — and `>10 pages` is prose comparing numbers, so the target
        must be ATTACHED to the operator and shaped like a path, a variable or a
        descriptor. That is narrower than POSIX on purpose: `> file cmd` is
        legal shell no document writes, and reading a blockquote as a command is
        how a gate starts firing on documentation.

        `<<` is excluded by lookahead, and that one is not taste — it is the
        heredoc operator, and eating it here would consume the delimiter the
        scanner keys on.
        """
        body = ["import json",
                "from agents.lib.ptet import _salt_cap", "PY", "```"]
        openers = ("2>/dev/null python3 - <<\'PY\'",
                   "2>&1 python3 - <<\'PY\'",
                   ">out.txt python3 - <<\'PY\'",
                   ">>log.txt python3 - <<\'PY\'",
                   "</dev/null python3 - <<\'PY\'",
                   ">&2 python3 - <<\'PY\'",
                   "&>all.log python3 - <<\'PY\'",
                   "2>$LOG python3 - <<\'PY\'",
                   "PYTHONPATH=. 2>/dev/null python3 - <<\'PY\'",
                   "time -p python3 - <<\'PY\'",
                   "time python3 - <<\'PY\'")
        for opener in openers:
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", opener] + body)
            assert seen == 1, (
                f"a prefix the shell parses away was read as the executable, so "
                f"no heredoc was counted: {opener}")
            assert len(found) == 1, (
                f"the body behind a parsed-away prefix went unread: {opener} "
                f"-> {found}")

        # Routing survives every one of them, so this is not "indict anything
        # with a redirection on it".
        for opener in openers:
            routed = opener.replace("python3", LAUNCHER, 1)
            assert self._heredoc_offenders(
                "synthetic.md", ["```sh", routed] + body) == ([], 1), (
                f"the launcher behind a parsed-away prefix lost its routing: "
                f"{routed}")

        # The bound, stated as heads that must NOT move. Every one of these is
        # markdown, not shell, and entering it would hand the scanner a command
        # word out of the middle of a sentence.
        for prose in ("> python3 - <<\'PY\'",
                      "> Detailed history lives elsewhere",
                      ">10 pages of output",
                      "<19 if a FULL-TIME student",
                      "<!-- python3 - <<\'PY\' -->"):
            assert _command_head(prose) == prose, (
                f"a redirection prefix was read out of markdown: {prose!r} -> "
                f"{_command_head(prose)!r}")
        for lines in (["```sh", "> python3 - <<\'PY\'"] + body,
                      ["> python3 - <<\'PY\'"] + body):
            assert self._heredoc_offenders("synthetic.md", lines) == ([], 0), (
                "a blockquote quoting an opener was read as one")

        # `<<` and `<<<` are operators this rule must not touch: consuming a
        # leading one would take the delimiter the scanner keys on with it.
        for heredoc in ("<<\'PY\' cat", "<<<\'print(1)\' python3"):
            assert _command_head(heredoc) == heredoc, (
                f"the redirection prefix ate a heredoc operator: {heredoc!r}")

        # ...and an option with no command after it is not a command either.
        assert self._heredoc_offenders(
            "synthetic.md", ["```sh", "-p python3 - <<\'PY\'"] + body) == (
            [], 0), "a bare `-p` was read as a prefix rather than as a word"

    def test_a_heredoc_fed_to_something_that_is_not_python_is_not_an_opener(self):
        """The opener pattern's leading `.*` crosses separators, so the
        `python3` it matches can belong to a command that runs no heredoc.

        `echo python3 && cat <<'PY'` feeds its body to `cat`, which prints it.
        Every engine import in that body was reported as the engine running on a
        bare interpreter — a false accusation on a correct document, and the
        failure mode that teaches a reader to reason past a gate rather than
        read it. Writing python source out with `cat` or `tee` is an ordinary
        thing for a runbook to do, so this is not a contrived shape.

        It is the same per-command pairing `_unactivated` does, in the audit a
        previous round examined and cleared: a heredoc really does have exactly
        one owner, but the INTERPRETER is not part of what identifies it, so the
        two facts still floated free of each other here. Naming that, rather
        than the shape, is what stops the next audit inheriting it.
        """
        body = ["from agents.lib.ptet import _salt_cap", "PY", "```"]
        for cmd in ("echo python3 && cat <<'PY'",
                    "echo 'run python3 -' | tee note.txt; cat <<'PY'",
                    "python3 --version && tee /tmp/gen.py <<'PY'"):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", cmd] + body)
            assert (found, seen) == ([], 0), (
                f"a heredoc owned by a command that executes nothing was read "
                f"as a python invocation: {cmd} -> {found}, {seen} seen")

        # The bound, in the same shape: move the delimiter onto the interpreter
        # and every one of them is an opener again. Without this the fix above
        # would be indistinguishable from a scanner that stopped looking.
        for cmd in ("echo python3 && python3 - <<'PY'",
                    "cat note.txt | python3 - <<'PY'"):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", cmd] + body)
            assert seen == 1, f"stopped seeing a real opener: {cmd}"
            assert len(found) == 1, (
                f"an engine import fed to a bare interpreter escaped: {cmd} -> "
                f"{found}")

        # ...and the OTHER half of what identifies an opener, which is not who
        # runs it but whether python executes what it is handed. An interpreter
        # already carrying a program reads a heredoc as DATA — `-c` and `-m`
        # bring their own, and so does a script operand — so an engine import in
        # one of these bodies is text nothing runs, and every one of them was
        # reported. Same false accusation as the block above, arriving through
        # the interpreter rather than through the owner.
        for cmd in ("python3 -c 'import sys; print(sys.stdin.read())' <<'PY'",
                    "python3 -m json.tool <<'PY'",
                    "python3 tool.py <<'PY'",
                    "python3 -uc 'print(1)' <<'PY'",
                    "cat <<'PY' | python3 -c 'print(1)'",
                    f"{LAUNCHER}.bak -c 'print(1)' <<'PY'"):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", cmd] + body)
            assert (found, seen) == ([], 0), (
                f"a heredoc read as DATA by an interpreter that already has a "
                f"program was scanned as source: {cmd} -> {found}, {seen} seen")

        # The bound, in the same shape as the one above: every spelling in which
        # stdin IS the program. `-` says so, options-only leaves nothing else to
        # run, and an option that takes a separate VALUE must not be mistaken for
        # the script — `-W ignore -` runs stdin.
        for cmd in ("python3 - <<'PY'", "python3 <<'PY'", "python3 -u <<'PY'",
                    "python3 -W ignore - <<'PY'",
                    f"{LAUNCHER}.bak - <<'PY'"):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", cmd] + body)
            assert (seen, len(found)) == (1, 1), (
                f"stopped seeing an interpreter that really does execute its "
                f"stdin: {cmd} -> {found}, {seen} seen")

        # ...and the bound on the bound. A `-c` program may read stdin and EXEC
        # it, and where the literal is right there it is read: clearing on the
        # presence of `-c` alone would hand back the whole class as a one-word
        # bypass. `-m` and a script file are where that literal is somewhere no
        # scanner here can look, which `_interpreter_role` states as its bound.
        found, seen = self._heredoc_offenders(
            "synthetic.md",
            ["```sh", "python3 -c 'exec(sys.stdin.read())' <<'PY'"] + body)
        assert (seen, len(found)) == (1, 1), (
            f"a `-c` body that executes its own stdin cleared the heredoc it "
            f"executes: {found}, {seen} seen")

    def test_only_a_delimiter_alone_on_a_line_closes_the_body(self):
        """The terminator rule, in both directions.

        `.strip()` read `    PY` as the close of an ordinary heredoc. sh does
        not: the body runs on to the unindented delimiter, so an engine import
        between the two is INSIDE it and the scan ended one screen early with a
        clean verdict. Nothing about that is visible from outside — one heredoc
        seen, no offender, exactly what a correct document produces.

        A Python body reaching an indented `PY` is contrived; that is not the
        argument. The argument is direction. Every mistake this predicate can
        make in the strict spelling ends in a false accusation somebody reads,
        and every one it made in the loose spelling ended in a silent pass.
        """
        # The finding, verbatim: the import sits between a decoy terminator and
        # the real one, so it is body text and the heredoc is bare.
        escape = ["```sh", "python3 - <<'PY'", "print(1)", "    PY",
                  "from agents.lib.ptet import _salt_cap", "PY", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", escape)
        assert (seen, len(found)) == (1, 1), (
            f"an indented decoy terminator ended the body early and the engine "
            f"import below it escaped: {found}, {seen} seen")

        # sh does not close on `PY ` either. Same escape, one invisible byte.
        trailing = ["```sh", "python3 - <<'PY'", "PY ",
                    "from agents.lib.ptet import _salt_cap", "PY", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", trailing)
        assert (seen, len(found)) == (1, 1), (
            f"a trailing space closed a heredoc that sh leaves open: {found}")

        # `<<-` is the one relaxation and it strips TABS. The import here is
        # genuinely outside the body, so indicting it would be the opposite
        # error — the false accusation that keeps the strict rule honest.
        # ...and the import below the terminator is ordinary document text once
        # the body has closed, which is what makes this case discriminating: a
        # terminator that failed to close would indict that line instead.
        dashed = ["```sh", "\tpython3 - <<-'PY'", "\tprint(1)", "\tPY",
                  "\tfrom agents.lib.ptet import _salt_cap", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", dashed)
        assert (seen, len(found)) == (1, 0), (
            f"a tab-indented terminator failed to close a `<<-` heredoc: {found}")

        # ...tabs, and not spaces. `<<-` leaves a space-indented line open too.
        spaced = ["```sh", "python3 - <<-'PY'", "    PY",
                  "from agents.lib.ptet import _salt_cap", "PY", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", spaced)
        assert (seen, len(found)) == (1, 1), (
            f"`<<-` closed on a SPACE-indented terminator, which strips tabs "
            f"only: {found}")

        # The routed form is unaffected by all of it: what the terminator
        # decides is where the body ENDS, never who runs it. Spelled with the
        # interpreter named, because the plain launcher form is not an opener at
        # all and would assert nothing about a body it never scans.
        routed = ["```sh", f"TAXPY=python3 {LAUNCHER} - <<'PY'", "    PY",
                  "from agents.lib.ptet import _salt_cap", "PY", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", routed)
        assert (seen, len(found)) == (1, 0), (
            f"the launcher stopped routing a body once the scan read further "
            f"into it: {found}")

    def test_a_line_continued_opener_is_one_command(self):
        """A `\\` at the end of a line is not a boundary, and reading it as one
        loses the opener in both directions.

        Split BEFORE the delimiter, no line holds an interpreter and a `<<` at
        once, so the scan finds no opener, counts none, and never opens the body
        — an engine call inside it passes with nothing reported and nothing seen.
        That is the silent clearance every other case in this class exists to
        rule out, arriving through the only shell construct that lets one command
        occupy two lines.

        Split AFTER it, the opener was found but the body was read from the
        continuation line, so it began one line early. Harmless while the
        continued tail is a redirection, and the join makes it exact rather than
        harmless-for-now.

        `_shell_units` already joins continuations for the activation audit. Two
        scanners over the same corpus disagreeing about where a command ends is
        the drift this pins shut.
        """
        engine = "from agents.lib.ptet import _salt_cap"

        before = ["```sh", "python3 - \\", "<<'PY'", engine, "PY", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", before)
        assert seen == 1, "an opener continued before its delimiter was not seen"
        assert len(found) == 1 and ":4:" in found[0], (
            f"the body of a continued opener went unread: {found}")

        # The continued tail is written to MATCH the body scan, which is what
        # makes the off-by-one visible: read as the first body line it is an
        # engine import inside an unrouted heredoc, and read as what it is — the
        # rest of the command — it is a pipeline the scan never sees.
        after = ["```sh", "python3 - <<'PY' \\", "2>&1 | grep 'from agents.lib'",
                 engine, "PY", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", after)
        assert (seen, len(found)) == (1, 1) and ":4:" in found[0], (
            f"the body began at the continued pipeline rather than after it: "
            f"{found}")

        # The join changes where a command ENDS, never who runs it: the two
        # verdicts a scanned opener carries both survive it.
        routed = ["```sh", "TAXPY=python3 \\", f"{LAUNCHER} - <<'PY'",
                  engine, "PY", "```"]
        assert self._heredoc_offenders("synthetic.md", routed) == ([], 1), (
            "joining the continuation lost the launcher that routes the body")

        not_python = ["```sh", "echo python3 && \\", "cat <<'PY'",
                      engine, "PY", "```"]
        assert self._heredoc_offenders("synthetic.md", not_python) == ([], 0), (
            "the joined line was read as an opener though `cat` owns the "
            "delimiter and executes nothing")

        # A continuation stops at the fence, and the cost of letting it through
        # is a false accusation rather than a missed one. Joined across the
        # closing fence, this opener's body would start at the line AFTER it —
        # ordinary document text, outside any block, with the fence that bounds
        # a malformed body already spent. The block is malformed either way; one
        # reading indicts the paragraph below it and the other stops.
        escaping = ["```sh", "python3 - <<'PY' \\", "```", engine]
        assert self._heredoc_offenders("synthetic.md", escaping) == ([], 1), (
            "a continuation ran through the fence that closes its block and "
            "read the prose below as the heredoc's body")

    def test_a_continued_import_in_the_body_is_read_as_one_statement(self):
        """The join above is the SHELL's; this is python's, in the body, and it
        was the scanner's last unjoined seam.

        `from agents.\\` / `lib import ptet` is one import statement: explicit
        line joining deletes the backslash and the newline before the line is
        tokenised, so the interpreter executes exactly the engine call the
        offending form executes. Read one physical line at a time, neither half
        names the package, nothing matches, and the scan reports a heredoc seen
        and clean — the silent pass, inside the audit written against silent
        passes, reached by pressing return.

        Parenthesised continuations need no join and are pinned here as the
        boundary of the claim: `from agents.lib import (` already carries the
        whole of what the pattern asks for on its first line.
        """
        split = ["```sh", "python3 - <<'PY'", "from agents.\\",
                 "lib import ptet", "PY", "```"]
        assert not any(re.search(ENGINE_IMPORT, line) for line in split), (
            "this fixture matches line by line, so it tests no join")
        found, seen = self._heredoc_offenders("synthetic.md", split)
        assert seen == 1 and len(found) == 1, (
            f"a continued engine import in the body went unread: {found}")
        # The FIRST physical line of the statement, which is where a reader
        # looks, and the joined text rather than the fragment that matched.
        assert ":3:" in found[0] and "from agents.lib import ptet" in found[0], (
            f"the offence is reported against the continuation rather than the "
            f"statement: {found[0]}")

        # Python DELETES the backslash and the newline and inserts nothing, so
        # the indent of the continued line lands against the dot — the ordinary
        # way to write this, and the reason the import pattern tolerates
        # whitespace around a dot at all.
        indented = ["```sh", "python3 - <<'PY'", "from agents.\\",
                    "    lib import ptet", "PY", "```"]
        assert len(self._heredoc_offenders("synthetic.md", indented)[0]) == 1, (
            "an INDENTED continuation joined to `from agents.    lib` and the "
            "import pattern read the gap as prose")

        paren = ["```sh", "python3 - <<'PY'", "from agents.lib import (",
                 "    ptet,", ")", "PY", "```"]
        assert len(self._heredoc_offenders("synthetic.md", paren)[0]) == 1, (
            "a parenthesised engine import went unread")

        # Who runs the body is not what the join decides: the same statement
        # under the launcher stays cleared.
        routed = ["```sh", f"{LAUNCHER} - <<'PY'", "from agents.\\",
                  "lib import ptet", "PY", "```"]
        assert self._heredoc_offenders("synthetic.md", routed) == ([], 1), (
            "joining the body lost the launcher that routes it")

        # ...and the two bounds, both of which end the body rather than the
        # statement. A trailing backslash on the LAST body line continues
        # nothing: swallowing the terminator would run the scan to EOF and read
        # the document below as heredoc body, and swallowing the closing fence
        # would do the same to the prose after a malformed block. Neither
        # fixture holds an engine import, so a join that overran either bound
        # would have to invent one out of the text it reached.
        at_terminator = ["```sh", "python3 - <<'PY'", "from agents.\\",
                         "PY", "from agents.lib import ptet", "```"]
        assert self._heredoc_offenders("synthetic.md", at_terminator) == ([], 1), (
            "a continuation on the last body line swallowed its terminator and "
            "read the document below as body")
        at_fence = ["```sh", "python3 - <<'PY'", "from agents.\\", "```",
                    "lib import ptet"]
        assert self._heredoc_offenders("synthetic.md", at_fence) == ([], 1), (
            "a continuation on the last line of a malformed block ran through "
            "the fence and joined the prose below it")

    def test_a_body_is_read_as_python_and_not_as_text(self):
        """The body scan reports what the INTERPRETER runs, and it was reading
        the body as a flat string.

        `# from agents.lib import ptet` executes nothing. Neither does
        `print("from agents.lib import ptet")`. Both were offenders — the
        shell-side `echo \'python3 - <<PY\'` case one language over, and the
        gate firing on a document that quotes the rule accurately, which is how
        a reader learns to reason past a gate rather than read it.

        TOKENIZED, not pattern-matched. A `#` inside a literal, a literal
        spanning three lines and an f-string are each a place a regex gets
        wrong, and python\'s own lexer is in the standard library.

        Two bounds, and they are what keep the fix from being a hole. A body
        that CALLS `exec`/`eval`/`compile` has its strings read raw, because
        there a quoted import really is source. And a body the tokenizer refuses
        — an unterminated literal, a fragment standing in for code — is read raw
        as well, which is exactly the behaviour that shipped, so nothing that
        was covered stops being covered.
        """
        def scan(body, opener="python3 - <<\'PY\'"):
            return self._heredoc_offenders(
                "synthetic.md", ["```sh", opener] + body + ["PY", "```"])

        for label, body in (
                ("a comment", ["# from agents.lib import ptet"]),
                ("a trailing comment",
                 ["x = 1  # from agents.lib import ptet"]),
                ("a string", ['print("from agents.lib import ptet")']),
                ("an f-string", ['print(f"from agents.lib import ptet")']),
                ("a docstring",
                 ['"""Never write: from agents.lib import ptet"""']),
                ("a triple-quoted block",
                 ['MSG = """', "from agents.lib import ptet", '"""'])):
            assert scan(body) == ([], 1), (
                f"{label} was reported as the engine running on a bare "
                f"interpreter: {scan(body)}")

        # ...while the statement itself still is one, comment and all: the mask
        # removes the comment, never the code beside it.
        for body in (["from agents.lib import ptet"],
                     ["from agents.lib import ptet  # noqa: E402"],
                     ["from agents.\\", "lib import ptet"],
                     ['print("x") \\', "from agents.lib import ptet"]):
            found, seen = scan(body)
            assert (seen, len(found)) == (1, 1), (
                f"a real engine import stopped being reported: {body} -> "
                f"{found}, {seen} seen")

        # The bound on the string half. `exec` and its two siblings turn a
        # literal into source, so where one is called the strings are read as
        # written — blanking them there would clear a bare engine call wearing
        # quotes, which is the silent direction this whole scanner exists for.
        for body in (['exec("from agents.lib import ptet")'],
                     ['eval(compile("from agents.lib import ptet", "x", "exec"))']):
            found, seen = scan(body)
            assert (seen, len(found)) == (1, 1), (
                f"a dynamically executed engine import was masked away: "
                f"{body} -> {found}")

        # The bound on the tokenizer. A body it cannot read is read RAW, which
        # is what shipped before this — so a template, a fragment or a broken
        # literal loses no coverage it had.
        for body in (['print("oops', "from agents.lib import ptet"],
                     ["...", "from agents.lib import ptet"]):
            found, seen = scan(body)
            assert (seen, len(found)) == (1, 1), (
                f"a body the tokenizer refused was cleared rather than read as "
                f"it stands: {body} -> {found}")

        # And routing still ends the question before any of this is asked.
        assert scan(["from agents.lib import ptet"],
                    f"{LAUNCHER} - <<\'PY\'") == ([], 1), (
            "the routed form is indicted, so the body scan now rejects the "
            "compliant spelling")

    def test_only_a_shell_active_redirection_opens_a_heredoc(self):
        """A `<<` the shell reads as text opens nothing, and the last one it
        reads as a redirection is the one python gets.

        The two are one rule — which `<<` is real — and they fail in opposite
        directions, which is why they are pinned together. Quoted, the scan
        indicts a block that runs no python at all: three `echo` lines printing an
        example is a correct document, and a gate that fires on one teaches a
        reader to reason past it. Unquoted and doubled, `python3 - <<A <<B` is
        valid and both are stdin redirections; the LAST wins, so keyed on the
        first the scan walks A's body and reads B's — the body python actually
        receives — as ordinary text below a closed heredoc. Silent.

        The two fixes are also each other's bound. Taking the last `<<` is only
        safe once the quoted ones are gone: on `python3 - <<B && echo 'x <<A'`
        the last `<<` in the raw line is inside a string, and reaching for it
        hands the heredoc to `echo` and loses B entirely — a silent pass
        manufactured by the fix for a silent pass.
        """
        engine = "from agents.lib.ptet import _salt_cap"

        echoed = ["```sh", "echo 'python3 - <<PY'", f"echo '{engine}'",
                  "echo PY", "```"]
        assert self._heredoc_offenders("synthetic.md", echoed) == ([], 0), (
            "a block that only prints an example was read as one that runs it")

        quoted_py = ["```sh", "echo 'python3' - <<PY", engine, "PY", "```"]
        assert self._heredoc_offenders("synthetic.md", quoted_py) == ([], 0), (
            "an interpreter named inside a string was read as one being run")

        doubled = ["```sh", "python3 - <<A <<B", "print(1)", "A",
                   engine, "B", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", doubled)
        assert seen == 1 and len(found) == 1 and ":5:" in found[0], (
            f"the body of the LAST heredoc — the one python is fed — went "
            f"unread: {found}, {seen} seen")

        masked = ["```sh", "python3 - <<B && echo 'x <<A'", engine, "B", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", masked)
        assert seen == 1 and len(found) == 1, (
            f"a quoted `<<` later on the line took the heredoc away from the "
            f"interpreter that owns it: {found}, {seen} seen")

        # The third place a `<<` is text rather than a redirection, and the one
        # that survived the two above: a COMMENT. `# ignore <<pass` is a
        # sentence, so the shell feeds PY and python runs the import — while the
        # greedy reach for the last `<<` took `pass` as the terminator, found it
        # on the very next line, closed the body before the import and reported
        # the whole thing clean. Quoting was masked and commenting was not, and
        # the rule they belong to is one rule.
        commented = ["```sh", "python3 - <<'PY' # ignore <<pass", "pass",
                     engine, "PY", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", commented)
        assert seen == 1 and len(found) == 1 and ":4:" in found[0], (
            f"a `<<` inside a shell comment was read as the redirection that "
            f"opens the body: {found}, {seen} seen")

        # ...and its bound, which is what keeps the masking from becoming the
        # next silent pass. A `#` is only a comment at the start of a WORD and
        # only outside quotes; masking either of these would eat the delimiter
        # python is actually fed and lose the body the same way.
        # The quoted case must put the `#` at a WORD START, or it is only the
        # other bound restated: `"#"` is rejected for its position whether or
        # not quoting is consulted, so the fixture passed with the quote guard
        # deleted. A control has to fail for the reason it names.
        for tail in ("a#b <<B", "' # ' <<B"):
            inner = ["```sh", f"python3 - <<A {tail}", "print(1)", "A",
                     engine, "B", "```"]
            found, seen = self._heredoc_offenders("synthetic.md", inner)
            assert seen == 1 and len(found) == 1 and ":5:" in found[0], (
                f"a `#` that opens no comment was treated as one, losing the "
                f"real delimiter: {tail!r} -> {found}, {seen} seen")

        # And the helper's own bound, stated directly. The scanner hands it a
        # line that has already been joined, so it holds no newline and the
        # scanner cannot reach this branch at all — a contract exercised in part
        # is one the next caller inherits wrong, and "comment" means to the end
        # of a LINE, not of the text.
        assert _blank_comments("python3 - # <<A\npython3 - <<B",
                               only="<") == "python3 - # \0\0A\npython3 - <<B", (
            "a comment was masked past the newline that ends it")

    def test_a_here_string_opens_no_body(self):
        """`<<<` is three characters and the opener pattern only ever counted
        two of them.

        A here-string has no body: its whole input is the word on that same
        line. Unguarded, the greedy run before `<<` simply backtracks one
        character onto the last two `<`, so `python3 - <<<\'print(1)\'` was read
        as a heredoc terminated by `print` — a terminator no line equals, so the
        scan ran on past it and read every quoted example below as executed
        python. That is the cry-wolf direction: a gate firing on correct shell,
        which teaches a reader to reason past the gate.

        A lookBEHIND is what it takes, and only that. The leading pair of the
        three is unreachable already — the class after `<<` admits a dash,
        whitespace, a quote or a terminator\'s first character, and `<` is none
        of them — so the pair the backtrack lands on is the TRAILING one, and a
        preceding `<` is what rejects it. The matching lookahead written here
        first killed no mutant, being unfalsifiable by construction, and is
        gone: a guard that cannot fail is indistinguishable from one that is
        not there.

        Excluding rather than parsing is only safe because the here-string keeps
        its code on the COMMAND, where `_unactivated` — the per-command audit
        this scanner supplements — already reads it. That is asserted, not
        assumed: without it this test would be pinning a hole.
        """
        body = ["import json",
                "from agents.lib.ptet import _salt_cap", "PY", "```"]
        for line in ("python3 - <<<\'print(1)\'", 'python3 - <<<"$code"',
                     "python3 <<< \'print(1)\'",
                     f"{LAUNCHER} - <<<\'print(1)\'",
                     "jq -c \'map(.id)\' <<<\"$PLAN\" && python3 -c \'print(1)\'"):
            assert self._heredoc_offenders(
                "synthetic.md", ["```sh", line] + body) == ([], 0), (
                f"a here-string was read as opening a heredoc body: {line}")

        # The audit that DOES own the form, so the exclusion above closes
        # nothing. An engine import inside the here-string is on the command,
        # and the per-command audit reports it.
        for line in ("python3 - <<<\'from agents.lib import ptet\'",
                     'python3 - <<<"from agents.lib.ptet import _salt_cap"'):
            assert _unactivated(line), (
                f"a here-string feeding the engine to a bare interpreter is "
                f"reported by neither audit: {line}")
        assert not _unactivated(
            f"{LAUNCHER} - <<<\'from agents.lib import ptet\'"), (
            "the routed here-string is indicted, so the pair of audits now "
            "rejects the compliant spelling of the form this one excludes")

        # ...and the two spellings that ARE heredocs stay ones. `<<-` is the
        # tab-stripping form and is one `<` away from the class just excluded.
        for opener in ("python3 - <<\'PY\'", "python3 - <<PY", "python3 - << PY"):
            assert self._heredoc_offenders(
                "synthetic.md", ["```sh", opener] + body) == (
                [f"synthetic.md:4: heredoc opened at line 2 reaches the "
                 f"engine: from agents.lib.ptet import _salt_cap"], 1), (
                f"the here-string guard took a real heredoc with it: {opener}")
        found, seen = self._heredoc_offenders(
            "synthetic.md", ["```sh", "python3 - <<-PY", "import json",
                             "\tfrom agents.lib.ptet import _salt_cap", "\tPY",
                             "```"])
        assert (seen, len(found)) == (1, 1), (
            f"`<<-` stopped opening a heredoc: {found}, {seen} seen")

    def test_prose_quoting_an_opener_is_not_an_opener(self):
        """The bound on the widening above, and the reason its tail is a closed
        class. These sentences MENTION the form; they open nothing. Read as
        openers they would each start a scan for a terminator that never comes,
        run to the end of the file, and report every engine import below them —
        turning a documentation paragraph into a permanent red test."""
        for prose in ["run `python3 - <<'PY'` to feed the engine",
                      "the opener is `python3 - <<'PY'`.",
                      "see python3 - <<'PY' above"]:
            lines = [prose, "from agents.lib.ptet import _salt_cap"]
            found, seen = self._heredoc_offenders("synthetic.md", lines)
            assert (found, seen) == ([], 0), (
                f"prose was read as a heredoc opener: {prose!r} -> {found}")

    def test_a_fenced_block_admits_arguments_that_prose_may_not(self):
        """The boundary that separates a command from a mention of one.

        `python3 - <<'PY' input.json` is valid: the heredoc feeds stdin and
        `input.json` lands in `sys.argv`. So is `--flag`. Requiring the tail to
        LEAD with an operator missed both, and simply allowing a bare word cannot
        be the fix — "see python3 - <<'PY' above" is the same text, and admitting
        it makes every sentence quoting an opener into one whose terminator never
        arrives. Neither the tail nor the words can tell the two apart. The FENCE
        can, so the fence is what decides: inside one, any argument; outside one,
        an operator or nothing. This test states both halves against the same
        line, which is the only way to show the discrimination is real.
        """
        cmd = "python3 - <<'PY' input.json"
        body = ["import json",
                "from agents.lib.ptet import _salt_cap", "PY"]

        found, seen = self._heredoc_offenders(
            "synthetic.md", ["```sh", cmd] + body + ["```"])
        assert seen == 1, "a fenced opener carrying an argument was not seen"
        assert len(found) == 1, (
            f"the fenced heredoc's body went unread: {found}")

        found, seen = self._heredoc_offenders("synthetic.md", [cmd] + body)
        assert (found, seen) == ([], 0), (
            f"the same text OUTSIDE a fence is indistinguishable from prose and "
            f"must not open a scan: {found}, {seen} seen")

    def test_an_unterminated_fenced_heredoc_stops_at_the_fence(self):
        """A malformed block is not a licence to read the rest of the file.

        Without the terminator the body scan ran to EOF, so every engine import
        anywhere below a typo'd fence was reported against it — a false positive
        that grows with the document and points at the wrong line.
        """
        lines = ["```sh", "python3 - <<'PY'", "import json", "```",
                 "", "Prose below, and a legitimate mention:",
                 "```python", "from agents.lib.ptet import _salt_cap", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", lines)
        assert seen == 1, "the opener itself should still register"
        assert not found, (
            f"the scan escaped its own fenced block and indicted a later one: "
            f"{found}")

    def test_a_wrapper_that_execs_its_argument_opens_the_heredoc(self):
        """`sudo`, `timeout`, `nohup` and `nice` fill the position `env` fills
        and were not in any list.

        Each execs the rest of its argv after its own options, so `sudo python3 -
        <<'PY'` runs python — and read as a command word `sudo` is neither an
        interpreter nor the launcher, so the scanner counted zero heredocs and
        never opened the body. That is EXEC_PREFIX's silent pass in the
        vocabulary a runbook actually writes: the command that has to be run as
        root, under a time limit, or detached.
        """
        body = ["from agents.lib.ptet import _salt_cap", "PY", "```"]
        # The venv RUNNERS are the same position filled by a subcommand, and
        # the vocabulary that replaced `sudo` in a modern runbook: `uv run
        # python3 - <<'PY'` reads as a command word `uv`, which is neither an
        # interpreter nor the launcher, so zero heredocs were counted and the
        # body went unread. Each execs the rest of its argv inside the project
        # environment and passes stdin through, which is the whole of what
        # admits it beside the four above.
        for prefix in ("sudo ", "sudo -u tax ", "sudo -i ", "nohup ",
                       "nice -n 5 ", "nice -5 ", "timeout 60 ", "timeout 1.5s ",
                       "timeout -k 5 60 ", "/usr/bin/timeout 60 ", "stdbuf -o0 ",
                       "setsid ", "ionice -c 3 ", "uv run ", "pipenv run ",
                       "poetry run ", "pdm run ", "hatch run ", "rye run ",
                       "uv run --locked ", "uv run --python=3.12 ",
                       "/usr/local/bin/uv run "):
            wrapped = ["```sh", f"{prefix}python3 - <<'PY'"] + body
            found, seen = self._heredoc_offenders("synthetic.md", wrapped)
            assert (seen, len(found)) == (1, 1), (
                f"`{prefix}python3` was not read as running its heredoc: "
                f"{found}, {seen} seen")

            routed = ["```sh", f"{prefix}{LAUNCHER} - <<'PY'"] + body
            assert self._heredoc_offenders("synthetic.md", routed) == ([], 1), (
                f"`{prefix}{LAUNCHER}` is the routed form and must be seen and "
                "cleared")

        # Stated as a POSITION as well, because through the scanner the two ways
        # an option list gets this wrong reach the same COUNT and both are
        # silent: consume an operand that is not one and the interpreter itself
        # is swallowed, leave a real operand standing and the head is `tax`.
        # Neither is an interpreter, and a head that is not one CLEARS the
        # command. That collision is why the words are spelled out one at a time
        # rather than sharing an option class — `sudo -i` takes no operand while
        # `stdbuf -i MODE` does, and `timeout -s SIG` does while `sudo -s` does
        # not.
        for command in ("sudo -u tax python3 -m agents.lib.ptet",
                        "sudo -i python3 -m agents.lib.ptet",
                        "stdbuf -o0 python3 -m agents.lib.ptet",
                        "timeout 60 python3 -m agents.lib.ptet",
                        "timeout -k 5 60 python3 -m agents.lib.ptet",
                        "nice -n 5 python3 -m agents.lib.ptet",
                        "ionice -c 3 python3 -m agents.lib.ptet",
                        "uv run python3 -m agents.lib.ptet",
                        "poetry run python3 -m agents.lib.ptet",
                        "uv run --locked -q python3 -m agents.lib.ptet"):
            assert _command_head(command) == "python3 -m agents.lib.ptet", (
                f"the unwrapping stopped short of, or ran past, the word the "
                f"shell runs: {command!r} -> {_command_head(command)!r}")

        # ...and the bound that makes the list closed rather than "wrappers".
        # `xargs` runs its argument too and stays out, for the reason it is not
        # an exec word either: it CONSUMES stdin to build argv, so the
        # interpreter never sees the body.
        assert _command_head("xargs -0 python3") == "xargs -0 python3", (
            "`xargs` was unwrapped as if it handed its stdin to the interpreter")
        # ...and `conda run` with it, for the same property read one step later:
        # it CAPTURES the child's streams by default, so the body the shell
        # wrote is not the body the interpreter reads.
        assert _command_head("conda run -n tax python3") == (
            "conda run -n tax python3"), (
            "`conda run` was unwrapped though it does not hand its stdin on")

        # The runners' option arm is GENERIC where the POSIX members' are
        # enumerated — a third-party CLI's option set moves, so an enumeration
        # is a guess that goes wrong silently the next time uv adds a flag. What
        # makes the generic form safe is that it can only stop EARLY: an operand
        # left standing is a head that is not an interpreter, which is the
        # silence there was before. The lookahead is what stops it running LONG.
        # An option word that NAMES an interpreter is left where
        # `BARE_ENGINE_CALL` still reads it, so the widening cannot turn a
        # report into a clear; a bare VERSION names none, and consuming it is
        # what keeps the routed command behind it reading as routed.
        assert _unactivated("uv run --python=python3.12 -m agents.lib.ptet"), (
            "the option word carrying the interpreter was consumed, and the "
            "engine call behind it cleared")
        assert _command_head(
            f"uv run --python=3.12 {LAUNCHER} -m agents.lib.ptet") == (
            f"{LAUNCHER} -m agents.lib.ptet")
        # The bound, named so it is not mistaken for coverage: `uv run
        # agents/lib/ptet.py` hands a runner an engine module with no
        # interpreter word anywhere on the command. That is execution by PATH —
        # the shape `./agents/lib/ptet.py` shares — and an audit built on
        # "interpreter … target" does not see it. Left named rather than
        # half-closed, because a rule that fires on a bare engine path fires on
        # every engine path in prose, and the documents are full of them.

    def test_a_heredoc_piped_into_an_interpreter_is_an_opener(self):
        """`cat <<'PY' | python3 -` is the canonical way to write a
        here-document into an interpreter, and it opened nothing at all.

        The delimiter's owner is `cat`, which executes none of the body — the
        fact that correctly clears `echo python3 && cat <<'PY'` — but a
        pass-through hands its stdin to the next stage, so the engine really does
        reach a bare interpreter. Zero heredocs seen and the body never read: the
        silent direction, arriving through the fix for a cry-wolf one, on the
        spelling that fix was most likely to hide.
        """
        body = ["from agents.lib.ptet import _salt_cap", "PY", "```"]
        for cmd in ("cat <<'PY' | python3 -",
                    "cat <<'PY' | python3",
                    "cat - <<'PY' | python3 -",
                    "tee /tmp/gen.py <<'PY' | python3 -",
                    "cat <<'PY' | tee /tmp/copy | python3 -",
                    # `|&` is bash's pipe-with-stderr and is ONE operator. Split
                    # at the `|`, the next command's executable is `&`, which is
                    # not an interpreter — so the pipe was followed to a head
                    # that clears every body it is handed.
                    "cat <<'PY' |& python3 -",
                    # ...and a redirection the pass-through test used to cut at.
                    # A descriptor belongs to the OPERATOR, and cutting at the
                    # angle bracket left `cat 2` — a command that copies nothing.
                    "cat 2>/dev/null <<'PY' | python3 -",
                    "cat <<'PY' 2>/dev/null | python3 -"):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", cmd] + body)
            assert (seen, len(found)) == (1, 1), (
                f"a heredoc piped into a bare interpreter was not read as one: "
                f"{cmd} -> {found}, {seen} seen")

        assert self._heredoc_offenders(
            "synthetic.md", ["```sh", f"cat <<'PY' | {LAUNCHER} -"] + body
        ) == ([], 1), "the launcher stopped routing a piped body"

        # The four bounds, each removing one thing the claim rests on: a
        # pass-through with a FILE operand reads the file and ignores stdin;
        # `xargs` consumes the body to build argv; `&&` is not a pipe; and a
        # filter that TRANSFORMS forwards something the body does not say, so
        # carrying the verdict through it would be a guess in the accusing
        # direction.
        for cmd in ("cat notes.txt <<'PY' | python3 -",
                    "cat <<'PY' | xargs python3",
                    "cat <<'PY' && python3 -",
                    "grep import <<'PY' | python3 -"):
            assert self._heredoc_offenders(
                "synthetic.md", ["```sh", cmd] + body) == ([], 0), (
                f"a body that reaches no interpreter was read as one that does: "
                f"{cmd}")

    def test_every_heredoc_on_a_line_is_read_against_its_own_command(self):
        """`python3 - <<X && cat <<Y` was read as ONE heredoc, and the wrong one.

        The scanner took the LAST `<<` on the line, asked `cat` whether it runs
        an interpreter, answered no and stopped — so X, the body python actually
        executes, was neither counted nor scanned. The comment recording that
        bound stated the opposite, that Y is read, which is how a silent pass
        survives being written down and cited.
        """
        engine = "from agents.lib.ptet import _salt_cap"

        pair = ["```sh", "python3 - <<X && cat <<Y", engine, "X",
                "ordinary data", "Y", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", pair)
        assert (seen, len(found)) == (1, 1) and ":3:" in found[0], (
            f"the first heredoc, the one python is fed, went unread: "
            f"{found}, {seen} seen")

        # Bodies arrive in DELIMITER order, so reading them the other way round
        # scores each against the wrong command. Same two commands, swapped.
        swapped = ["```sh", "cat <<X && python3 - <<Y", "ordinary data", "X",
                   engine, "Y", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", swapped)
        assert (seen, len(found)) == (1, 1) and ":5:" in found[0], (
            f"the bodies were matched to the delimiters out of order: "
            f"{found}, {seen} seen")

        both = ["```sh", "python3 - <<X && python3 - <<Y", engine, "X",
                engine, "Y", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", both)
        assert (seen, len(found)) == (2, 2), (
            f"two commands with one heredoc each were read as one: "
            f"{found}, {seen} seen")

        # ...against `<<A <<B` on ONE command, where only the last supplies
        # stdin: sh reads A's text and throws it away, so scanning it would
        # report an import nothing executes. Its lines are still WALKED, or every
        # line number after it is out by the length of a body.
        superseded = ["```sh", "python3 - <<A <<B", engine, "A",
                      "print(1)", "B", "```"]
        assert self._heredoc_offenders("synthetic.md", superseded) == ([], 1), (
            "a superseded heredoc body was read as code python executes")

        # ...and the DESCRIPTOR, which is what "one stdin per command" turns on.
        # `3<<DATA` feeds fd 3 and supersedes nothing: python still executes PY.
        # Keyed on the command alone, the later heredoc won whatever it
        # redirected — so the body that runs was walked and a data channel was
        # scanned in its place.
        fd = ["```sh", "python3 - <<PY 3<<DATA", engine, "PY",
              "ordinary data", "DATA", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", fd)
        assert (seen, len(found)) == (1, 1) and ":3:" in found[0], (
            f"the body python is fed was skipped for one on another "
            f"descriptor: {found}, {seen} seen")

        # ...and the other direction: a body on a descriptor no interpreter
        # reads is not an offence, so it is walked and never reported.
        quiet = ["```sh", "python3 - <<PY 3<<DATA", "print(1)", "PY",
                 engine, "DATA", "```"]
        assert self._heredoc_offenders("synthetic.md", quiet) == ([], 1), (
            "a heredoc on fd 3 was read as code the interpreter executes")

        # A descriptor is a WORD, and any digit in front of `<<` was read as
        # one. `python3<<'PY'` attaches the heredoc to the command word, and the
        # `3` of `python3` became fd 3 — walked, never scanned, and the ordinary
        # per-command audit cannot see it either because the import is on a body
        # line.
        for attached in ("python3<<'PY'", "python3<<PY", "python3 -<<'PY'"):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", attached, engine, "PY", "```"])
            assert (seen, len(found)) == (1, 1), (
                f"an attached heredoc was read as feeding a descriptor nothing "
                f"executes: {attached!r} -> {found}, {seen} seen")

        # ...and the LAST redirection to stdin wins whether or not it is a
        # heredoc. `</dev/null` after the opener runs python on an empty stdin,
        # so the body is never executed and reporting it accuses text nothing
        # runs.
        for taken in ("python3 - <<'PY' </dev/null", "python3 - <<'PY' 0<in.txt"):
            assert self._heredoc_offenders(
                "synthetic.md", ["```sh", taken, engine, "PY", "```"]
            ) == ([], 0), (
                f"a heredoc stdin later redirected away was still read as the "
                f"interpreter's input: {taken!r}")

        # ...and the three things that are NOT a stdin redirection, each of
        # which would silence a real body if it were read as one.
        for kept in ("python3 - <<'PY' 2>&1", "python3 - <<'PY' >out.log",
                     "python3 - <<'PY' <(date)"):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", kept, engine, "PY", "```"])
            assert (seen, len(found)) == (1, 1), (
                f"a redirection that does not take stdin silenced the body: "
                f"{kept!r} -> {found}, {seen} seen")

        # A body its owner never executes is still a BODY. Skipping by one line
        # left the shell's own text to be parsed as markup: the `python3 <<'PY'`
        # example quoted inside `cat <<'DOC'` was read as a live opener, and the
        # document quoting the rule correctly was the one indicted.
        nested = ["```sh", "cat <<'DOC'", "python3 - <<'PY'", engine, "PY",
                  "DOC", "```"]
        assert self._heredoc_offenders("synthetic.md", nested) == ([], 0), (
            "a heredoc example quoted inside a non-executing heredoc was read "
            "as a command")

        # ...and the bound on that walk: the outer body ENDS at its terminator,
        # so a real opener below it is still read.
        after = ["```sh", "cat <<'DOC'", "python3 - <<'PY'", "PY", "DOC",
                 "python3 - <<'PY'", engine, "PY", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", after)
        assert (seen, len(found)) == (1, 1) and ":7:" in found[0], (
            f"walking a quoted body ran past its terminator: {found}, "
            f"{seen} seen")

    def test_a_backslash_quoted_delimiter_opens_a_heredoc(self):
        """`<<\\PY` is the third spelling of a quoted delimiter and the class
        admitted two.

        sh reads `\\PY` as the word `PY` and suppresses expansion in the body
        exactly as `<<'PY'` does, so this was not an opener: nothing counted,
        nothing scanned. A third spelling of a construct the scanner already
        handles twice is the shape of every silent pass in this file.
        """
        body = ["from agents.lib.ptet import _salt_cap", "PY", "```"]
        for delim in ("<<'PY'", '<<"PY"', "<<\\PY", "<<PY", "<< 'PY'"):
            lines = ["```sh", f"python3 - {delim}"] + body
            found, seen = self._heredoc_offenders("synthetic.md", lines)
            assert (seen, len(found)) == (1, 1), (
                f"delimiter {delim!r} opened no heredoc: {found}, {seen} seen")

        # A QUOTED delimiter is any shell word, and the identifier class read
        # `<<'123'` as no opener at all — body unscanned, nothing counted.
        #
        # The `$` forms are the fourth spelling, and the pair of them is why the
        # dollar cannot be one shared prefix: sh performs quote removal on
        # `$'PY'` and `$"PY"`, so the terminator is `PY` and the body is
        # unexpanded — while `$END` is not quoted at all, no expansion is
        # performed on a delimiter word, and the terminator is the literal
        # `$END`. Dropped from one, kept in the other, and neither matched.
        for delim, term in (("<<'123'", "123"), ('<<"9x"', "9x"),
                            ("<<-'123'", "123"),
                            ("<<$'PY'", "PY"), ('<<$"PY"', "PY"),
                            ("<<-$'PY'", "PY"), ("<<$END", "$END"),
                            ("<< $END", "$END")):
            lines = ["```sh", f"python3 - {delim}",
                     "from agents.lib.ptet import _salt_cap", term, "```"]
            found, seen = self._heredoc_offenders("synthetic.md", lines)
            assert (seen, len(found)) == (1, 1), (
                f"quoted delimiter {delim!r} opened no heredoc: {found}, "
                f"{seen} seen")

        # ...and why the UNQUOTED class keeps its identifier rule, which is the
        # half of the widening that is refused. A left shift is textually an
        # unquoted numeric delimiter, and admitting digits there would read
        # every shifted constant in a fenced block as opening a body that runs
        # to the end of it. The quote is what tells the two apart, and a shift
        # never carries one.
        for shift in ("x = 1 << 3", "mask = flags<<2", "print(1 << 40)"):
            assert self._heredoc_offenders(
                "synthetic.md", ["```py", shift,
                                 "from agents.lib.ptet import _salt_cap",
                                 "```"]) == ([], 0), (
                f"a left shift was read as opening a heredoc: {shift!r}")

        # The bound the `$` widening needs, and it is the terminator: `<<$END`
        # closes on `$END` and on nothing else. Closing on `END` would end the
        # body one line early — which is how a scan reports a heredoc seen and
        # nothing wrong, the silence this whole class of fix keeps arriving at.
        early = ["```sh", "python3 - <<$END", "END",
                 "from agents.lib.ptet import _salt_cap", "$END", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", early)
        assert (seen, len(found)) == (1, 1), (
            f"`$END` closed on `END`, so the import below it was read as text "
            f"outside any body: {found}, {seen} seen")

        # ...and the reason a dollar is safe in the unquoted class where a DIGIT
        # was not. Shell arithmetic writes the one construct that collides —
        # `$((1 << $n))` — and the command holding it executes nothing, so it
        # reaches no interpreter and opens nothing to scan.
        assert self._heredoc_offenders(
            "synthetic.md", ["```sh", "n=3", "echo $((1 << $n))",
                             "from agents.lib.ptet import _salt_cap",
                             "```"]) == ([], 0), (
            "a shifted shell arithmetic expression was read as an opener")

    def test_a_four_backtick_fence_does_not_invert_the_state(self):
        """The standard way to quote a fenced block in markdown is a fence of
        FOUR backticks around one of three, and a toggle on any fence line reads
        it exactly backwards.

        The inner ``` flips the state back to "outside", so everything the outer
        block holds is judged as document PROSE — where the tail rule rejects the
        ordinary `<<'PY' input.json` spelling — and the body is never scanned.
        Silence, on the one construct a document uses to write ABOUT commands.
        """
        engine = "from agents.lib.ptet import _salt_cap"
        quoted = ["````markdown", "```sh", "python3 - <<'PY' input.json",
                  engine, "PY", "```", "````"]
        found, seen = self._heredoc_offenders("synthetic.md", quoted)
        assert (seen, len(found)) == (1, 1), (
            f"a block inside a four-backtick fence was read as prose: "
            f"{found}, {seen} seen")

        inside, boundary = _fence_state(quoted)
        assert inside == [False, True, True, True, True, True, False], inside
        assert boundary == [True, False, False, False, False, False, True], (
            "an inner fence that closes nothing was recorded as a boundary — "
            "which is what would end a heredoc body early, and silently")

        # The two halves of the closing rule, each on its own. A tilde fence does
        # not close a backtick one...
        assert _fence_state(
            ["```sh", "~~~", "x", "```"])[0] == [False, True, True, False]
        # ...and a fence carrying an INFO string opens rather than closes, which
        # is what makes ```sh inside a block content.
        assert _fence_state(
            ["```", "```py", "x", "```"])[0] == [False, True, True, False]

    def test_a_generated_script_run_bare_is_the_pair_neither_audit_sees(self):
        """`cat > gen.py <<'PY'` executes nothing, so the heredoc scanner is
        right not to call it an opener; `python3 gen.py` names no engine target,
        so the per-command audit is right to read it as prose.

        Between them the engine runs on whatever `python3` resolves to, which is
        the failure the whole rule is about, with both audits silent for a
        correct reason each. Requiring BOTH halves is what keeps this off the
        ordinary act of writing a python file out.
        """
        engine = "from agents.lib.ptet import _salt_cap"
        pair = ["```sh", "cat > gen.py <<'PY'", engine, "PY",
                "python3 gen.py", "```"]
        found, seen = self._heredoc_offenders("synthetic.md", pair)
        assert seen == 0, (
            "the writing command executes nothing, so it opens no heredoc — "
            "counting it would claim `cat` runs python")
        assert len(found) == 1 and "gen.py" in found[0], (
            f"a script written by a heredoc and run bare went unreported: "
            f"{found}")
        assert not self._bare_engine_offenders("synthetic.md", pair), (
            "the per-command audit is the one that structurally cannot see "
            "this, and a control that says otherwise is testing the wrong half")

        teed = ["```sh", "tee gen.py <<'PY'", engine, "PY",
                "python3 gen.py", "```"]
        assert len(self._heredoc_offenders("synthetic.md", teed)[0]) == 1, (
            "`tee` writes the same file and the same pair follows")

        # ...and the stage that writes it need not be the one holding the
        # heredoc. Asked of the owner alone, `cat <<'PY' | tee gen.py` named no
        # file: `cat` writes nothing and `tee` holds no heredoc, so each half
        # was clean and the pair was invisible.
        piped = ["```sh", "cat <<'PY' | tee gen.py", engine, "PY",
                 "python3 gen.py", "```"]
        assert len(self._heredoc_offenders("synthetic.md", piped)[0]) == 1, (
            "a script written by a later pipeline stage went unpaired")

        # ...and the file need not be called `gen.py`. Python does not require
        # the suffix — `python3 gen` runs `gen` — so an extensionless generated
        # script was a legal spelling of this exact pair that the check returned
        # None on for a reason about the FILENAME. Nothing was lost by dropping
        # it: the discrimination the extension was reaching for is the PAIR, and
        # a document only reaches here by putting the written path in an
        # interpreter's script position, which is it saying the file is python
        # whatever it is named.
        for nameless in (["```sh", "cat > gen <<'PY'", engine, "PY",
                          "python3 gen", "```"],
                         ["```sh", "cat <<'PY' | tee gen", engine, "PY",
                          "python3 ./gen", "```"],
                         ["```sh", "cat > \"gen\" <<'PY'", engine, "PY",
                          "python3 gen", "```"]):
            assert len(self._heredoc_offenders(
                "synthetic.md", nameless)[0]) == 1, (
                f"an extensionless generated script run bare went unreported: "
                f"{nameless}")

        # What replaces the extension as the bound, because it has to: `.py` was
        # also picking the right operand out of a command with two redirections,
        # and an fd-blind search takes the FIRST — stderr's file, which the body
        # never reaches. Only fd 1 carries it, `&>` included; `2>` carries
        # nothing, and recording its operand would name a file as the script on
        # the strength of a diagnostic log.
        for descriptor, offences in (("cat 2>err.log > gen", 1),
                                     ("cat 1> gen 2>err.log", 1),
                                     ("cat &> gen", 1),
                                     ("cat 2> gen", 0),
                                     ("cat >&2", 0)):
            written = ["```sh", f"{descriptor} <<'PY'", engine, "PY",
                       "python3 gen", "```"]
            assert len(self._heredoc_offenders(
                "synthetic.md", written)[0]) == offences, (
                f"the body's descriptor was read wrong: {descriptor!r} "
                f"should give {offences}")

        # The spellings a shell reads as one file and a string comparison does
        # not. The quoted operand was RECORDED as `"gen.py`, which nothing later
        # matches; the `./` execution failed the word boundary the recorded name
        # was searched with. Both are the ordinary way to write the command and
        # both were silent passes on the construct this check exists for.
        for spelled in (["```sh", "cat > \"gen.py\" <<'PY'", engine, "PY",
                         "python3 gen.py", "```"],
                        ["```sh", "cat > gen.py <<'PY'", engine, "PY",
                         "python3 ./gen.py", "```"],
                        ["```sh", "cat > './gen.py' <<'PY'", engine, "PY",
                         "python3 \"gen.py\"", "```"]):
            assert len(self._heredoc_offenders("synthetic.md", spelled)[0]) == 1, (
                f"two spellings of one file failed to compare equal, so the "
                f"pair went unreported: {spelled}")

        # Four bounds, each removing one half of the pair, and the fourth is the
        # name: `gen.py` is a WORD, so `regen.py` is a different file.
        for clear in (["```sh", "cat > gen.py <<'PY'", engine, "PY",
                       f"{LAUNCHER} gen.py", "```"],
                      ["```sh", "cat > gen.py <<'PY'", engine, "PY", "```"],
                      ["```sh", "cat > notes.txt <<'PY'", engine, "PY",
                       "python3 gen.py", "```"],
                      ["```sh", "cat > gen.py <<'PY'", "print(1)", "PY",
                       "python3 gen.py", "```"],
                      ["```sh", "cat > gen.py <<'PY'", engine, "PY",
                       "python3 regen.py", "```"],
                      # The other half of the normalisation, and the direction a
                      # looser comparison gets wrong: a DIRECTORY is part of the
                      # name. `./gen.py` is the same file, `sub/gen.py` is not.
                      ["```sh", "cat > gen.py <<'PY'", engine, "PY",
                       "python3 sub/gen.py", "```"],
                      # WHO writes the file. A redirection says where a
                      # command's output goes and never that the body is what
                      # goes there: the launcher below writes its PROGRAM's
                      # output to a `.py` file — the compliant spelling §4
                      # mandates, pulled into the walk and reported as an
                      # offender — and `echo` reads no stdin whatever it
                      # redirects.
                      ["```sh", f"{LAUNCHER} - <<'PY' > generated.py", engine,
                       "PY", "```"],
                      ["```sh", "echo > gen.py <<'PY'", engine, "PY",
                       "python3 gen.py", "```"],
                      # ...and WHICH word python runs. The name occurs in both
                      # of these and is the script operand of neither: a `-c`
                      # literal is text, and an argument to another script is
                      # that script's input.
                      ["```sh", "cat > gen.py <<'PY'", engine, "PY",
                       """python3 -c 'print("gen.py")'""", "```"],
                      ["```sh", "cat > gen.py <<'PY'", engine, "PY",
                       "python3 -m tool gen.py", "```"]):
            assert self._heredoc_offenders("synthetic.md", clear)[0] == [], (
                f"a document that does not reach the engine off the launcher "
                f"was indicted: {clear}")

        # The third shape of the same pair, and the one a PIPE carries: engine
        # source handed to an interpreter that reads its program from stdin.
        # `_commands` splits at the pipe — correctly, and for a cry-wolf fix,
        # `python3 -m pytest && <launcher> -m agents.lib.ptet` being two correct
        # commands — so the left half names a file and executes nothing while
        # the right names no engine, and each is clean read alone.
        for piped in ("cat agents/lib/ptet.py | python3 -",
                      "cat agents/lib/ptet.py | cat | python3 -",
                      "echo 'from agents.lib import ptet' | python3",
                      "cat agents/lib/sub/tool.py |& python3 -"):
            assert self._bare_engine_offenders(
                "synthetic.md", ["```sh", piped, "```"]), (
                f"engine source piped into a bare interpreter went unreported: "
                f"{piped}")

        # The bounds, one per half of the rule. A pipe carries the data and
        # `&&` does not; an interpreter with a program of its own reads the pipe
        # as DATA; the launcher on the receiving end is the routed form; and a
        # pipe carrying something else carries nothing.
        for clear in (f"cat agents/lib/ptet.py | {LAUNCHER} -",
                      "cat agents/lib/ptet.py && python3 -",
                      "cat agents/lib/ptet.py | python3 gen.py",
                      "cat agents/lib/ptet.py | python3 -m json.tool",
                      "cat notes.txt | python3 -"):
            assert not self._bare_engine_offenders(
                "synthetic.md", ["```sh", clear, "```"]), (
                f"a document that does not reach the engine bare was indicted: "
                f"{clear}")

        # ...and the PROSE reading, where `|` is a markdown TABLE delimiter at
        # least as often as a pipe. Two code spans in a row are a table; one
        # span holding both halves is a command.
        assert self._bare_engine_offenders(
            "synthetic.md",
            ["Run `cat agents/lib/ptet.py | python3 -` before filing."]), (
            "a prose pipeline written inside one code span went unreported")
        assert not self._bare_engine_offenders(
            "synthetic.md", ["| `agents/lib/ptet.py` | `python3 -` |"]), (
            "a markdown table row was read as a pipeline")

    def test_a_variable_is_read_as_whatever_the_shell_would_expand(self):
        """Two commands, neither an offence on its own.

        `CODE='from agents.lib import ptet'` runs nothing — `_command_head`
        strips an assignment to the empty string, correctly — and `python3 -c
        "$CODE"` names no engine, so the per-command predicate is right about
        each half and there is no half it is wrong about. The offence is the
        PAIR, which is why it is resolved over a sequence of units rather than
        one at a time.

        BOTH halves travel that way, which is what the name of this test used to
        get wrong. It said "engine source held in a variable", and the model
        underneath it agreed: it recorded whether a value held an engine TARGET
        and nothing else, so the INTERPRETER holding the other end of the same
        call — `PYTHON=python3` — was recorded as harmless. The map holds the
        value now and `_expand` puts the text back, so one rule answers the pair
        form, the interpreter form, and the compliant mirror of each, in all
        three audits over this corpus.
        """
        held = ["```sh", "CODE='from agents.lib import ptet'",
                'python3 -c "$CODE"', "```"]
        offenders = self._bare_engine_offenders("synthetic.md", held)
        assert len(offenders) == 1 and ":3:" in offenders[0], (
            f"engine source reaching a bare interpreter through a variable went "
            f"unreported: {offenders}")

        joined = ["```sh",
                  'CODE=\'from agents.lib import ptet\'; python3 -c "$CODE"',
                  "```"]
        assert len(self._bare_engine_offenders("synthetic.md", joined)) == 1, (
            "the same pair on ONE line, which is where a per-unit predicate "
            "could have caught it and did not")

        # Three bounds: the launcher fed the same variable is compliant, a use
        # BEFORE its assignment is not one, and a variable holding something
        # else is not the engine.
        for clear in (["```sh", "CODE='from agents.lib import ptet'",
                       f'{LAUNCHER} -c "$CODE"', "```"],
                      ["```sh", 'python3 -c "$CODE"',
                       "CODE='from agents.lib import ptet'", "```"],
                      ["```sh", "CODE='print(1)'", 'python3 -c "$CODE"',
                       "```"],
                      # ...and the same name REASSIGNED to something else, or
                      # unset, before it is expanded. The holder set only ever
                      # grew, so a name that had once held engine source held it
                      # for the rest of the document and the two lines below
                      # were indicted on a value the shell would not expand.
                      ["```sh", "CODE='from agents.lib import ptet'",
                       "CODE='print(1)'", 'python3 -c "$CODE"', "```"],
                      ["```sh", "CODE='from agents.lib import ptet'",
                       "unset CODE", 'python3 -c "$CODE"', "```"],
                      # An assignment-shaped ARGUMENT is not shell state. Read
                      # as one, `echo CODE=<engine>` — a line that prints a
                      # string and assigns nothing — overwrote the model, and
                      # the expansion below it was reported for a value the
                      # shell would never produce.
                      ["```sh", "CODE='print(1)'",
                       "echo CODE='from agents.lib import ptet'",
                       'python3 -c "$CODE"', "```"],
                      ["```sh", "CODE='print(1)'",
                       "grep -q CODE='from agents.lib import ptet' log",
                       'python3 -c "$CODE"', "```"]):
            assert not self._bare_engine_offenders("synthetic.md", clear), (
                f"a document that does not reach the engine bare was indicted: "
                f"{clear}")

        # ...and the half the argument rule must not take with it: `export`
        # and its siblings assign wherever their operands sit.
        for builtin in ("export CODE='from agents.lib import ptet'",
                        "readonly CODE='from agents.lib import ptet'",
                        "declare -x CODE='from agents.lib import ptet'"):
            assert self._bare_engine_offenders(
                "synthetic.md", ["```sh", builtin, 'python3 -c "$CODE"',
                                 "```"]), (
                f"an assignment builtin stopped being read as one: {builtin!r}")

        # The half the clearing must not take with it: reassignment tracks the
        # LAST value, so a name that starts harmless and is then given engine
        # source is an offence on the line that expands it.
        assert self._bare_engine_offenders(
            "synthetic.md", ["```sh", "CODE='print(1)'",
                             "CODE='from agents.lib import ptet'",
                             'python3 -c "$CODE"', "```"]), (
            "clearing a holder on reassignment cleared it in both directions")

        # A leading assignment on a command that RUNS something is scoped to
        # that command. `RUNNER=<launcher> /usr/bin/true` leaves `RUNNER`
        # holding whatever it held before, so the expansion below it is a bare
        # engine call — and a tracker that carried the temporary value forward
        # cleared it as routed, on a value the shell never held. Report to
        # clear is the direction this file has no tolerance for.
        assert self._bare_engine_offenders(
            "synthetic.md", ["```sh", "RUNNER=python3",
                             f"RUNNER='{LAUNCHER}' /usr/bin/true",
                             "$RUNNER -m agents.lib.ptet", "```"]), (
            "a command-scoped assignment outlived its command and cleared the "
            "engine call below it")
        # The same in the other direction, so the rule is about SCOPE and not
        # about the launcher: a temporary `python3` must not indict a document
        # whose name still holds the launcher.
        assert not self._bare_engine_offenders(
            "synthetic.md", ["```sh", f"RUNNER='{LAUNCHER}'",
                             "RUNNER=python3 /usr/bin/true",
                             "$RUNNER -m agents.lib.ptet", "```"]), (
            "a command-scoped assignment overwrote a binding the shell keeps")
        # ...and the half the scope rule must not take with it: an
        # assignment-ONLY command persists, comment and all.
        assert self._bare_engine_offenders(
            "synthetic.md", ["```sh", "RUNNER=python3  # the host one",
                             "$RUNNER -m agents.lib.ptet", "```"]), (
            "a trailing comment was read as the command word, so the binding "
            "the shell keeps was dropped")

        # The scope is a FILE. A name assigned in one document does not hold in
        # the next, and carrying it across would indict a document for a
        # variable defined somewhere else.
        assert not self._bare_engine_offenders(
            "synthetic.md", ["```sh", 'python3 -c "$CODE"', "```"]), (
            "a holder set leaked past the document that defined it")

        # The INTERPRETER travels in a variable as readily as the source does.
        # A model that recorded whether a value held engine source recorded
        # `PYTHON=python3` as harmless, and `$PYTHON -m agents.lib.ptet` then
        # names no interpreter any pattern here can see — the engine reaching a
        # stock python with every audit clean, which is the silent direction.
        for held in (["```sh", "PYTHON=python3",
                      "$PYTHON -m agents.lib.ptet", "```"],
                     ["```sh", "PY=python3", '"$PY" -m agents.lib.ptet', "```"],
                     ["```sh", "PY=python3", "${PY} agents/lib/ptet.py", "```"],
                     ["```sh", "PY=python3; $PY -m agents.lib.ptet", "```"],
                     ["```sh", "PY=python3",
                      "CODE='from agents.lib import ptet'",
                      '$PY -c "$CODE"', "```"]):
            assert self._bare_engine_offenders("synthetic.md", held), (
                f"an interpreter expanded from a variable reached the engine "
                f"off the launcher unreported: {held}")

        # ...and the three bounds a rule that recognised "this value is an
        # interpreter" would have owed a second answer to each of. A name
        # holding the LAUNCHER expands to the routed form; one the shell would
        # expand to nothing carries nothing; and a name assigned on the SAME
        # command is not yet bound, sh expanding before it assigns. The last is
        # what a per-LINE map would have got wrong in the accusing direction.
        for clear in (["```sh", f"RUNNER='{LAUNCHER}'",
                       "$RUNNER -m agents.lib.ptet", "```"],
                      ["```sh", "PY=python3", "unset PY",
                       "$PY -m agents.lib.ptet", "```"],
                      ["```sh", "PY=python3 ${PY} -m agents.lib.ptet", "```"],
                      # An unrecorded name is left STANDING and not deleted:
                      # emptying `$PY` would join the words on either side of it
                      # into a command nobody wrote.
                      ["```sh", "$PY -m agents.lib.ptet", "```"]):
            assert not self._bare_engine_offenders("synthetic.md", clear), (
                f"a document that does not reach the engine bare was "
                f"indicted: {clear}")

        # The substitution rejoin, driven through THIS audit and not only
        # through `_unactivated`. The two carry the reading separately — one
        # over a unit, one over a sequence of them with a holders map — and a
        # fix applied to one of a pair is the sibling sweep this repository
        # keeps re-learning. Both spellings: the engine target after the closing
        # bracket, and inside it.
        for cut in (["```sh",
                     'python3 "$(git rev-parse --show-toplevel)'
                     '/agents/lib/tie_out.py"', "```"],
                    ["```sh", 'python3 - <<<"$(cat agents/lib/ptet.py)"', "```"]):
            assert self._bare_engine_offenders("synthetic.md", cut), (
                f"an engine target reaching a bare interpreter across a command "
                f"substitution went unreported: {cut}")
        for whole in (["```sh", 'sh "$(git rev-parse --show-toplevel)'
                       '/agents/lib/run.sh" -m agents.lib.ptet', "```"],
                      ["```sh", f'{LAUNCHER} -m agents.lib.ptet "$(date)"',
                       "```"],
                      ["```sh",
                       f"(cd x && python3 -c ok); {LAUNCHER} -m agents.lib.ptet",
                       "```"]):
            assert not self._bare_engine_offenders("synthetic.md", whole), (
                f"a correctly routed command was indicted by the rejoin: {whole}")

        # A LOOP binds a name, and reading only `NAME=value` meant it bound
        # nothing here. `;` puts the module names in one command and the
        # interpreter in another, so neither half is an offence and the pair is
        # — the same shape as the assignment above, spelled with the one
        # construct a runbook reaches for when there is more than one module to
        # run.
        for loop in (["```sh",
                      "for m in agents.lib.tie_out agents.lib.capabilities; "
                      'do python3 -m "$m"; done', "```"],
                     ["```sh", "for m in agents.lib.tie_out", 'do python3 -m "$m"',
                      "done", "```"],
                     ["```sh", "select m in agents.lib.ptet; "
                      'do python3 -m "$m"; done', "```"]):
            assert self._bare_engine_offenders("synthetic.md", loop), (
                f"an engine module reaching a bare interpreter through a loop "
                f"variable went unreported: {loop}")

        # ...and the bounds. The routed spelling of the same loop is clean, a
        # loop over something else binds something else, and the loop's own
        # binding does not outlive being overwritten.
        for clear in (["```sh",
                       "for m in agents.lib.tie_out agents.lib.capabilities; "
                       f'do {LAUNCHER} -m "$m"; done', "```"],
                      ["```sh", 'for f in a.py b.py; do python3 -m pytest "$f"; '
                       "done", "```"],
                      ["```sh", "for m in agents.lib.ptet", "m=pytest",
                       'python3 -m "$m"', "```"]):
            assert not self._bare_engine_offenders("synthetic.md", clear), (
                f"a document that does not reach the engine bare was "
                f"indicted: {clear}")

        # The same defect in the two audits either side of this one, because a
        # finding's scope is the class and not the line it was spotted on. A
        # heredoc's owner is identified by its first WORD, and a word may be a
        # name: `$PY - <<'PY'` opened nothing, so nothing was counted and the
        # body went unread — a silence no count can tell from a document that
        # holds no heredoc at all.
        body = ["from agents.lib.ptet import _salt_cap", "PY", "```"]
        for held in (["```sh", "PY=python3", "$PY - <<'PY'"] + body,
                     ["```sh", "PY=python3; $PY - <<'PY'"] + body):
            found, seen = self._heredoc_offenders("synthetic.md", held)
            assert len(found) == 1 and seen == 1, (
                f"a heredoc opened by an interpreter held in a variable went "
                f"unseen: {held} -> {found}, {seen} seen")

        # ...and its bound, which is what tells the fix from a scanner that
        # stopped telling openers apart: the launcher in that position is SEEN
        # and cleared, never skipped.
        found, seen = self._heredoc_offenders(
            "synthetic.md",
            ["```sh", f"RUNNER='{LAUNCHER}'", "$RUNNER - <<'PY'"] + body)
        assert (found, seen) == ([], 1), (
            f"the launcher expanded from a variable stopped reading as routed: "
            f"{found}, {seen} seen")

        # ...and the generated-script pair, where the interpreter is a variable
        # on the line that runs the file the heredoc wrote.
        wrote = ["```sh", "cat > gen.py <<'PY'",
                 "from agents.lib.ptet import _salt_cap", "PY"]
        assert self._heredoc_offenders(
            "synthetic.md", wrote + ["PY=python3", "$PY gen.py", "```"])[0], (
            "a generated engine script run on an interpreter held in a "
            "variable went unreported")
        assert not self._heredoc_offenders(
            "synthetic.md",
            wrote + [f"RUNNER='{LAUNCHER}'", "$RUNNER gen.py", "```"])[0], (
            "the launcher expanded from a variable was read as a bare "
            "interpreter")

    def test_the_two_inline_c_lexers_read_one_option(self):
        """`INLINE_PY` decides a command HAS an inline body and `INLINE_SCRIPT`
        masks what is IN it, and they disagreed over one character.

        The join required a DETACHED `-c`, so `python3 -uc '` — the cluster
        `INLINE_SCRIPT` had already been widened to read — opened nothing, and a
        multi-line body written that way arrived as separate units with the
        interpreter in the first and the engine import in the third.
        """
        for opener in ("python3 -uc '", "python3 -c'", "python3 -c '"):
            lines = ["```sh", opener, "from agents.lib import tie_out", "'",
                     "```"]
            assert len(self._bare_engine_offenders("synthetic.md", lines)) == 1, (
                f"a multi-line inline body opened by `{opener}` was not joined")
            assert INLINE_SCRIPT.search(f"{opener}from agents.lib import x'"), (
                f"the two lexers disagree about `{opener}`")

        # The LAUNCHER spelling joins the same way and is clean, which is what
        # says the join reports a routing fact and not the shape of the opener.
        # Asserted on the JOIN, since the verdict is `[]` either way and an
        # absent unit and a routed one read the same from outside.
        routed = ["```sh", f"{LAUNCHER} -uc '",
                  "from agents.lib import tie_out", "'", "```"]
        joined = [u for _n, u, _f in _join_units(routed) if "-uc" in u]
        assert joined and "import tie_out" in joined[0], (
            "the launcher's cluster spelling was not joined into one unit")
        assert not self._bare_engine_offenders("synthetic.md", routed), (
            "the launcher form of an inline body was reported")

        # ...and the bound the cluster keeps: after `-c` the rest of the
        # argument is the PROGRAM, so `-cu` runs `u` and is not this shape.
        assert not INLINE_PY.search("python3 -cu 'x'"), (
            "a cluster continuing past the `c` was read as an inline body")

    def test_a_comment_on_the_writer_still_writes_the_script(self):
        """`_heredoc_written` hands `_passes_through` a comment-blanked head, and
        the mask characters are not whitespace — so a trailing `# note` on the
        `cat > gen.py <<'PY'` line stopped `cat` counting as a pass-through and
        the generated-script pair was never recorded.

        `tee` did not have the defect, its operand arm eating the mask, so the
        two members of one closed class disagreed about the same line.
        """
        for writer in ("cat > gen.py <<'PY'  # generator",
                       "cat > gen.py <<'PY'",
                       "cat <<'PY' | tee gen.py  # generator"):
            lines = ["```sh", writer,
                     "from agents.lib.ptet import _salt_cap", "PY",
                     "python3 gen.py", "```"]
            found, _seen = self._heredoc_offenders("synthetic.md", lines)
            assert len(found) == 1, (
                f"a generated script written by `{writer}` was cleared: {found}")

        # The control: routed, and still clean with the same comment on it.
        routed = ["```sh", "cat > gen.py <<'PY'  # generator",
                  "from agents.lib.ptet import _salt_cap", "PY",
                  f"{LAUNCHER} gen.py", "```"]
        assert self._heredoc_offenders("synthetic.md", routed) == ([], 0), (
            "the routed spelling of a generated script was reported")

    def test_a_redirection_among_the_arguments_is_not_the_script(self):
        """`[assignments] [redirections] word [args]` puts a redirection anywhere,
        and `_command_head` strips only the LEADING ones — so `python3 2>&1
        <<'PY'` handed the argument walk `2` and `1`, read the first as python's
        script operand, and never opened the body.

        `python3 - 2>&1 <<'PY'` was read correctly all along, so the two
        spellings of one command disagreed.
        """
        for opener in ("python3 2>&1 <<'PY'", "python3 >/dev/null <<'PY'",
                       "python3 - 2>&1 <<'PY'", "python3 <<'PY' 2>&1"):
            lines = ["```sh", opener, "from agents.lib import ptet", "PY", "```"]
            found, seen = self._heredoc_offenders("synthetic.md", lines)
            assert (len(found), seen) == (1, 1), (
                f"`{opener}` did not open its body: {found}, {seen} seen")

        # The same rule on the other side of a pipe, where `_runs_stdin` asks it.
        for stage in ("python3 2>/dev/null", "python3 -", "python3 - 2>&1"):
            piped = ["```sh", f"cat agents/lib/ptet.py | {stage}", "```"]
            assert len(self._bare_engine_offenders("synthetic.md", piped)) == 1, (
                f"the engine piped into `{stage}` was cleared")

        # ...and the bound stays: a command given a program of its own reads the
        # body as DATA whatever it redirects.
        clear = ["```sh", "python3 -m json.tool 2>&1 <<'PY'",
                 "from agents.lib import ptet", "PY", "```"]
        assert self._heredoc_offenders("synthetic.md", clear) == ([], 0), (
            "a body fed to a program of python's own was read as source")

    def test_a_quoted_value_spanning_lines_is_one_assignment(self):
        """A quoted assignment VALUE spans physical lines exactly as an inline
        `-c` body does, and only the `-c` opener was looked for — so `CODE='` /
        an engine import / `'` was cut into units, the name read as holding the
        empty string, and `python3 -c "$CODE"` expanded to `-c ""` and cleared.
        """
        for quote in ("'", '"'):
            lines = ["```sh", f"CODE={quote}", "from agents.lib import ptet",
                     quote, 'python3 -c "$CODE"', "```"]
            assert len(self._bare_engine_offenders("synthetic.md", lines)) == 1, (
                f"a {quote}-quoted value spanning lines was not joined")

        # The routed mirror stays clean, which is what says the join reports a
        # ROUTING fact rather than the presence of engine source.
        routed = ["```sh", "CODE='", "from agents.lib import ptet", "'",
                  f'{LAUNCHER} -c "$CODE"', "```"]
        assert not self._bare_engine_offenders("synthetic.md", routed), (
            "the launcher form of a multi-line assignment was reported")

    def test_a_heredoc_bound_to_a_variable_is_the_third_pair(self):
        """`read -r -d '' CODE <<'PY'` executes nothing, copies nothing on and
        writes no file, so the scanner had no reading for it at all: nothing
        counted, the body never read, and the `python3 -c "$CODE"` below it names
        no engine. Between them the engine runs on a stock interpreter.
        """
        for binder, closer in (("read -r -d '' CODE <<'PY'", []),
                               # The BUNDLED spelling of the same command, which
                               # is how a runbook writes it and which the
                               # whole-word option test could not read: `-rd`
                               # matched no member of `takes`, `''` stood where
                               # the name goes, and the audit this test exists
                               # for cleared the pair it was written to catch.
                               ("read -rd '' CODE <<'PY'", []),
                               ("CODE=$(cat <<'PY'", [")"]),
                               ("mapfile -t CODE <<'PY'", []),
                               ("mapfile -td '' CODE <<'PY'", [])):
            lines = (["```sh", binder, "from agents.lib import ptet", "PY"]
                     + closer + ['python3 -c "$CODE"', "```"])
            found, _seen = self._heredoc_offenders("synthetic.md", lines)
            assert len(found) == 1, (
                f"a body bound by `{binder}` and run bare was cleared: {found}")

        # Both controls. The routed spelling is compliant, and a bound body that
        # nothing expands into an interpreter is an ordinary here-document.
        routed = ["```sh", "read -r -d '' CODE <<'PY'",
                  "from agents.lib import ptet", "PY",
                  f'{LAUNCHER} -c "$CODE"', "```"]
        assert self._heredoc_offenders("synthetic.md", routed) == ([], 0), (
            "the launcher form of a bound body was reported")
        unused = ["```sh", "read -r -d '' CODE <<'PY'",
                  "from agents.lib import ptet", "PY", 'echo "$CODE"', "```"]
        assert self._heredoc_offenders("synthetic.md", unused) == ([], 0), (
            "a bound body no interpreter runs was reported")

    def test_an_interpreter_is_recognised_beyond_its_posix_spelling(self):
        """`py` is the Windows launcher this repository's own compatibility
        documentation supports, and recognition by enumerated spelling meant `py
        -3 -m agents.lib.ptet` reached the engine with every audit green.

        Bounded by hand rather than by `\\b`, because a word boundary matches the
        EXTENSION of every `.py` path in the corpus.
        """
        assert len(self._bare_engine_offenders(
            "synthetic.md", ["```sh", "py -3 -m agents.lib.ptet", "```"])) == 1
        found, seen = self._heredoc_offenders(
            "synthetic.md", ["```sh", "py -3 - <<'PY'",
                             "from agents.lib import ptet", "PY", "```"])
        assert (len(found), seen) == (1, 1), (
            f"the Windows launcher opened no heredoc: {found}, {seen} seen")

        # The bound, and it is the whole reason `\\b` is not used: an engine PATH
        # ends in `.py`, and reading that as an interpreter would indict every
        # sentence in the tree that names one.
        for prose in ("these are scripts under `agents/lib/ptet.py`, a sibling",
                      "install numpy and py-spy first"):
            assert not self._bare_engine_offenders("synthetic.md", [prose]), (
                f"a `.py` path or a longer word read as the launcher: {prose}")

    def test_a_command_word_may_be_an_alias_and_a_path_may_be_relative(self):
        """Three spellings that reach the engine with no engine target and no
        interpreter written where the patterns look: an ALIAS holding the
        interpreter, a path that is cwd-relative after a `cd`, and a path with an
        interior `..` the engine pattern cannot cross.
        """
        for lines in (["```sh", "alias py3=python3", "py3 -m agents.lib.ptet",
                       "```"],
                      ["```sh", "cd agents/lib && python3 ptet.py", "```"],
                      ["```sh", "cd agents/lib", "python3 -m ptet", "```"],
                      ["```sh", "python3 ./agents/lib/../lib/ptet.py", "```"],
                      # A script operand is a PATH, and the class it was
                      # rewritten with held a bare FILENAME — so a cwd-relative
                      # operand carrying any directory component was rewritten
                      # to nothing and cleared, while the `-m` twin of the same
                      # command two lines up was reported. Both directions of
                      # the same `cd`, because they normalize differently.
                      ["```sh", "cd agents && python3 lib/ptet.py", "```"],
                      ["```sh", "cd agents/lib && python3 ./ptet.py", "```"],
                      ["```sh", "cd agents/lib", "python3 ../lib/ptet.py",
                       "```"]):
            assert len(self._bare_engine_offenders("synthetic.md", lines)) == 1, (
                f"an engine call reached through a rewrite was cleared: {lines}")

        # The bounds, each of which is what keeps the rewrite off ordinary
        # commands: a relocation only names a file this tree HAS, an unknown
        # working directory disables it, and the routed spelling stays routed.
        for clear in (["```sh", "cd agents/lib && python3 -m pytest", "```"],
                      ["```sh", "cd agents/lib && python3 conftest.py", "```"],
                      ["```sh", "cd $HOME", "python3 ptet.py", "```"],
                      ["```sh", f"cd agents/lib && {LAUNCHER} ptet.py", "```"],
                      # ...and the same bound for the widened path class,
                      # from both sides. A multi-segment operand naming a real
                      # file OUTSIDE agents/lib is relocated and still reaches
                      # no engine, and one naming no file in this tree is not
                      # relocated at all. Widening the class is safe only
                      # while the rewrite's TARGET is what decides.
                      ["```sh", "cd agents && python3 ../tests/conftest.py",
                       "```"],
                      ["```sh", "cd agents && python3 lib/nosuch.py", "```"],
                      ["```sh", "alias py3=python3", "echo py3 -m agents.lib.x",
                       "```"]):
            assert not self._bare_engine_offenders("synthetic.md", clear), (
                f"a command that reaches no engine was indicted: {clear}")

    def test_a_fence_admits_the_redirection_spelling_prose_may_not(self):
        """`> out.txt python3 - <<'PY'` is one command POSIX spells exactly
        that way, and the attachment requirement that keeps `> ` out of markdown
        blockquotes made it invisible.

        That requirement is a decision about MARKDOWN — 126 of the 140 heads it
        moves are blockquotes — and inside a fence there are no blockquotes, so
        the noise it buys does not exist there and the hole it leaves does.
        """
        body = ["from agents.lib.ptet import _salt_cap", "PY", "```"]
        found, seen = self._heredoc_offenders(
            "synthetic.md", ["```sh", "> out.txt python3 - <<'PY'"] + body)
        assert (seen, len(found)) == (1, 1), (
            f"a fenced command behind a spaced redirection went unread: "
            f"{found}, {seen} seen")

        assert self._heredoc_offenders(
            "synthetic.md", ["> out.txt python3 - <<'PY'"] + body) == ([], 0), (
            "a markdown blockquote was read as a redirection")

        # The TARGET is a shell word and may be quoted, which the path-shaped
        # class excluded — so the redirection stayed standing as the apparent
        # executable and the opener behind it was never recognised.
        for spelled in ('2>"$LOG" python3 - <<\'PY\'',
                        "2>'my log.txt' python3 - <<'PY'",
                        '> "out dir/x.txt" python3 - <<\'PY\''):
            found, seen = self._heredoc_offenders(
                "synthetic.md", ["```sh", spelled] + body)
            assert (seen, len(found)) == (1, 1), (
                f"a quoted redirect target hid the command behind it: "
                f"{spelled!r} -> {found}, {seen} seen")

        # The case the attachment rule was written around, unaffected in BOTH
        # corpora: consuming `> python3 ` hands back `-`, which is not an
        # interpreter, so a quoted opener stays unread either way.
        for lines in (["```sh", "> python3 - <<'PY'"] + body,
                      ["> python3 - <<'PY'"] + body):
            assert self._heredoc_offenders("synthetic.md", lines) == ([], 0), (
                "a blockquote quoting an opener was read as one")

    def test_the_body_mask_stops_only_at_a_bare_dynamic_exec(self):
        """`\\b(?:exec|eval|compile)\\s*\\(` matched `re.compile(`.

        A body that builds a regex had every string literal in it read raw, so
        `print("never: from agents.lib import ptet")` — an accurate quotation of
        the rule — was reported as the engine reached from a bare interpreter.
        Half this file's own examples are bodies that build a regex.
        """
        engine = 'print("never: from agents.lib import ptet")'
        for body in (["import re", 'pat = re.compile(r"\\d+")', engine],
                     ["import json", "x = json.loads(s)", engine],
                     ["self.eval(1)", engine],
                     ["codeop.compile_command(s)", engine],
                     # A body that TALKS about the construct executes nothing.
                     # Searched over the raw text, a sentence in a comment and a
                     # literal naming it both stopped the mask for the whole
                     # body, and every unrelated string in it was then read as
                     # code — a gate firing on a document that explains the rule.
                     ["# exec(code) is how a string becomes code", engine],
                     ['print("call exec(src) to run it")', engine],
                     [engine]):
            lines = ["```sh", "python3 - <<'PY'"] + body + ["PY", "```"]
            found, seen = self._heredoc_offenders("synthetic.md", lines)
            assert (found, seen) == ([], 1), (
                f"a quoted engine import beside a DOTTED compile/eval was read "
                f"as executed: {body} -> {found}")

        # The half that has to survive: a BARE one really does turn the string
        # into code, so the mask has to stop for it. `builtins.` is the same
        # function under its module and is admitted for that reason.
        for call in ('exec("from agents.lib import ptet")',
                     'eval("from agents.lib import ptet")',
                     'compile("from agents.lib import ptet", "x", "exec")',
                     'builtins.exec("from agents.lib import ptet")'):
            lines = ["```sh", "python3 - <<'PY'", call, "PY", "```"]
            found, seen = self._heredoc_offenders("synthetic.md", lines)
            assert (len(found), seen) == (1, 1), (
                f"a literal a bare dynamic exec turns into code was blanked: "
                f"{call} -> {found}")

        # What the coarse rule COSTS, asserted rather than left to be
        # rediscovered: one bare call keeps every literal in the body, including
        # ones it never reaches. The narrower rule — mask all but the argument —
        # is written up and declined at `DYNAMIC_EXEC`, because the source of an
        # `exec` is as often an assignment as an argument and a rule reading
        # arguments misses that one SILENTLY. Pinned here so that narrowing it
        # has to argue with a test rather than with a comment.
        aside = ["```sh", "python3 - <<'PY'",
                 'compile("print(1)", "<s>", "exec")',
                 'print("from agents.lib import ptet")', "PY", "```"]
        assert len(self._heredoc_offenders("synthetic.md", aside)[0]) == 1, (
            "the mask stopped for a bare dynamic exec and kept only some "
            "literals — which one it kept is the question that has no local "
            "answer")

        # The two LOADERS, which take a module NAME rather than source text.
        # They were excluded while nothing here could read a name — true of
        # `ENGINE_IMPORT`, a pattern about the `from`/`import` keywords — and
        # that exclusion WAS the hole: the mask took the literal and no pattern
        # wanted it back, so `python3 -c '__import__("agents.lib.ptet")'` bound
        # the engine on a stock interpreter with every audit clean.
        for call in ('__import__("agents.lib.ptet")',
                     "importlib.import_module('agents.lib.ptet')",
                     "from importlib import import_module; "
                     'import_module("lib.ptet")'):
            lines = ["```sh", "python3 - <<'PY'", call, "PY", "```"]
            found, seen = self._heredoc_offenders("synthetic.md", lines)
            assert (len(found), seen) == (1, 1), (
                f"a dynamic loader handed the engine by name went unread: "
                f"{call} -> {found}")
            assert _unactivated(f"python3 -c '{call}'", fenced=True), (
                f"the same loader in a `-c` operand: {call}")
            assert not _unactivated(f"{LAUNCHER} -c '{call}'", fenced=True), (
                f"the launcher was indicted for the loader it routed: {call}")

        # Both bounds. A loader naming something else is not the engine, and a
        # loader QUOTED is text: the guard reads the MASKED body, so a literal
        # that merely spells the call does not stop the mask — which is the
        # same answer `exec` already gets two blocks up.
        for clear in ('__import__("json")', "import_module('os.path')",
                      'print("__import__(\\"agents.lib.ptet\\")")'):
            lines = ["```sh", "python3 - <<'PY'", clear, "PY", "```"]
            assert self._heredoc_offenders("synthetic.md", lines) == ([], 1), (
                f"a body that binds no engine was indicted: {clear}")

    def test_the_body_mask_covers_a_literal_on_every_interpreter(self):
        """A version-dependent verdict is worse than either answer, and this
        suite runs on two interpreters.

        3.12 gave f-strings their own token type and 3.14 gave t-strings one, so
        `t"from agents.lib import ptet"` was masked on 3.12 — where it lexes as a
        NAME and a STRING — and read as executed code on 3.14. The same document,
        clean on one CI leg and an offender on the other.
        """
        for literal in ('"from agents.lib import ptet"',
                        'f"from agents.lib import ptet"',
                        'rb"from agents.lib import ptet"',
                        't"from agents.lib import ptet"'):
            lines = ["```sh", "python3 - <<'PY'", f"print({literal})", "PY",
                     "```"]
            found, seen = self._heredoc_offenders("synthetic.md", lines)
            assert (found, seen) == ([], 1), (
                f"a quoted engine import was read as executed on this "
                f"interpreter: {literal} -> {found}")

    @staticmethod
    def _bare_engine_offenders(rel, lines):
        """Returns the offenders in `lines`, as the heredoc scanner does and for
        the same reason: the audit above runs over a corpus that is CLEAN, so
        nothing in it can tell a document with no offence from a scan that reads
        the document wrongly. The control feeds this synthetic lines whose
        answers are known, and it is the function the audit calls — a control
        with its own copy of the loop passes against the copy.

        That is not hypothetical here. The join went into the audit with a
        control that exercised `_join_units` and `BARE_ENGINE_CALL` directly, and
        reverting the audit to a per-line scan left the suite green: the two
        pieces were tested and their USE was not.

        `_unactivated`, which is the skills audit, rather than the pattern it
        calls. The two ask one question and had drifted apart on the answer:
        applied to a whole unit the pattern reaches ACROSS a separator, so
        `python3 -m pytest tests/ -q && sh agents/lib/run.sh -m agents.lib.ptet`
        — two commands, both correct — was reported here and nowhere else. The
        skills audit had split on separators since the round that found it there.
        One predicate, and the documents get the assignment stripping too.
        """
        return [f"{rel}:{n}: {' '.join(unit.split())[:70]}"
                for _, n, unit in _bare_engine_units(
                    (rel, n, unit, fenced)
                    for n, unit, fenced in _join_units(lines))]

    @classmethod
    def _heredoc_offenders(cls, rel, lines):
        """Returns (offenders, heredocs_seen). A heredoc opened by a python
        invocation: capture its terminator, then read the body up to that
        terminator alone on a line — `_terminates` decides that, and "alone" is
        the whole of it. `run.sh` on the command that OWNS the
        invocation is the routed form and the whole point of the rule. The count comes back so callers can
        tell "nothing was wrong" from "nothing was looked at".

        The tail after the terminator is a CLOSED class of shell syntax —
        redirections (`> f`, `2>&1`), a pipeline, `&&`/`||`/`;`/`&`, a comment, a
        closing `)`. It is deliberately not `.*`: an opener quoted inside markdown
        prose ends in a backtick or a word, and admitting those would make every
        such sentence an opener whose terminator never arrives, so the scan runs
        to EOF and indicts every engine import below it. Requiring end-of-line
        instead — the first spelling — was the opposite error: `python3 - <<\'PY\'
        > fixture.json` registered as no heredoc at all, and a heredoc never seen
        is a body never read.

        The TERMINATOR is a shell word, not an identifier: `<<\'PY-CODE\'` is
        valid and `\\w+` stopped at the hyphen.

        A `\\`-continued opener is ONE command and is joined before matching, the
        way a shell joins it — by deleting the backslash and the newline. Both
        halves of the form need it. `python3 - \\` / `<<\'PY\'` puts the
        interpreter and the delimiter on different LINES, so a per-line match
        finds no opener at all: nothing counted, nothing scanned, and a bare
        engine call in the body clean away. That is the silent pass, in the
        scanner written against silent passes. `python3 - <<\'PY\' \\` /
        `> fixture.json` continues AFTER the delimiter; that one was at least
        seen, but its body was read starting at the redirection line, one line
        early. The trailing `\\` stays in the prose tail class for the opener
        continued on the last line of the file, where there is nothing to join."""
        # EVERY heredoc on the line, in the order the delimiters appear, and
        # that is a change from "the last `<<` wins". `python3 - <<X && cat <<Y`
        # is two commands with one heredoc each and their bodies arrive in
        # delimiter order — X's first, and X's is the one python executes. Keyed
        # on the last, the scanner asked `cat` whether it runs an interpreter,
        # answered no, and never read X at all: nothing seen, nothing scanned,
        # the body python really does run clean away. The comment that stood
        # here named that shape as a known bound and stated the opposite of what
        # the code did — it claimed Y is read — which is the failure this file
        # keeps finding in itself, written down and then believed.
        #
        # `python3 - <<A <<B` on ONE command is the other shape, and both bodies
        # belong to that command: sh reads A's text and discards it, B's is what
        # python gets on stdin. Each is now scanned against the command that owns
        # it, in its own extent, rather than as one blob running from A's first
        # line to B's terminator.
        #
        # Masking first is what makes any of it safe: unmasked, a `<<` inside a
        # quoted string is a delimiter, and its "body" belongs to a command that
        # has none.
        #
        # The launcher is admitted beside the interpreter by `_heredoc_runner`,
        # which decides which command — or neither — actually runs the body.
        # Keyed on the interpreter alone the scanner could not even SEE
        # `<launcher> - <<\'PY\'`, the compliant spelling, so that body was never
        # scanned and the clearance was an absence rather than a verdict.
        #
        # `<<<` is a HERE-STRING and opens no body at all: the whole input is
        # the word after it, on that same line. Unguarded, the greedy `[^\n]*`
        # backtracks onto the last two of the three `<`, and `python3 -
        # <<<'print(1)'` registered as a heredoc terminated by `print` — a
        # terminator no line ever equals, so the scan swallowed the rest of the
        # block and read every quoted example below it as executed python. That
        # is the cry-wolf direction, on shell this repository already writes
        # sixteen times over, and a gate that fires on a correct document is how
        # a reader learns to reason past it. The guard is a lookBEHIND and only
        # that: of the two candidate pairs in `<<<` the leading one is already
        # unreachable — a `<` is not a dash, not whitespace, not a quote and not
        # the first character of a terminator, so the class after `<<` rejects
        # it — and the pair the backtrack actually lands on is the trailing one,
        # which is the one a preceding `<` rejects. A matching `(?!<)` was
        # written here first and killed nothing: it is unfalsifiable by
        # construction, which is the shape this file refuses everywhere else.
        #
        # It closes no hole, which is the reason it is safe to exclude rather
        # than to parse: a here-string keeps its code on the COMMAND, where
        # `_unactivated` — the per-command audit this scanner exists to
        # supplement — already reads it. The control below asserts that rather
        # than assuming it.
        # The DELIMITER may be quoted three ways, not two. `<<\PY` is the third:
        # sh takes `\PY` as the literal word `PY` and suppresses expansion in the
        # body exactly as `<<'PY'` does, and the class admitted only the two
        # quote characters — so the opener was not one, nothing was counted, and
        # the body went unread. A third spelling of a construct this scanner
        # already handles twice is the shape of every silent pass in this file.
        # A QUOTED delimiter is any shell word — `<<'123'` is valid sh and the
        # identifier class found no opener in it, so the body went unscanned.
        # Unquoted it stays an identifier, and that asymmetry is the point
        # rather than an omission: `x = 1 << 3` is a left shift, textually
        # identical to an unquoted numeric delimiter, and admitting digits there
        # would read every shifted constant in a fenced block as opening a body
        # that runs to the end of it. The quote is what tells the two apart, and
        # a shift never has one.
        #
        # The DESCRIPTOR is captured because `<<` defaults to stdin and `3<<`
        # does not — see the supersession map below.
        # The descriptor is its own WORD, and `(?<![<\d])(?P<fd>\d*)` said any
        # digit in front of `<<` was one. `python3<<'PY'` is valid sh with the
        # heredoc attached to the command word, and the `3` of `python3` was
        # read as fd 3 — a descriptor no interpreter executes, so the body was
        # walked and never scanned. Two alternatives rather than an optional
        # group: a descriptor that starts a word, or no descriptor at all, in
        # which case only the here-string guard applies.
        # A `$` in front of the delimiter is a fourth spelling and the shell
        # reads it two different ways, so both are here. `<<$'PY'` and `<<$"PY"`
        # are QUOTED words — quote removal leaves `PY` and, being quoted, the
        # body is not expanded, exactly as `<<'PY'` behaves — while `<<$END` is
        # UNQUOTED, `$` is not a quoting character, and no expansion is performed
        # on a delimiter word: the terminator is the literal `$END`. So the
        # dollar is DROPPED from the quoted forms and KEPT in the bare one, which
        # is the whole of why it cannot be one shared prefix. Matching neither
        # left the opener unrecognised, the heredoc uncounted and the body never
        # read — the silent direction, and the fourth time this scanner has been
        # taught a spelling of the construct it already handled.
        opener = re.compile(
            r"(?:(?<![\w<])(?P<fd>\d+)|(?<!<))<<(?P<dash>-?)\s*"
            r"(?:\$?'(?P<sq>[^'\n]+)'|\$?\"(?P<dq>[^\"\n]+)\""
            r"|\\(?P<bs>[\w.+-]+)|(?P<bare>\$?[A-Za-z_][\w.-]*))")
        # INSIDE a fence, anything may follow the delimiter: `<<'PY' input.json`
        # passes a positional argument and `<<'PY' --flag` an option, both of
        # which sh runs exactly as written. OUTSIDE one, the tail stays the
        # closed operator class, because a bare word after the delimiter is also
        # what a SENTENCE looks like — "see python3 - <<'PY' above" — and the two
        # are textually identical. The fence is the only thing that separates a
        # command from a mention of one, so it is what the two are separated by.
        # ANY tail inside a fence, and the leading `\s` that used to be required
        # was not a bound — it was a hole. `out="$(python3 - <<\'PY\')"` closes on
        # `)"` with no space before it, so inside a ```sh block, where documents
        # actually write shell, the commonest substitution spelling matched
        # nothing at all. The prose class admits `)` directly and caught it; the
        # fenced one, meant to be the PERMISSIVE of the two, did not.
        #
        # Asked of the LAST delimiter on the line, since that is where the tail
        # is, and it gates the whole line rather than one redirection: prose that
        # mentions an opener mentions the whole command.
        prose_tail = re.compile(r"\s*(?:\d*[<>|&;#)\\][^\n]*)?$")
        # A plain input redirection to fd 0, which is the other thing that can
        # take stdin. `python3 - <<'PY' </dev/null` runs the interpreter on an
        # empty stdin and the body is never executed, so reporting an import in
        # it is an accusation about text nothing runs. Excluded: `<<` (this is
        # the heredoc itself), `<(` (process substitution is an ARGUMENT), and
        # `<&` (a dup, which names no file).
        stdin_redirect = re.compile(
            r"(?:(?<![\w<])(?P<fd>\d+)|(?<!<))<(?![<(&])")
        engine = re.compile(ENGINE_BINDING)
        # One pass for fence state, so the body scan below can jump the index
        # without having to keep it.
        inside, boundary = _fence_state(lines)

        # What each name holds, carried down the FILE the way the shell
        # carries it. Assignments inside a heredoc BODY are recorded too, which
        # is loose in the cry-wolf direction and deliberately not tightened: an
        # extra binding can only put more text in front of a pattern that still
        # has to find an interpreter reaching an engine target, and the audit
        # over these same documents has read body lines as shell since it
        # existed.
        offenders, seen, i, generated, holders = [], 0, 0, [], {}
        # The bodies that reach the engine and land in a NAME rather than in an
        # interpreter or a file — see `_heredoc_bound`. Collected beside
        # `generated` because they are the same shape of offence: no single
        # command carries it, and the pair is resolved after the walk.
        bindings = []
        while i < len(lines):
            in_fence = inside[i]
            # A `<<` the shell does not read as a redirection opens nothing. A
            # fenced block whose lines only ECHO an example — `echo \'python3 -
            # <<PY\'` and the two below it — executes no python at all, and was
            # reported as the engine running on a bare interpreter: a gate firing
            # on a correct document, which teaches a reader to reason past it.
            # Only the `<` is masked; blanking the whole quoted region would take
            # the DELIMITER of `<<\'PY\'` with it, quoted there as heredoc syntax
            # rather than to hide anything.
            # Joined only within one fence state: a `\\` on the line before a
            # closing fence continues nothing, and swallowing the fence would
            # lose the bound that keeps an unterminated body out of the rest of
            # the file.
            logical, end = lines[i], i
            while (logical.rstrip().endswith("\\") and end + 1 < len(lines)
                   and inside[end + 1] == in_fence):
                end += 1
                logical = logical.rstrip()[:-1] + lines[end]
            masked = _blank_comments(_blank_inert(logical, only="<"), only="<")
            here = list(opener.finditer(masked))
            # The PROSE gate, asked once of the whole line rather than per
            # redirection: a sentence mentioning an opener mentions the command
            # entire, and the tail that tells the two apart sits after the last
            # delimiter on it.
            if here and not in_fence and not prose_tail.match(
                    masked, here[-1].end()):
                here = []
            if not here:
                # By ONE line, not past the join. A continuation that opened
                # nothing may still be followed by a line that opens something,
                # and the joined text matching is what proves the reverse
                # direction safe: a tail that matches alone matches joined too.
                holders = _values_before(logical, len(logical) + 1, holders)
                i += 1
                continue
            # WHICH command holds each delimiter decides both questions, and the
            # first of them is whether this is an opener at all.
            # `echo python3 && cat <<'PY'` feeds its body to `cat`, which
            # executes nothing, and reporting that as the engine running on a
            # bare interpreter is a false accusation on a correct document. A
            # gate that fires on correct data is how a reader learns to reason
            # past a gate.
            #
            # This is the sibling of the per-command pairing `_unactivated`
            # already does, in the audit a previous round examined and cleared
            # on the ground that a heredoc has exactly one owner. It does — but
            # the INTERPRETER is not part of what identifies it, so the two
            # facts still floated free of each other here.
            # ONE stdin per command, and it is the LAST redirection to it.
            # `python3 - <<A <<B` opens both, and sh reads A's text and throws it
            # away: only B reaches python. Scanning A would report an import
            # nothing executes — the cry-wolf direction — while skipping its
            # lines would put every line number after it out by the length of a
            # body. So the earlier ones are WALKED and not read.
            #
            # Per COMMAND, which is the distinction the single-`<<`-per-line rule
            # this replaced could not make: `python3 - <<X && cat <<Y` is two
            # commands with one stdin each, and X is not superseded by anything.
            # ...and one stdin per command means one per DESCRIPTOR. `python3
            # - <<PY 3<<DATA` opens two, and only the first feeds the
            # interpreter: fd 3 is a data channel python never executes. Keyed
            # on the command alone, the later heredoc superseded the earlier one
            # whatever it redirected — so the body python actually runs was
            # walked and DATA was scanned in its place, which is the silent
            # direction. Only fd 0 is READ, for the same reason: a body on
            # another descriptor reaches no interpreter, and reporting it would
            # be an accusation about text nothing executes.
            last = {}
            for idx, hit in enumerate(here):
                last[(_command_index(masked, hit.start()),
                      hit.group("fd") or "0")] = idx
            live = {idx for (_, fd), idx in last.items() if fd == "0"}
            # ...and a plain redirection AFTER the surviving heredoc takes stdin
            # back from it. Ordered by POSITION, because that is what the shell
            # uses: the last redirection to a descriptor is the one that stands.
            for other in stdin_redirect.finditer(masked):
                if (other.group("fd") or "0") != "0":
                    continue
                idx = last.get((_command_index(masked, other.start()), "0"))
                if idx is not None and here[idx].start() < other.start():
                    live.discard(idx)
            reading = []
            for idx, hit in enumerate(here):
                if idx not in live:
                    reading.append((hit, False, False, None, None))
                    continue
                # The launcher is a COMMAND WORD, so the match ends where the
                # word does. `startswith` alone made every path the launcher is a
                # PREFIX of into the launcher: `sh agents/lib/run.sh.bak -
                # <<'PY'` was recorded as routed and its body waved through
                # unread, though the one launcher AGENTS.md §4 mandates was never
                # executed. That is an affirmative clearance granted on a
                # spelling, and the clearance is the direction that must not be
                # loose — a body wrongly scanned is a visible false accusation, a
                # body wrongly cleared is silence.
                # ...and the first word may be a NAME. `PY=python3` then
                # `$PY - <<'PY'` is an interpreter reaching a body with no
                # interpreter written anywhere on the command, so the line was
                # not an opener, nothing was counted, and the body went unread —
                # the silent direction, and the same defect the per-command
                # audit had one function over. Expanded, the word is whatever
                # the shell would run: `RUNNER=<launcher>` then `$RUNNER -
                # <<'PY'` is routed, and reads as routed.
                head = _expand(_heredoc_runner(masked, hit.start(), in_fence),
                               _values_before(logical, hit.start(), holders))
                routed = bool(re.match(rf"{re.escape(LAUNCHER)}(?=\s|$)", head))
                # ...or a NEAR MISS of the launcher, which is an opener that is
                # not routed. Tightening `routed` above only moves the impostor
                # from "cleared" to "not an opener at all", and both spellings of
                # silence read the same from outside. It carries the mandated
                # launcher's path as its executable and is not it, which is a
                # violation of §4 wearing the compliant form's clothes — the one
                # shape where scanning the body is the fail-closed answer.
                #
                # ANCHORED, like `routed`, and it was not: a near miss is a
                # matter of POSITION, and `LAUNCHER in head` asked only whether
                # the text appeared. `echo sh agents/lib/run.sh.bak <<'PY'` runs
                # `echo`, which executes nothing whatever its argv holds — read
                # as an impostor, its body was scanned and every import in it
                # reported. The two branches differ in one character's worth of
                # context, so they cannot both hold.
                impostor = bool(re.match(rf"{re.escape(LAUNCHER)}\S", head))
                # An opener is a command whose EXECUTABLE runs the body: an
                # interpreter, or the launcher that execs one. The first word,
                # never a token anywhere in the line — `echo python3 <<'PY'`
                # names an interpreter and runs none.
                word = re.match(r"\S+", head)
                # ...and an opener is a command whose stdin python EXECUTES.
                # `python3 -c '...' <<'PY'`, `python3 -m json.tool <<'PY'` and
                # `python3 tool.py <<'PY'` all run an interpreter and all give
                # it a program already; the body is DATA that program may read.
                # Scanning it reported an engine import nothing runs, on a
                # correct document — see `_interpreter_role`, which owns the
                # rule and the bound.
                #
                # `DYNAMIC_EXEC` is that bound where it can be read: a `-c`
                # literal that calls `exec` may execute exactly this stdin, and
                # the literal is right here. Asked of the whole head rather than
                # of the operand alone, which over-keeps and is the safe side.
                runs = bool(routed or impostor
                            or (word and INTERPRETER.search(word.group())))
                skip = len(LAUNCHER.split()) if routed or impostor else 1
                opens = bool(runs
                             and (_interpreter_role(head, skip)[0]
                                  or DYNAMIC_EXEC.search(head)))
                # ...and the third thing a body can be: a FILE. `cat > gen.py
                # <<'PY'` executes nothing, so it is not an opener — and the
                # `python3 gen.py` two lines below it names no engine target, so
                # the per-command audit reads it as prose. Recorded here and
                # resolved after the walk, because neither half is an offence
                # and the pair is.
                #
                # Asked only of commands that COPY stdin, and of every such
                # stage the body flows through — the same traversal the runner
                # makes, for the same reason. A redirection says where a
                # command's output goes, never that the body is what goes there:
                # `run.sh - <<'PY' > generated.py` is the mandated launcher
                # writing its program's OUTPUT to a file, and reading that as
                # engine source on disk pulled a compliant command into the walk
                # below and reported it; `echo > gen.py <<'PY'` reads no stdin
                # whatever it redirects. See `_heredoc_written`.
                reading.append((hit, routed, opens,
                                _heredoc_written(logical, hit.start(),
                                                 in_fence),
                                _heredoc_bound(logical, hit.start(),
                                               in_fence)))
            # No early exit when nothing on the line is live. A heredoc whose
            # owner executes nothing still HAS a body, and skipping by one line
            # left the shell's own text to be parsed as markup: a `python3
            # <<'PY'` example quoted inside `cat <<'DOC'` was read as a live
            # opener and the document reporting the rule correctly was indicted.
            # Every recognised extent is walked; only the ones that can reach an
            # interpreter are read.
            # The body begins after the LAST line the opener occupies; `start`
            # names the first, which is where a reader looks. Bodies then follow
            # one another in DELIMITER order, each ending at its own terminator:
            # that ordering is the whole of what makes two heredocs on one line
            # readable, and reading only the last one left the first unscanned.
            start, pos = i + 1, end + 1
            for hit, routed, opens, script, bound in reading:
                term = (hit.group("sq") or hit.group("dq") or hit.group("bs")
                        or hit.group("bare"))
                dash = bool(hit.group("dash"))
                # Where the body ENDS, resolved ONCE. Three things need it — the
                # walk below, the continuation join inside it, and the extent
                # handed to the tokenizer — and the first two carried their own
                # copy of the rule. A third copy is a third thing to keep true,
                # and this one could not be a copy anyway: a mask that covers
                # different lines than the scan reads masks the wrong ones.
                #
                # A fenced heredoc whose terminator is missing is a malformed
                # block, not a licence to read the rest of the file as its body,
                # so the fence bounds it exactly as the terminator does — and by
                # `boundary`, never by "matches FENCE", or an inner ``` that
                # closes nothing would end the body early and silently.
                stop = pos
                while (stop < len(lines)
                       and not _terminates(lines[stop], term, dash)
                       and not (in_fence and boundary[stop])):
                    stop += 1
                if opens:
                    seen += 1
                if (opens and not routed) or script or bound:
                    # What PYTHON runs, not what the body says. A raw search
                    # reported `# from agents.lib import ptet` and
                    # `print("from agents.lib import ptet")` — a comment and a
                    # string, executed by nothing — as the engine reached from a
                    # bare interpreter. That is the shell-side `echo \'python3 -
                    # <<PY\'` case one language over, and the same answer: mask
                    # what the lexer says is not code, then match. Reported from
                    # the ORIGINAL line, because the mask is for the verdict and
                    # a reader needs the text.
                    probes, j = _blank_python(lines[pos:stop]), pos
                    while j < stop:
                        # The BODY joins its own continuations too, and for the
                        # reason the opener does: python's explicit line joining
                        # deletes a trailing backslash and the newline before the
                        # statement is read, so `from agents.\` / `lib import
                        # ptet` executes the engine while neither physical line
                        # names it. Parenthesised continuations need nothing.
                        #
                        # Bounded by the terminator and the fence, never past
                        # them. Joining slightly more than python would can only
                        # ADD text to a line the pattern is looking at, so the
                        # error is a visible false accusation rather than
                        # silence. The mask is joined ALONGSIDE it, by column:
                        # `_blank_python` replaces characters and never removes
                        # any, so a match in one is a match in the other.
                        stmt, probe, k = lines[j], probes[j - pos], j
                        while stmt.rstrip().endswith("\\") and k + 1 < stop:
                            cut = len(stmt.rstrip()) - 1   # drop the backslash
                            stmt = stmt[:cut] + lines[k + 1]
                            probe = probe[:cut] + probes[k + 1 - pos]
                            k += 1
                        if engine.search(probe):
                            # `not routed` here, matching the walk condition
                            # above. A body reached the walk on EITHER ground,
                            # and a routed opener that also wrote a file entered
                            # it on the second and was reported on the first —
                            # an offence recorded against the one spelling
                            # AGENTS.md §4 mandates.
                            if opens and not routed:
                                offenders.append(
                                    f"{rel}:{j + 1}: heredoc opened at line "
                                    f"{start} reaches the engine: "
                                    f"{' '.join(stmt.split())[:60]}")
                            elif script:
                                generated.append((script, j + 1, start))
                            elif bound:
                                bindings.append(
                                    (bound, "\n".join(lines[pos:stop]),
                                     j + 1, start))
                        j = k + 1
                pos = stop + 1
            holders = _values_before(logical, len(logical) + 1, holders)
            i = pos
        return (offenders
                + cls._generated_offenders(rel, lines, generated)
                + cls._bound_offenders(rel, lines, bindings)), seen

    @staticmethod
    def _bound_offenders(rel, lines, bindings):
        """The offence no single command carries, in its third shape: a heredoc
        body that reaches the engine, bound to a VARIABLE, and that variable then
        expanded into an inline program on a bare interpreter.

        `read -r -d '' CODE <<'PY'` executes nothing, so the scanner is right not
        to call it an opener; `python3 -c "$CODE"` names no engine, so
        `BARE_ENGINE_CALL` is right to read it as prose. Between them the engine
        runs on whatever `python3` resolves to, which is the failure the whole
        rule is about, with both audits silent for a correct reason each. Same
        argument, same resolution and the same controls as
        `_generated_offenders`, one binding site along.

        The verdict is `_bare_engine_command`'s, with the body supplied as the
        name's VALUE, rather than a second rule about `-c` operands. That is what
        keeps the routed spelling clean without a second exemption: the launcher
        form expands to a launcher, and the lookbehind reads it where it sits.

        Scoped to the DOCUMENT, for the reason `_generated_offenders` is: a
        runbook that shows the command before the here-document that fills it is
        ordinary prose order, and a positional rule buys a silent pass to remove
        a false accusation.
        """
        if not bindings:
            return []
        offenders, seen = [], set()
        for n, unit, fenced in _join_units(lines):
            for command in _commands(unit) + _substitution_commands(unit):
                for name, body, body_line, opened_at in bindings:
                    # The name has to be EXPANDED here, not merely present: the
                    # command that binds it holds the same word, and reporting
                    # that one accuses the line that wrote the body of running it.
                    if not re.search(rf"\$\{{{re.escape(name)}\}}"
                                     rf"|\${re.escape(name)}\b", command):
                        continue
                    if not _bare_engine_command(command, fenced, {name: body}):
                        continue
                    key = (n, name, opened_at)
                    if key in seen:
                        continue
                    seen.add(key)
                    offenders.append(
                        f"{rel}:{n}: runs `${name}` on a bare interpreter — "
                        f"bound by the heredoc opened at line {opened_at}, "
                        f"which reaches the engine at line {body_line}")
        return offenders

    @staticmethod
    def _generated_offenders(rel, lines, generated):
        """The offence no single command carries: a heredoc body that reaches
        the engine, WRITTEN to a `.py` file, and that file then run on a bare
        interpreter.

        `cat > gen.py <<'PY'` feeds its body to a redirection and executes
        nothing, so the heredoc scanner is right not to call it an opener;
        `python3 gen.py` names no engine target, so `BARE_ENGINE_CALL` is right
        to read it as prose. Between them the engine runs on whatever `python3`
        resolves to — the failure the whole rule is about — with both audits
        silent for a correct reason each.

        Requiring BOTH halves is what keeps this off the ordinary act of writing
        a python file out. A runbook that generates a script and then runs it
        through the launcher is compliant and stays clean, and that is the
        control the tests state.

        Scoped to the DOCUMENT and not to the text below the terminator, which is
        a refusal rather than an oversight. Printing the command before showing
        the heredoc that writes the script is ordinary prose order — "run
        `python3 gen.py`, where `gen.py` is:" — and a positional rule clears it:
        a silent pass bought to remove a false accusation, which is the trade
        every paragraph in this file exists to refuse. The narrowing that IS safe
        is a narrower FILE, not a nearer position, and the `.py` operand plus a
        body that reaches the engine already carry it.
        """
        # The interpreter invocations, resolved ONCE and in file order rather
        # than once per generated script. The nested walk it replaces could not
        # carry shell state across its outer loop, and the first word of a
        # command may be a NAME: `PY=python3` then `$PY gen.py` runs the
        # generated script on a stock interpreter with no interpreter written
        # anywhere, which is the hole `_expand` closes for the two audits either
        # side of this one.
        if not generated:
            return []
        heads, holders = [], {}

        def record(n, command, fenced, values):
            text = _expand(_blank_comments(command), values)
            head = _command_head(text, fenced)
            word = re.match(r"\S+", head)
            if word and INTERPRETER.search(word.group()):
                heads.append((n, head))

        for n, unit, fenced in _join_units(lines):
            # `_substitution_commands` alongside `_commands`, the pairing
            # `_bare_engine_units` makes and this loop did not. A `$( ... )` in
            # an assignment VALUE splits the enclosing command at its `(`, so
            # `PYTHONPATH="$(pwd)" python3 gen.py` arrived as two fragments and
            # the one holding `python3` began with the substitution's tail —
            # not an interpreter, no head recorded, the generated script run on
            # a stock python with nothing reported. Setting PYTHONPATH is
            # exactly what a generated script importing `agents.lib` needs, and
            # the unquoted spelling of the same line was caught, so the two
            # differed by a quote character. Judged against the map the unit is
            # ENTERED with, for the reason `_bare_engine_units` keeps one: the
            # rejoined reading has no position in the span order to be ordered
            # against.
            rejoined = _substitution_commands(unit)
            entering = dict(holders) if rejoined else None
            for command in _commands(unit):
                record(n, command, fenced, holders)
                holders.update(_assigned_values(command))
            for command in rejoined:
                record(n, command, fenced, entering)
        offenders = []
        for path, body_line, opened_at in generated:
            for n, head in heads:
                # The path in the SCRIPT position, normalised on both sides the
                # way `_script_path` normalises: `./gen.py` and `"gen.py"` are
                # the file the heredoc wrote, `regen.py` and `sub/gen.py` are
                # other files, and a name that merely OCCURS in the command —
                # inside a `-c` literal, as an argument to some other script —
                # is not the thing python executes.
                if _interpreter_operand(head) == path:
                    offenders.append(
                        f"{rel}:{n}: runs `{path}` on a bare interpreter — "
                        f"written by the heredoc opened at line {opened_at}, "
                        f"which reaches the engine at line {body_line}")
        return offenders


class TestTheLauncherAndTheGateAgreeOnWhichVenv:
    """Two files, one question. When they answered it differently the verdict
    was a refusal whose remedy was the command that produced it."""

    def test_the_default_venv_is_the_engine_tree_s(self, tmp_path, monkeypatch):
        """`run.sh` derives `<root>` from its own location and activates
        `<root>/.venv`. Resolving against the CWD instead meant that from any
        directory carrying its own `.venv`, the gate compared the engine's
        active virtualenv against a stranger's and blocked — with `rm -rf .venv`
        as the only escape offered, aimed at one that was never the problem.
        """
        monkeypatch.chdir(tmp_path)
        assert P._workspace_venv() == P._ENGINE_ROOT / ".venv", (
            "the gate resolves `.venv` somewhere the launcher does not")

    def test_the_two_derive_the_same_root(self):
        """The launcher's `$0/../..` and this module's `_HERE.parent.parent` are
        the same expression written in two languages; pin that they agree rather
        than trusting the pair to be edited together."""
        launcher = (REPO / "agents" / "lib" / "run.sh").read_text()
        assert 'dirname -- "$0")/../..' in launcher, (
            "run.sh no longer anchors to its own grandparent — if the anchor "
            "moved, `_ENGINE_ROOT` has to move with it or the two disagree "
            "again, and that disagreement is a refusal with no exit")
        assert P._ENGINE_ROOT == REPO

    def test_the_json_channel_carries_the_fourth_state(self, tmp_path):
        """`venv_broken` false, `venv_uncomparable` false and a non-empty
        `venv_error` is precisely `unactivated`, whose remedy is "run the
        launcher" — advised against a root that does not exist."""
        d = P._report_to_dict(
            P.check(modules=(), workspace=tmp_path / "not-a-real-root"))
        assert d["workspace_missing"] is True
        assert d["venv_broken"] is False and d["venv_uncomparable"] is False

    def test_the_banner_and_the_remedy_name_one_cause(self, tmp_path):
        """The banner blamed the interpreter check while the remedy two
        paragraphs below said nothing here is about an interpreter."""
        out = P.render_markdown(
            P.check(modules=(), workspace=tmp_path / "not-a-real-root"))
        assert "`--workspace` argument names a directory that does not exist" in out
        assert "could not verify the interpreter" not in out, (
            "one document, two stated causes")


class TestTheOkVerdictClaimsOnlyWhatItChecked:
    """The pass line is the one an agent reads before computing a return, so
    every clause in it has to be something this run established."""

    def test_it_names_only_the_gates_it_loaded(self):
        """`Gates available:` printed all four roles whenever the probe ran,
        and the probe runs off the math module alone — so a roster of one
        announced the staleness, capability and tie-out gates as present
        without loading any of them."""
        report = P.check(modules=(P.GATE_MODULES[0],))
        out = P.render_markdown(report)
        assert report.probe_ran and not report.blocking
        assert P.GATE_MODULES[0][1] in out
        for _name, role, _sym in P.GATE_MODULES[1:]:
            assert role not in out, (
                f"the verdict claims {role!r} is available on a run that never "
                f"loaded it")

    def test_the_full_roster_still_names_them_all(self):
        """The counterweight: the claim must survive when it is true, or the
        line has simply been deleted."""
        out = P.render_markdown(P.check())
        for _name, role, _sym in P.GATE_MODULES:
            assert role in out


class TestAWorkspaceWithNoVirtualenvAtAll:
    """The state the gate was silent in: nothing to compare against, so it
    certified whatever interpreter it happened to be running on."""

    def test_it_warns_rather_than_passing_silently(self, tmp_path, monkeypatch):
        """Clone the repo, install the four packages with `pip --user`, never
        create `.venv`: the ENOENT branch returned clean, the dependency probe
        imported the user-site copies, and the verdict read "OK … Pinned
        dependencies importable" while all twenty skills went on to run the
        return on the system interpreter — which AGENTS.md §7 forbids outright.

        The lock is hash-pinned because a republished parsing library changes
        what a document parses to, so unpinned versions are a wrong-number risk
        rather than an untidy one.
        """
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES", ())
        report = P.check(modules=(), workspace=tmp_path)
        assert report.no_workspace_venv
        out = P.render_markdown(report)
        assert "warning(s)" in out
        assert "No workspace virtualenv" in out
        assert "-m venv .venv" in out, "warned without saying how to fix it"

    def test_it_does_not_block(self, tmp_path, monkeypatch):
        """`tax-setup` Step 6 is what creates the environment, and every skill —
        that one included — opens with this gate. Blocking here would deadlock
        the only skill that fixes the condition, which is the trap
        `rebuild_remedy` already warns about in its own text.
        """
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES", ())
        report = P.check(modules=(), workspace=tmp_path)
        assert not report.blocking, (
            "the gate deadlocks the skill whose Step 6 creates the virtualenv")
        assert report.venv_error == "", (
            "a non-blocking condition wrote the blocking field")

    def test_a_present_venv_produces_no_such_warning(self, tmp_path,
                                                     monkeypatch):
        """The counterweight, and the one that would catch this firing on the
        ordinary case — which is every correctly set-up workspace."""
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text("VIRTUAL_ENV=x")
        (venv / "python3").write_text("")
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES", ())
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))
        report = P.check(modules=(), workspace=tmp_path)
        assert not report.no_workspace_venv


class TestAGateModuleThatWouldExitZeroDoingNothing:
    """IMPORTABLE is not EXECUTABLE, and the skills execute.

    Every other check in this module asks whether a module imports. No skill
    imports anything: `tax-compute` Step 12.7 runs
    `sh agents/lib/run.sh agents/lib/tie_out.py --workspace .` and acts on the
    exit code. A file cut between its last function and its
    `if __name__ == "__main__":` tail satisfies the file check, the import and
    the roster-symbol check — and running it exits 0 with no output, which that
    step reads as TIED OUT.
    """

    def _headless(self, tmp_path, monkeypatch, body):
        pkg = tmp_path / "headless"
        pkg.mkdir()
        _complete_package(pkg)
        (pkg / "tie_out.py").write_text(body)
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.setattr(P, "_HERE", pkg)
        return pkg

    def test_a_module_truncated_before_its_dispatch_blocks(self, tmp_path,
                                                           monkeypatch):
        """The middle band of truncation, and the one that looks healthiest.

        Zero bytes is caught by the roster symbol; a cut mid-statement raises
        SyntaxError at import. A cut at a statement BOUNDARY — 18 KB of
        tie_out.py's 107 KB, which is where a partial download or a stripped
        LFS pointer lands — passed both and was certified with the words
        "Gates available: … the intake-to-result tie-out gate".
        """
        self._headless(tmp_path, monkeypatch,
                       "def load_ledger(path):\n    return {}\n")
        report = P.check(modules=(("headless.tie_out", "the tie-out gate",
                                   "load_ledger"),))
        assert report.blocking, (
            "a gate whose CLI would exit 0 having done nothing was certified")
        assert "__main__" in report.failures[0].error
        remedy = P._remedy_text(report)
        assert "git checkout -- agents/lib/" in remedy, remedy
        assert "git clone" not in remedy, (
            "told to re-clone over a file that is present and imported")

    def test_such_a_file_really_exits_zero_when_run(self, tmp_path):
        """The half of the claim that lives outside this module.

        Without this the block above is asserted against a hazard nobody
        measured: the check is only worth its cost if the file it now refuses
        would otherwise have been read as a clean gate pass.
        """
        leaf = tmp_path / "tie_out.py"
        leaf.write_text("def load_ledger(path):\n    return {}\n")
        run = subprocess.run([sys.executable, str(leaf), "--workspace", "."],
                             capture_output=True, text=True)
        assert run.returncode == 0 and run.stdout == "" and run.stderr == "", (
            "the premise is wrong — a headless engine module does not read as "
            "a silent clean pass after all")

    def test_every_engine_module_carries_the_dispatch(self):
        """Non-vacuity, and AGENTS.md §5's CLI-entry-point claim pinned
        structurally for the first time.

        The DISPATCH, not the `_main` name §5 uses: seven of these modules
        (`tax_computations`, `secure_fs`, `tax_law_fetcher`, `parse_utils`,
        `irs_dates`, `archive`, `crypto_importer`) legitimately run their guard
        body inline or under another name, and a check keyed on the symbol
        would block every skill against seven correct files.

        The check above is only real if it passes on a correct clone; a module
        legitimately shipped without a `__main__` block would make it fire on
        every run, which is the gate-that-cries-wolf shape this repository has
        paid for before.
        """
        for name, role, _sym in P.ENGINE_MODULES:
            leaf = P._HERE / f"{name.rsplit('.', 1)[-1]}.py"
            assert P._MAIN_DISPATCH.search(leaf.read_text()), (
                f"{leaf.name} ({role}) has no `__main__` dispatch, so the gate "
                f"blocks every run of every skill")

    def test_an_indented_guard_is_not_a_dispatch(self, tmp_path, monkeypatch):
        """A guard inside a function never runs at module scope, so a file
        carrying only that one still exits 0 having done nothing — and a
        pattern that matched anywhere in the source would have called it
        executable."""
        self._headless(
            tmp_path, monkeypatch,
            "def load_ledger(path):\n"
            "    if __name__ == \"__main__\":\n"
            "        raise SystemExit(2)\n"
            "    return {}\n")
        report = P.check(modules=(("headless.tie_out", "the tie-out gate",
                                   "load_ledger"),))
        assert report.blocking, "an indented guard was read as a CLI entry point"

    def test_a_complete_module_passes(self, tmp_path, monkeypatch):
        """The counterweight for the check itself, at the level of one file."""
        self._headless(tmp_path, monkeypatch,
                       "def load_ledger(path):\n    return {}\n\n\n"
                       "if __name__ == \"__main__\":\n    raise SystemExit(2)\n")
        report = P.check(modules=(("headless.tie_out", "the tie-out gate",
                                   "load_ledger"),))
        assert not report.blocking, P.render_markdown(report)


class TestTheProbeRefusesANonFiniteAnswer:
    """NaN is a float, survives `float()` and `round()`, and then makes every
    comparison against it False — `>` included. AGENTS.md §8 states the rule
    for every parser in `agents/lib/`; the probe's own tolerance check was
    written the one way that NaN defeats silently."""

    class _Math:
        def __init__(self, value):
            self.value = value

        def compute_bracket_tax(self, income, brackets):
            return self.value, []

    def test_nan_is_not_computing_correctly(self):
        assert P._probe(self._Math(float("nan"))), (
            "a damaged engine returning NaN was certified as computing "
            "correctly — `abs(nan - expected) > 0.005` is False")

    def test_infinity_is_not_computing_correctly(self):
        assert "non-finite" in P._probe(self._Math(float("inf")))

    def test_the_right_answer_still_passes(self):
        """The counterweight: a guard that rejects everything is not a guard."""
        assert P._probe(self._Math(P._PROBE_EXPECTED)) == ""


class TestTheRemedyPastesIntoAShell:
    """`~/Documents/Tax Prep` is an ordinary place to keep a return, and every
    remedy in this module chains off a `cd` that names the workspace root."""

    def test_a_workspace_with_a_space_is_quoted(self, tmp_path, monkeypatch):
        root = tmp_path / "Tax Prep"
        root.mkdir()
        monkeypatch.chdir(tmp_path)          # so the `cd` is not suppressed
        step = P._cd_step(str(root))
        assert step, "the `cd` was dropped, leaving `rm -rf .venv` unanchored"
        # Parsed the way a shell parses it, not matched as text: `cd /x/Tax
        # Prep` is two arguments and fails, and the chain that follows it —
        # `rm -rf .venv`, `python3 -m venv .venv` — is what the `&&` then
        # withholds. The reader is blocked with no recovery path at all.
        assert shlex.split(step) == ["cd", str(root)], step

    def test_the_whole_rebuild_survives_the_quoting(self, tmp_path,
                                                    monkeypatch):
        root = tmp_path / "Tax Prep"
        root.mkdir()
        monkeypatch.chdir(tmp_path)
        steps = P._rebuild_steps(workspace=str(root))
        first = steps.strip().splitlines()[0].rstrip("&").strip()
        assert shlex.split(first) == ["cd", str(root)], steps

    def test_the_workspace_remedy_quotes_its_ls(self, tmp_path):
        root = tmp_path / "Tax Prep"
        text = P.workspace_remedy(str(root))
        line = [ln.strip() for ln in text.splitlines()
                if ln.strip().startswith("ls -ld")][0]
        assert shlex.split(line) == ["ls", "-ld", str(root)], line

    def test_an_ordinary_path_is_left_alone(self, tmp_path, monkeypatch):
        """Quoting that fires on every path churns the verdict every reader
        sees; `shlex.quote` leaves a plain path untouched, and this is what
        says so."""
        monkeypatch.chdir(tmp_path.parent)
        assert P._cd_step(str(tmp_path)) == f"cd {tmp_path}"


class TestTheJsonChannelCarriesTheNoVenvWarning:
    """Both channels report the same run, and a consumer that reads only JSON
    is the one an agent uses."""

    def test_the_state_and_its_remedy_are_exported(self, tmp_path,
                                                   monkeypatch):
        """`blocking: false`, an empty `venv_error` (this condition
        deliberately does not write that field) and an empty `remedy` are
        indistinguishable from a correctly set-up workspace — so a consumer
        went on to compute the return on the host interpreter, which AGENTS.md
        §7 forbids and the hash-pinned lock exists to prevent."""
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES", ())
        report = P.check(modules=(), workspace=tmp_path)
        d = P._report_to_dict(report)
        assert d["no_workspace_venv"] is True
        assert not d["blocking"]
        assert "-m venv .venv" in d["remedy"], (
            "a warning the markdown channel prints, with no way to act on it")

    def test_a_healthy_workspace_says_neither(self, tmp_path, monkeypatch):
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text("VIRTUAL_ENV=x")
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES", ())
        # The version comparison reads the RUNNER's site-packages, and this
        # test is about the no-virtualenv warning alone. A contributor one
        # patch release off the lock would otherwise fail it for a reason it
        # makes no claim about. `test_a_passing_report_carries_no_remedy` is
        # where the real environment is read.
        monkeypatch.setattr(P, "_drifted_dependencies", lambda: ((), ""))
        # Standing INSIDE that virtualenv, which is the state a `run.sh`
        # invocation produces — otherwise the report blocks as unactivated and
        # its remedy is a rebuild, which says nothing about this warning.
        monkeypatch.setattr(P.sys, "prefix", str(tmp_path / ".venv"))
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))
        d = P._report_to_dict(P.check(modules=(), workspace=tmp_path))
        assert d["no_workspace_venv"] is False
        assert d["remedy"] == ""

    def test_the_venv_is_not_built_twice_when_a_package_is_missing_too(
            self, tmp_path, monkeypatch):
        """A clone whose setup was never run is BOTH warnings at once, and the
        dependency remedy already opens by creating the environment. Emitting
        both handed a consumer `python3 -m venv .venv` twice with no way to
        tell whether that is one procedure or two — the duplication three
        comments in `_remedy_text` suppress on the blocking side."""
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES",
                            (("not_a_real_package_xyz", "not_a_real_package_xyz",
                              "PDF parsing", ("/tax-parse",)),))
        report = P.check(modules=(), workspace=tmp_path)
        remedy = P._report_to_dict(report)["remedy"]
        assert remedy.count("-m venv .venv") == 1, remedy
        assert "pip install -r requirements.txt" in remedy


class _FakeDist:
    """The two attributes `_drifted_dependencies` reads off an installed
    distribution. Fabricated rather than installed: the drift this test is
    about is a version nobody would deliberately put in a contributor's venv,
    and installing one to measure it would leave it there."""

    def __init__(self, name, version):
        self.metadata = {"Name": name}
        self.version = version


class TestTheLockIsMoreThanImportable:
    """IMPORTABLE is not PINNED, and the verdict claimed the stronger thing.

    The dependency probe imports four packages and the pass line then read
    "Pinned dependencies importable" — true, and not what the lock is for.
    AGENTS.md §7: `requirements.txt` pins the whole TRANSITIVE tree by hash
    because pdfplumber 0.11.10 shipped no pdfplumber code at all, only a
    `pdfminer.six` bump, and `pdfminer.six` is the engine document parsing
    actually runs on. A venv built by `pip install pdfplumber openpyxl
    reportlab defusedxml` imports all four and parses a taxpayer's documents on
    a resolution the suite has never run against.
    """

    def _installed(self, monkeypatch, *pairs):
        """A workspace holding the whole locked tree, with `pairs` substituted.

        The pairs alone are not an environment. `_drifted_dependencies` now
        reports a locked package that nothing provides, so a fake set of one
        distribution is a workspace missing eleven of them — every test here
        would then measure that instead of the substitution it names. Filling
        the rest in from the lock at their pinned versions is also the more
        honest fixture: the defect each test is about is ONE package wrong in an
        otherwise correct install, which is what a real drift looks like.
        """
        override = {P._canonical_dist(name) for name, _version in pairs}
        base = {}
        for match in P._LOCK_PIN.finditer((REPO / "requirements.txt").read_text()):
            name = match.group(1)
            if P._canonical_dist(name) not in override:
                base.setdefault(name, match.group(2))
        dists = [_FakeDist(n, v) for n, v in base.items()]
        dists += [_FakeDist(n, v) for n, v in pairs]
        monkeypatch.setattr(P.importlib.metadata, "distributions",
                            lambda: list(dists))

    def _locked(self, dist):
        match = re.search(rf"^{re.escape(dist)}==(\S+)",
                          (REPO / "requirements.txt").read_text(), re.MULTILINE)
        assert match, f"{dist} is no longer pinned in requirements.txt"
        return match.group(1)

    def test_a_drifted_transitive_version_is_named(self, monkeypatch):
        """The reported case: every top-level import succeeds, so the four
        probes are silent, and the parser underneath them is a version nobody
        tested."""
        self._installed(monkeypatch, ("pdfminer.six", "20200101"))
        drift, error = P._drifted_dependencies()
        assert error == ""
        assert len(drift) == 1, drift
        package, installed, pinned = drift[0]
        assert package == "pdfminer-six"
        assert installed == "20200101"
        assert pinned and pinned != installed, (
            "the drift record does not say what the lock expects, so the "
            "reader cannot tell which direction to move")

    def test_the_metadata_spelling_is_reconciled_with_the_lock(self,
                                                               monkeypatch):
        """The lock says `pdfminer-six`; the distribution's own metadata says
        `pdfminer.six`. Compared raw, the one transitive package whose
        republication actually changes what a document parses to is the one
        that matches no pin and is skipped in silence — and the check would
        look like it was working, because the other eleven still compare."""
        self._installed(monkeypatch,
                        ("pdfminer.six", self._locked("pdfminer-six")))
        assert P._drifted_dependencies() == ((), "")

    def test_every_pinned_direct_dependency_is_actually_compared(self,
                                                                 monkeypatch):
        """A vacuity guard on the lock parse. A regex that stopped matching —
        the `--hash=` continuation lines and the `; python_full_version` markers
        are both one character away from breaking it — yields an empty pin
        table, and an empty pin table reports NO drift for every version of
        everything, which is indistinguishable from a clean workspace."""
        self._installed(monkeypatch, *[(dist, "0.0.0-not-a-real-version")
                                       for dist, _i, _r, _s
                                       in P.RUNTIME_DEPENDENCIES])
        drift, error = P._drifted_dependencies()
        assert error == ""
        assert {d[0] for d in drift} == {P._canonical_dist(dist) for dist, _i,
                                         _r, _s in P.RUNTIME_DEPENDENCIES}

    def test_a_package_the_lock_does_not_name_is_not_drift(self, monkeypatch):
        """`pip`, `pytest` and the build tooling live in the same virtualenv
        and are pinned nowhere. Reporting them would make the warning fire on
        every correct workspace, which is how a gate gets reasoned past."""
        self._installed(monkeypatch, ("pytest", "1.0"), ("pip", "0.1"))
        assert P._drifted_dependencies() == ((), "")

    def test_one_package_pinned_twice_under_opposite_markers(self, tmp_path,
                                                             monkeypatch):
        """`--universal` legitimately pins the same name twice, under markers
        that select by interpreter version — and the two lines carry two
        DIFFERENT versions, exactly one of which this interpreter is meant to
        have. Both readings of that are wrong: comparing against whichever line
        came last reports the CI runner's own correct install as drift on one of
        the two Pythons the suite must pass on, and comparing against the two
        pooled into a SET accepts the other interpreter's version as locked —
        the same certification of an untested tree this check exists to remove,
        one layer in.

        Written against THIS interpreter's minor rather than a fixed 3.12: the
        constant made the test's meaning depend on which supported Python ran
        it, and on 3.10 or 3.11 (`MIN_PYTHON` is 3.10) it asserted the opposite
        of what it says.
        """
        here = "%d.%d" % sys.version_info[:2]
        (tmp_path / "requirements.txt").write_text(
            f"foo==1.0 ; python_version < '{here}' \\\n"
            "    --hash=sha256:" + "a" * 64 + "\n"
            f"foo==2.0 ; python_version >= '{here}' \\\n"
            "    --hash=sha256:" + "b" * 64 + "\n")
        monkeypatch.setattr(P, "_ENGINE_ROOT", tmp_path)

        # The version the applicable line names: clean, and not drift against
        # the excluded sibling.
        self._installed(monkeypatch, ("foo", "2.0"))
        assert P._drifted_dependencies() == ((), "")

        # The other line's version — pinned, present, and not the one this
        # interpreter is meant to have.
        self._installed(monkeypatch, ("foo", "1.0"))
        drift, error = P._drifted_dependencies()
        assert error == ""
        assert drift == (("foo", "1.0", "2.0"),), (
            "a version the lock pins only for an interpreter this is not read "
            "as the locked tree, so the parse boundary opens a taxpayer's "
            "documents on a stack the suite never ran here")

        # A version neither line names, reported against the applicable pin
        # ALONE — a record naming both tells a reader on this interpreter to
        # install the one meant for the other.
        self._installed(monkeypatch, ("foo", "3.0"))
        drift, _error = P._drifted_dependencies()
        assert drift == (("foo", "3.0", "2.0"),)

    def test_an_unreadable_lock_is_not_reported_as_no_drift(self, tmp_path,
                                                            monkeypatch):
        """The fail-open this module removes wherever it finds one: an empty
        drift list means every pin MATCHED, and a lock that could not be read
        produced exactly that. `agents/lib/` copied into a tree with no
        `requirements.txt` is the shape this whole file exists for."""
        monkeypatch.setattr(P, "_ENGINE_ROOT", tmp_path)
        drift, error = P._drifted_dependencies()
        assert drift == ()
        assert error, "a comparison that never ran reported as a clean one"

    def test_a_lock_that_parses_to_nothing_is_not_reported_as_no_drift(
            self, tmp_path, monkeypatch):
        """The same fail-open one step in, and the likelier of the two.

        A lock that READS fine and matches no pin is what a changed `uv pip
        compile` output format, or one character in `_LOCK_PIN`, produces — and
        an empty pin table reports every installed version as correct, for
        every package, in silence. `test_every_pinned_direct_dependency_is_
        actually_compared` catches that against the SHIPPED file; this catches
        it against a file whose format has moved on, which is the day it
        happens.
        """
        (tmp_path / "requirements.txt").write_text(
            "# This file was autogenerated by uv via the following command:\n"
            "#    uv pip compile requirements.in --generate-hashes\n"
            "    --hash=sha256:" + "c" * 64 + "\n")
        monkeypatch.setattr(P, "_ENGINE_ROOT", tmp_path)
        self._installed(monkeypatch, ("pdfminer.six", "20200101"))
        drift, error = P._drifted_dependencies()
        assert drift == ()
        assert error, (
            "a pin table that came out empty reported every installed version "
            "as matching the lock")

    def test_the_verdict_warns_and_the_pass_line_is_withdrawn(self, tmp_path,
                                                              monkeypatch):
        """Both halves on one report: the OK line stops claiming the locked
        versions, and the warning names the package, the two versions and the
        command that fixes it."""
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES", ())
        monkeypatch.setattr(P, "_drifted_dependencies",
                            lambda: ((("pdfminer-six", "20200101",
                                       "20260107"),), ""))
        monkeypatch.setattr(P.sys, "prefix", str(tmp_path / ".venv"))
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text("VIRTUAL_ENV=x")
        report = P.check(modules=(), workspace=tmp_path)

        assert not report.blocking, (
            "blocking on a version comparison refuses /tax-calendar and every "
            "other skill that opens no document — the cry-wolf shape that "
            "teaches an agent to reason past the gates that matter")
        out = P.render_markdown(report)
        assert "at the locked versions" not in out, (
            "the pass line still claims the lock over a workspace that is off "
            "it")
        assert "warning(s)" in out
        assert "pdfminer-six" in out and "20200101" in out and "20260107" in out
        assert "pip install -r requirements.txt" in out, (
            "warned without saying how to fix it")

    def test_the_json_channel_carries_it_too(self, tmp_path, monkeypatch):
        """A consumer that reads only JSON is the one an agent uses, and
        `blocking: false` with an empty remedy is what a healthy workspace
        looks like."""
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES", ())
        monkeypatch.setattr(P, "_drifted_dependencies",
                            lambda: ((("pdfminer-six", "20200101",
                                       "20260107"),), ""))
        d = P._report_to_dict(P.check(modules=(), workspace=tmp_path))
        assert d["drifted_dependencies"] == [
            {"package": "pdfminer-six", "installed": "20200101",
             "pinned": "20260107"}]
        assert d["dependency_lock_error"] == ""
        assert "pip install -r requirements.txt" in d["remedy"]

    def test_the_lock_error_reaches_both_channels(self, tmp_path, monkeypatch):
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES", ())
        monkeypatch.setattr(P, "_drifted_dependencies",
                            lambda: ((), "requirements.txt could not be read"))
        report = P.check(modules=(), workspace=tmp_path)
        assert not report.blocking
        assert "NOT verified" in P.render_markdown(report)
        d = P._report_to_dict(report)
        assert d["dependency_lock_error"], (
            "the JSON channel reports no drift for a check that never ran")
        assert d["remedy"]

    def test_the_install_step_is_printed_once_when_both_warnings_fire(
            self, tmp_path, monkeypatch):
        """A clone whose setup was never run is missing packages AND off the
        lock, and the same `pip install -r requirements.txt` fixes both.
        Printing it twice is the duplication three comments in `_remedy_text`
        suppress on the blocking side."""
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES",
                            (("not_a_real_package_xyz",
                              "not_a_real_package_xyz", "PDF parsing",
                              ("/tax-parse",)),))
        monkeypatch.setattr(P, "_drifted_dependencies",
                            lambda: ((("pdfminer-six", "20200101",
                                       "20260107"),), ""))
        # A workspace WITH a virtualenv, standing inside it. Without that the
        # no-venv warning fires as well and carries its own creation-and-install
        # step, so the count would measure that pair instead of this one.
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text("VIRTUAL_ENV=x")
        monkeypatch.setattr(P.sys, "prefix", str(tmp_path / ".venv"))
        monkeypatch.setattr(P.sys, "base_prefix", str(tmp_path / "base"))
        out = P.render_markdown(P.check(modules=(), workspace=tmp_path))
        assert out.count("pip install -r requirements.txt") == 1, out
        assert "pdfminer-six" in out

    def test_a_blocking_report_cannot_carry_a_version_verdict(self):
        """`check()` skips the comparison whenever a blocking cause is already
        known — the versions would be read off an interpreter the same report
        disclaims. Stated as an invariant so a future edit that starts
        computing it anyway gets told what else has to change."""
        with pytest.raises(ValueError, match="BLOCKING"):
            P.PreflightReport(python_version="3.9.0", python_ok=False,
                              failures=(), probe_error="",
                              drifted_dependencies=(("foo", "1.0", "2.0"),))
        with pytest.raises(ValueError, match="BLOCKING"):
            P.PreflightReport(python_version="3.9.0", python_ok=False,
                              failures=(), probe_error="",
                              dependency_lock_error="unreadable")


class TestALockedPackageThatIsSimplyNotThere:
    """The other direction of the same comparison, and it was not made.

    `_drifted_dependencies` walked the INSTALLED distributions and compared the
    ones the lock also named. A locked package that nothing provides matches
    nothing, is compared against nothing, and came back as a clean tree — under
    a verdict that then certified "at the locked versions". The four import
    probes do not cover it either: they import the four DIRECT dependencies, and
    what can go missing without an import failing is the transitive tail nothing
    touches until it needs it — `cryptography` only on an encrypted PDF,
    `pillow` and `pypdfium2` only on a page render.

    Every test here builds its own lock, because the assertion is about names
    the lock holds and the environment does not, and against the shipped file
    that is a statement about the contributor's venv rather than about the code.
    """

    def _lock(self, tmp_path, monkeypatch, *lines):
        (tmp_path / "requirements.txt").write_text("".join(
            f"{line} \\\n    --hash=sha256:{'a' * 64}\n" for line in lines))
        monkeypatch.setattr(P, "_ENGINE_ROOT", tmp_path)

    def _installed(self, monkeypatch, *pairs):
        monkeypatch.setattr(P.importlib.metadata, "distributions",
                            lambda: [_FakeDist(n, v) for n, v in pairs])

    def test_a_locked_package_nothing_provides_is_reported(self, tmp_path,
                                                            monkeypatch):
        """The finding, at its smallest: two pins, one installed."""
        self._lock(tmp_path, monkeypatch, "foo==1.0", "bar==2.0")
        self._installed(monkeypatch, ("foo", "1.0"))
        drift, error = P._drifted_dependencies()
        assert error == ""
        assert drift == (("bar", None, "2.0"),), (
            "a pin the environment does not satisfy at all read as a match")

    def test_absent_is_carried_as_a_state_not_as_a_word(self, tmp_path,
                                                        monkeypatch):
        """`None` in the version slot, not the string the renderer prints.

        A consumer separating "wrong version" from "not installed" would
        otherwise have to match on prose it does not control — the failure mode
        every `venv_broken`/`venv_uncomparable` comment in this module is about.
        """
        self._lock(tmp_path, monkeypatch, "foo==1.0")
        self._installed(monkeypatch)
        (record,), _error = P._drifted_dependencies()
        assert record[1] is None
        out = P.render_markdown(P.PreflightReport(
            python_version="3.12.0", python_ok=True, failures=(),
            probe_error="", drifted_dependencies=(record,)))
        assert "NOT INSTALLED" in out and "None" not in out, (
            "the markdown channel printed the sentinel at the reader")

    def test_a_pin_this_interpreter_is_excluded_from_is_not_reported(
            self, tmp_path, monkeypatch):
        """`--universal` locks name packages this interpreter must NOT have.

        `cffi ; platform_python_implementation != 'PyPy'` is absent on PyPy
        because it is supposed to be. Reporting it would raise an alarm the
        printed remedy cannot clear — `pip install -r requirements.txt` will not
        install a pin whose marker excludes the machine — and a warning that
        survives its own fix is the one an agent learns to skip.
        """
        impl = P.platform.python_implementation()
        self._lock(tmp_path, monkeypatch,
                   f"foo==1.0 ; platform_python_implementation != '{impl}'")
        self._installed(monkeypatch)
        assert P._drifted_dependencies() == ((), "")

    def test_a_pin_this_interpreter_is_selected_for_is_reported(self, tmp_path,
                                                                monkeypatch):
        """The counterweight. A marker evaluator that answered False to
        everything would pass the test above and check nothing at all."""
        impl = P.platform.python_implementation()
        self._lock(tmp_path, monkeypatch,
                   f"foo==1.0 ; platform_python_implementation == '{impl}'")
        self._installed(monkeypatch)
        drift, _error = P._drifted_dependencies()
        assert drift == (("foo", None, "1.0"),)

    def test_a_version_marker_is_read_as_a_release_not_as_a_string(
            self, tmp_path, monkeypatch):
        """`'3.14' >= '3.9'` is False as text and True as a release.

        The comparison that decides whether a pin applies is a version
        comparison, and every interpreter this suite runs on has a minor of 10
        or more (AGENTS.md §7 pins CI to 3.12 and 3.14, and `MIN_PYTHON` is
        3.10) — so a string compare is not merely imprecise here, it inverts.
        """
        assert sys.version_info[1] >= 10, (
            "this test discriminates only on an interpreter whose minor sorts "
            "below '9' as text")
        self._lock(tmp_path, monkeypatch, "foo==1.0 ; python_version >= '3.9'")
        self._installed(monkeypatch)
        drift, error = P._drifted_dependencies()
        assert error == ""
        assert drift == (("foo", None, "1.0"),), (
            "the marker was compared as text, so a pin that applies to every "
            "supported interpreter read as one that applies to none")

    # The four shapes that make a marker undecidable, one per reason the
    # evaluator has for refusing: the structural ones it does not parse, a
    # variable it cannot answer, a version literal with no dotted-integer
    # reading, and an ordering comparison on an opaque string. Parametrised
    # because the first exits at the structural guard and the other three at the
    # term — one case would leave whichever path it misses untested, and an
    # evaluator that answered True at that point is the original defect.
    @pytest.mark.parametrize("marker", [
        "extra == 'x' or sys_platform == 'nope'",
        "platform_release == '99'",
        "python_full_version < '3.99.0rc1'",
        "sys_platform > 'linux'",
    ])
    def test_a_marker_this_evaluator_cannot_read_is_not_reported_as_clean(
            self, tmp_path, monkeypatch, marker):
        """The fail-open one layer down.

        An evaluator that guessed at an unfamiliar marker would put the
        certification back exactly where this pass found it — claiming the lock
        over pins it never examined.
        """
        self._lock(tmp_path, monkeypatch, f"foo==1.0 ; {marker}")
        self._installed(monkeypatch)
        drift, error = P._drifted_dependencies()
        assert drift == (), (
            "an undecidable marker was answered as if it selected this machine")
        assert "foo" in error, (
            "a pin whose applicability was never decided reported as a clean "
            "comparison")

    def test_a_definite_exclusion_outranks_an_undecidable_sibling(
            self, tmp_path, monkeypatch):
        """`and` is settled by any False, whatever the rest of the chain says.

        Ordering these the other way turns `cffi ; implementation_name != 'PyPy'
        and <something new>` into an undecidable warning on PyPy, where the pin
        is plainly excluded — a permanent alarm on a machine that is correct.
        """
        impl = P.platform.python_implementation()
        self._lock(tmp_path, monkeypatch,
                   f"foo==1.0 ; platform_python_implementation != '{impl}' "
                   f"and platform_release == '99'")
        self._installed(monkeypatch)
        assert P._drifted_dependencies() == ((), "")

    def test_an_installed_package_is_never_undecidable(self, tmp_path,
                                                       monkeypatch):
        """A marker cannot make an installed package un-installed, so the
        undecidable channel must stay silent about one — otherwise the alarm
        fires on a workspace that is exactly right, which is the whole cost
        this three-answer design is paying to avoid."""
        self._lock(tmp_path, monkeypatch,
                   "foo==1.0 ; extra == 'x' or sys_platform == 'nope'")
        self._installed(monkeypatch, ("foo", "1.0"))
        assert P._drifted_dependencies() == ((), "")

    def test_every_marker_in_the_shipped_lock_is_decidable(self):
        """The evaluator is deliberately small, so this is what keeps it big
        enough. A regenerated lock that reaches for `or`, parentheses or a
        variable it does not know turns CI red on the commit that changes the
        lock — when a human is already reading the lock diff — rather than
        degrading a taxpayer's workspace into an unexplained warning later.
        """
        markers = [(match.group(1), (match.group(3) or "").strip())
                   for match in P._LOCK_PIN.finditer(
                       (REPO / "requirements.txt").read_text())]
        assert markers, "the lock parsed to no pins at all"
        undecidable = [name for name, marker in markers
                       if P._marker_applies(marker) is None]
        assert not undecidable, (
            f"requirements.txt now carries a marker `_marker_applies` cannot "
            f"evaluate, on {undecidable} — extend it, or these pins stop being "
            f"checked for absence")

    def test_both_halves_of_the_verdict_are_printed_when_both_fire(
            self, tmp_path, monkeypatch):
        """They were an if/else. A report carrying an unevaluable marker
        alongside real drift printed the marker sentence and swallowed every
        package name — the actionable half — under a headline saying nothing
        was verified."""
        monkeypatch.setattr(P, "RUNTIME_DEPENDENCIES", ())
        monkeypatch.setattr(P, "_drifted_dependencies",
                            lambda: ((("pdfminer-six", None, "20260107"),),
                                     "markers not evaluated on foo"))
        report = P.check(modules=(), workspace=tmp_path)
        out = P.render_markdown(report)
        assert "pdfminer-six" in out and "NOT INSTALLED" in out
        assert "NOT verified" in out and "foo" in out
        d = P._report_to_dict(report)
        assert d["drifted_dependencies"] == [
            {"package": "pdfminer-six", "installed": None,
             "pinned": "20260107"}]
        assert d["dependency_lock_error"]


class TestTheInstalledVersionIsComparedAgainstThePinThatApplies:
    """Which pin governs is a question the marker answers, and it was not asked.

    `test_one_package_pinned_twice_under_opposite_markers` covers the shape a
    `--universal` lock actually produces. These are the three states that shape
    leaves over once the versions are no longer pooled — a lock naming two
    versions that both apply, an installed package under a marker this
    evaluator cannot read, and one under a marker that definitely excludes this
    machine. Each is a place where "compare against something" and "compare
    against the right thing" part company, and the first two are the ones a
    silent answer would certify.
    """

    def _lock(self, tmp_path, monkeypatch, *lines):
        (tmp_path / "requirements.txt").write_text("".join(
            f"{line} \\\n    --hash=sha256:{'a' * 64}\n" for line in lines))
        monkeypatch.setattr(P, "_ENGINE_ROOT", tmp_path)

    def _installed(self, monkeypatch, *pairs):
        monkeypatch.setattr(P.importlib.metadata, "distributions",
                            lambda: [_FakeDist(n, v) for n, v in pairs])

    def test_two_pins_that_both_apply_leave_nothing_to_compare_against(
            self, tmp_path, monkeypatch):
        """No install satisfies both, so whichever the check accepted would be
        one of two picked arbitrarily — the pooled-set fail-open, surviving in
        the one case where the pool is genuinely ambiguous. `uv pip compile`
        does not emit this; a hand-edited lock does, and it must not read as a
        certified tree."""
        impl = P.platform.python_implementation()
        self._lock(tmp_path, monkeypatch,
                   f"foo==1.0 ; platform_python_implementation == '{impl}'",
                   f"foo==2.0 ; implementation_name == '{sys.implementation.name}'")
        self._installed(monkeypatch, ("foo", "1.0"))
        drift, error = P._drifted_dependencies()
        assert drift == (), (
            "one of two contradictory pins was picked as the answer")
        assert "foo" in error, (
            "a lock that names two versions for this interpreter reported as a "
            "clean comparison")

    def test_an_unreadable_marker_over_an_unnamed_version_is_not_answered(
            self, tmp_path, monkeypatch):
        """Installed at a version no readable pin names. Whether that is drift
        depends on the marker the evaluator could not read, so it goes to the
        undecided channel — reported, and not answered either way. Called
        drift, it would be an alarm the printed remedy cannot clear; called
        clean, it is the certification of an unexamined pin."""
        self._lock(tmp_path, monkeypatch, "foo==1.0 ; platform_release == '99'")
        self._installed(monkeypatch, ("foo", "2.0"))
        drift, error = P._drifted_dependencies()
        assert drift == ()
        assert "foo" in error

    def test_a_package_this_interpreter_is_not_locked_for_is_left_alone(
            self, tmp_path, monkeypatch):
        """The excluded pin, from the installed side. PyPy ships its own
        `cffi` while the lock pins one only for everything else: there is no
        version to compare against here, and `pip install -r requirements.txt`
        would neither install nor remove it. Silence, on the same rule that
        keeps an unpinned `pytest` out of the drift list."""
        impl = P.platform.python_implementation()
        self._lock(tmp_path, monkeypatch,
                   f"foo==1.0 ; platform_python_implementation != '{impl}'")
        self._installed(monkeypatch, ("foo", "9.9"))
        assert P._drifted_dependencies() == ((), "")


class TestThePackageInitialiserIsCertified:
    """The one engine file whose absence every other check reads as health.

    Python treats `agents/lib/` with no `__init__.py` as a NAMESPACE package,
    so on the partial copy this whole module exists to detect, every sibling
    still imports, every roster entry still passes, and the host-interpreter
    guard is simply gone. Reported by Codex against PR #82 and fixed there: the
    enforcement that replaced a documentation audit could itself go missing
    behind a green verdict.
    """

    def _fake_here(self, tmp_path, monkeypatch, initialiser):
        pkg = tmp_path / "lib"
        pkg.mkdir()
        if initialiser is not None:
            (pkg / "__init__.py").write_text(initialiser)
        monkeypatch.setattr(P, "_HERE", pkg)
        return pkg

    def test_a_missing_initialiser_blocks(self, tmp_path, monkeypatch):
        self._fake_here(tmp_path, monkeypatch, None)
        error, _redaction, missing, _fault = P._guard_error()
        assert "namespace package" in error.lower()
        assert missing is True, (
            "a file that is GONE is the missing-file class, which is what "
            "selects the re-clone remedy rather than the truncated-file one")
        assert P.check(modules=()).blocking

    def test_an_emptied_initialiser_blocks(self, tmp_path, monkeypatch):
        """The coarser cut, and the one that imports perfectly."""
        self._fake_here(tmp_path, monkeypatch, "")
        error, _redaction, _missing, _fault = P._guard_error()
        assert "EMPTY" in error
        assert P.check(modules=()).blocking

    def test_an_initialiser_that_is_only_a_comment_blocks(self, tmp_path,
                                                          monkeypatch):
        """What this file WAS before the guard, byte for byte.

        A revert is the likeliest way the guard disappears, and it leaves a
        non-empty file — so a size check alone would certify it.

        THE VERDICT, not the size. This asserted `st_size` and nothing else:
        it never called `_guard_error`, so the revert case it is named for was
        covered only incidentally, by `_wiring_error`'s fall-through branch —
        and a later refactor that weakened that branch would leave
        `git checkout main -- agents/lib/__init__.py` passing preflight while
        this test stayed green on the bytes. Reported against PR #82. The
        import-time probe is what catches it: the symbol probe reads the
        IMPORTED package rather than this file, so only executing the file can
        see that nothing in it refuses.
        """
        self._fake_here(tmp_path, monkeypatch,
                        "# Tax preparation agent utilities\n")
        assert (P._HERE / "__init__.py").stat().st_size, (
            "the fixture is empty, so the EMPTY branch would answer this and "
            "the revert case would go unmeasured")
        error, _redaction, _missing, fault = P._guard_error()
        assert "never CALLED at import" in error
        assert fault == "", (
            "a reverted initialiser is an engine defect, and routing it to the "
            "environment channel would downgrade it to a warning")
        assert P.check(modules=()).blocking

    def test_a_neutered_guard_blocks(self, monkeypatch):
        """Importable is not correct, for the reason `_probe` exists.

        A guard reduced to `return ""` — a half-applied merge, a debugging edit
        left in — passes an existence check and certifies nothing.
        """
        monkeypatch.setattr(P.importlib, "import_module",
                            lambda name: _Stub(lambda **kw: ""))
        error, _redaction, _missing, _fault = P._guard_error()
        assert "certifying nothing" in error

    def test_a_guard_that_refuses_a_venvless_workspace_blocks(self, monkeypatch):
        """The other direction, and it is not symmetrical noise.

        A guard that fires with no `.venv` present blocks CI and every clone
        before `tax-setup` Step 6 — a gate firing on a correct state, which
        teaches its reader to reason past it.
        """
        monkeypatch.setattr(P.importlib, "import_module",
                            lambda name: _Stub(lambda **kw: "always"))
        error, _redaction, _missing, _fault = P._guard_error()
        assert "no `.venv`" in error

    def test_a_guard_that_raises_is_reported_not_propagated(self, monkeypatch):
        def boom(**kwargs):
            raise RuntimeError("probe exploded")
        monkeypatch.setattr(P.importlib, "import_module",
                            lambda name: _Stub(boom))
        error, redaction, _missing, _fault = P._guard_error()
        assert "RuntimeError" in error and "probe exploded" in error
        assert redaction is True

    def test_a_package_that_exits_on_import_is_reported(self, monkeypatch):
        """`SystemExit` is not an `Exception`, and this module's whole subject
        is a package that exits on import."""
        def exiting(name):
            raise SystemExit(2)
        monkeypatch.setattr(P.importlib, "import_module", exiting)
        error, _redaction, _missing, _fault = P._guard_error()
        assert "SystemExit" in error

    def test_the_probe_does_not_take_the_preflight_exemption(self):
        """The probe would be vacuous if it did.

        The guard exempts preflight by `sys.orig_argv`, and this check runs
        INSIDE preflight — so handing it the real argv would return "" in both
        directions and the probe would measure nothing while reading green.
        """
        source = (REPO / "agents" / "lib" / "preflight.py").read_text()
        body = source[source.index("def _guard_error"):
                      source.index("def _wiring_error(")]
        assert ("main_file=None" in body and '"-c"' in body
                and "orig_argv=" in body), (
            "the known-answer probe no longer passes ALL THREE of the inputs "
            "the exemption reads — it would take the exemption and read clean "
            "against any guard at all. `main_file` is the one that made this "
            "concrete rather than theoretical: with only the argv overridden "
            "the probe read the real `__main__.__file__`, saw preflight, and "
            "certified a stub")

    def _initialiser(self, replacement=None):
        """The REAL initialiser, optionally damaged one way.

        Written against the shipped file rather than a hand-made stand-in
        because the thing under test is whether that file, executed, refuses —
        a stand-in would only re-assert the shape this test was written from.
        """
        source = (REPO / "agents" / "lib" / "__init__.py").read_text()
        if replacement is None:
            return source
        old, new = replacement
        assert old in source, f"the initialiser no longer contains {old!r}"
        return source.replace(old, new)

    def test_a_guard_that_is_defined_but_never_called_blocks(self, tmp_path,
                                                             monkeypatch):
        """The finding this probe answers, reported against PR #82.

        Truncating the initialiser after its last `return` leaves a valid
        module whose `host_interpreter_defect` still returns both expected
        values — so every check above passes — while removing the module-level
        call that is the only thing enforcing the rule. The package then
        imports cleanly on any interpreter and a partial copy in exactly that
        state computes on the host behind a green preflight.
        """
        source = self._initialiser()
        truncated = source[:source.index("if _defect :=")]
        self._fake_here(tmp_path, monkeypatch, truncated)
        error, _redaction, _missing, _fault = P._guard_error()
        assert "never CALLED at import" in error
        assert P.check(modules=()).blocking

    def test_a_guard_whose_call_is_neutered_blocks(self, tmp_path, monkeypatch):
        """And this is why the check EXECUTES the file rather than reading it.

        The call is still on the page here, so a source scan for it — the
        cheaper check, and what `_cli_dispatch_error` does for `__main__` —
        certifies this file. Only running it can tell the two apart.
        """
        self._fake_here(tmp_path, monkeypatch, self._initialiser(
            ("if _defect := host_interpreter_defect():",
             "if False and host_interpreter_defect():")))
        error, _redaction, _missing, _fault = P._guard_error()
        assert "never CALLED at import" in error

    def test_a_guard_that_exits_with_the_wrong_status_blocks(self, tmp_path,
                                                             monkeypatch):
        """Exit 2 is preflight's "blocked" and the skills branch on it, so a
        refusal that exits 1 reads as "could not run" and a 0 as success."""
        self._fake_here(tmp_path, monkeypatch,
                        self._initialiser(("sys.exit(2)", "sys.exit(1)")))
        error, _redaction, _missing, _fault = P._guard_error()
        assert "status 1" in error and "does not read as blocked" in error

    def test_the_probe_leaves_orig_argv_as_it_found_it(self, tmp_path,
                                                       monkeypatch):
        """It overrides `sys.orig_argv` to escape the preflight exemption, and
        the process carries on afterwards — a synthetic argv left behind would
        silently change what the guard decides for the rest of this run."""
        self._fake_here(tmp_path, monkeypatch, self._initialiser())
        before = list(sys.orig_argv)
        assert P._guard_error() == ("", False, False, "")
        assert sys.orig_argv == before

    def test_the_probe_does_not_shadow_the_real_package(self, tmp_path,
                                                        monkeypatch):
        """It executes a COPY of the initialiser. Registering that copy under
        any name would leave it standing in for the package that preflight
        keeps importing after this returns."""
        self._fake_here(tmp_path, monkeypatch, self._initialiser())
        before = set(sys.modules)
        P._guard_error()
        assert set(sys.modules) - before == set()

    def test_a_healthy_tree_is_certified(self):
        assert P._guard_error() == ("", False, False, "")

    def _no_space(self, *args, **kwargs):
        raise OSError(28, "No space left on device")

    def test_a_probe_that_cannot_build_its_tree_warns_rather_than_blocks(
            self, monkeypatch):
        """A full `$TMPDIR` is not an engine defect, and it used to read as one.

        Both probes build a throwaway tree, and every step of that build used
        to arrive through the same `except Exception` as a guard that RAISED.
        So ENOSPC on `/tmp`, or EACCES under a sandboxed `$TMPDIR`, reported
        `engine_missing` on a healthy clone in the correct virtualenv — which
        blocks the hard gate of every taxpayer-facing skill, and answers it
        with `import_failure_remedy`: re-clone the engine and reinstall the
        lock, for a disk with no space on it, with the OS error redacted away.
        Reported against PR #82.

        The bias argument is AGENTS.md §4's: what is lost is certification of
        the guard, not the guard — the package imported a few lines above, so
        the refusal already ran for this process. A gate that fires on a state
        the engine is not in is what teaches an agent to reason past the next
        one.
        """
        monkeypatch.setattr(P.tempfile, "mkdtemp", self._no_space)
        error, _redaction, missing, fault = P._guard_error()
        assert error == "" and missing is False, (
            "an environment fault produced an engine verdict")
        assert "No space left on device" in fault

        report = P.check(modules=())
        assert not report.blocking, (
            "a temp directory that cannot be written blocks every skill")
        assert report.failures == ()
        assert report.guard_probe_error
        rendered = P.render_markdown(report)
        assert "not CERTIFIED" in rendered
        assert "git checkout" not in rendered, (
            "the re-clone remedy is the one this split exists to stop offering")

    def test_a_probe_that_cannot_create_its_venv_warns(self, monkeypatch):
        """The second half of the same tree, and its own OSError."""
        real = P.os.mkdir

        def refuse(path, *args, **kwargs):
            if str(path).endswith(".venv"):
                raise PermissionError(13, "Permission denied")
            return real(path, *args, **kwargs)

        monkeypatch.setattr(P.os, "mkdir", refuse)
        error, _redaction, _missing, fault = P._guard_error()
        assert error == ""
        assert "Permission denied" in fault

    def test_a_guard_that_raises_is_still_an_engine_verdict(self, monkeypatch):
        """The control on the split above. Routing OSError to the environment
        channel is only right for the SETUP; a guard that raises one is a
        broken guard, and downgrading that to a warning would be a fail-open
        reached through the fix for a fail-closed."""
        def boom(**kwargs):
            raise OSError(5, "Input/output error")
        monkeypatch.setattr(P.importlib, "import_module",
                            lambda name: _Stub(boom))
        error, redaction, _missing, fault = P._guard_error()
        assert "OSError" in error and redaction is True
        assert fault == ""

    def test_the_wiring_probe_reports_a_setup_failure_without_raising(
            self, tmp_path, monkeypatch):
        """And the `finally` that restores `sys.orig_argv` cannot raise.

        The assignment used to sit AFTER the temp-tree setup while the
        `finally` held an unconditional `del sys.orig_argv`, so an OSError
        during that setup, on an interpreter with no `orig_argv` to begin with,
        raised AttributeError from the `finally` and replaced the handled
        exception. That propagates to `_main`'s catch-all, which prints
        `ERROR: AttributeError` and returns 1 — "could not run" — instead of
        the exit-2 report carrying the MIN_PYTHON remedy, on precisely the
        pre-3.10 interpreter preflight exists to diagnose. Reported against
        PR #82; every assignment now happens before the first filesystem call.
        """
        self._fake_here(tmp_path, monkeypatch, self._initialiser())
        monkeypatch.delattr(sys, "orig_argv", raising=False)
        monkeypatch.setattr(P.tempfile, "mkdtemp", self._no_space)
        error, _redaction, _missing, fault = P._wiring_error(
            P._HERE / "__init__.py")
        assert error == ""
        assert "No space left on device" in fault
        assert not hasattr(sys, "orig_argv"), (
            "the probe left a synthetic argv behind on an interpreter that has "
            "none, which switches the guard's own pre-3.10 fallback off for "
            "the rest of this process")

    def test_the_probe_leaves_main_file_as_it_found_it(self, tmp_path,
                                                       monkeypatch):
        """It hides `__main__.__file__` to escape the preflight exemption —
        the copy it executes reads that attribute rather than taking it as an
        argument — and the process carries on afterwards."""
        self._fake_here(tmp_path, monkeypatch, self._initialiser())
        main = sys.modules["__main__"]
        before = getattr(main, "__file__", None)
        assert P._guard_error() == ("", False, False, "")
        assert getattr(main, "__file__", None) == before


class _Stub:
    """A stand-in for the imported package, exposing one guard function."""

    def __init__(self, guard):
        self.host_interpreter_defect = guard
