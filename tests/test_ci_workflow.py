"""What CI runs, and on which events — `.github/workflows/ci.yml` (W6).

The workflow ran on `pull_request` alone, and every gap this module pins follows
from widening that:

  - `github.event.pull_request.base.sha` is EMPTY on every other event, so the
    inline range expression would have handed the PII scanner `...HEAD` the
    moment a push or schedule trigger existed. The range now comes from
    `scripts/ci-pii-range.sh`, which is a script rather than a workflow
    expression precisely so it can be run here.
  - the test runner was installed by a bare `pip install pytest`, pinning
    nothing, while the runtime dependencies are hash-locked.
  - Windows behaviour is claimed in 27 places across `secure_fs` and `run.sh`,
    and one of its branches carries `# pragma: no cover - POSIX CI`.

PARSED, not read as text — which it was until the floor guard needed STRUCTURAL
questions answered of this file: which job binds `python-version` to the matrix,
whether the matrix excludes a leg, which step carries which condition. Nine
spellings of a wrong pin walked past the patterns that stood in for those
questions, every one of them valid YAML. PyYAML is in `requirements-test.in` for
this and is deliberately not in the runtime lock, where a parser bought for a
test does not belong; the two locks share no dependency and a test keeps it so.
The prose and the lock headers are still read as text, having no structure to
ask, and SO ARE ABOUT TWENTY ASSERTIONS ABOUT `ci.yml` ITSELF further down this
file — the range guard, the install sweep, the Windows job. Those ask whether a
string is present, which is a question the parse does not make easier. They go
through `_ci_code` so that a commented-out step cannot answer them; what they do
not do is ask the document about its own structure. This module is MIXED, and
saying so beats either blanket claim it has carried.

NOT SELECTED BY THE SKILL-INVARIANTS HOOK, and stated so nobody assumes it is:
the hook runs seven modules and this is not one of them, the same standing
`test_pii_content_scan.py` already has. Editing `ci.yml` therefore turns this red
in CI rather than at tool time. Adding it to the hook would put it on the cold
pass and give MEASURED COSTS another row to keep, which is not worth it for a
file that changes a few times a year.
"""

import html
import re
import shlex
import subprocess
import sys
import tomllib

import yaml
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import preflight as P  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
RANGE_SCRIPT = REPO / "scripts" / "ci-pii-range.sh"
DEV_IN = REPO / "requirements-dev.in"
DEV_LOCK = REPO / "requirements-dev.txt"
TEST_IN = REPO / "requirements-test.in"
TEST_LOCK = REPO / "requirements-test.txt"


WINDOWS_MODULES = ["tests/test_secure_fs.py", "tests/test_host_guard.py"]


PYTHON_VERSION = REPO / ".python-version"
PYPROJECT = REPO / "pyproject.toml"
# uv echoes the command it ran into each `.txt`, so the LOCK states the floor
# it was compiled for; the recipe is `scripts/recompile-locks.sh` since W5a,
# which reads the floor rather than carrying a copy of it.
#
# DISCOVERED, not listed. A hand-written tuple could not see a fourth lock:
# adding `requirements-docs.{in,txt}` compiled for 3.12, and a CI step that
# installs it, left this class green because nothing swept for siblings.
LOCK_INPUTS = tuple(sorted(REPO.glob("requirements*.in")))
LOCKS = tuple(sorted(REPO.glob("requirements*.txt")))
RECOMPILE = REPO / "scripts" / "recompile-locks.sh"
# The one requirement sentence in each user-facing document. A reader takes the
# floor from these and from nothing else.
FLOOR_PROSE = (REPO / "AGENTS.md", REPO / "README.md",
               REPO / "CONTRIBUTING.md", REPO / "docs" / "QUICKSTART.md")
FLOOR_CLAIM = re.compile(r"Python (3\.\d+)(?:\+| or newer)")
# The SECOND statement of the floor in a file whose first one is checked, each
# located by text that is not the version. `Python 3.13+` in CONTRIBUTING is
# matched by FLOOR_CLAIM; `and 3.13 is the one to build with`, four words later
# in the same sentence, was matched by nothing, and `ci-and-locks.md` states the
# floor in a HEADING. Both would survive a floor move still reading the old
# number, in the two documents a contributor is most likely to act on.
FLOOR_SENTENCES = ((REPO / "CONTRIBUTING.md", "is the one to build with"),
                   (REPO / "CONTRIBUTING.md", "per .python-version"),
                   (REPO / "docs" / "contributing" / "ci-and-locks.md",
                    "The floor is"),
                   (REPO / "docs" / "contributing" / "ci-and-locks.md",
                    "floor is now ONE number"))
# The sibling claim, which is about the MATRIX rather than the floor and so is
# checked against the legs instead. It was pinned in the issue template and
# nowhere else, while three contributor-facing documents restate it — one of
# them saying those are the ONLY two legs, which is a claim in both directions.
MATRIX_PROSE = ((REPO / "CONTRIBUTING.md", "A green local suite is not a green CI"),
                (REPO / "CONTRIBUTING.md", "Open a pull request against"),
                (REPO / "docs" / "QUICKSTART.md", "CI runs the suite on"))
# A `3.N` in the four prose documents that is CLAIMED AS AN INTERPRETER, which
# is decided by what stands next to it rather than by how the number is spelled.
# Sweeping every `3.N` put a Python-floor guard in charge of the NIIT rate:
# `3.8% NIIT` survived only on a `%` lookahead, so rewording it to `3.8 percent`
# turned the suite red, and so would naming `openpyxl 3.1.5` in AGENTS.md §7 or
# retelling the `charset-normalizer` 3.5.1-over-3.4.9 story in CONTRIBUTING.
# Those are not claims about an interpreter and this is not their guard.
PROSE_VERSION = re.compile(r"(?<![\d.])3\.\d+")
INTERPRETER_CONTEXT = re.compile(
    r"python|cpython|\bpy3|interpreter|\bfloor\b|\bleg\b|\bvenv\b",
    re.IGNORECASE)


def _interpreter_versions(text: str) -> set:
    """Every `3.N` in `text` that its own neighbourhood claims is an interpreter.

    The window is the forty characters before the number and the twenty after,
    FLATTENED first, because the claim and the number are routinely on
    different lines — `requires Python 3.12\nor newer` is one sentence to a
    reader and two lines to a file, and that soft wrap was one of the spellings
    that walked past the pattern this replaces.

    Deciding by neighbourhood rather than by the numeral keeps what matters:
    the check still does not care HOW the version is written, which is the
    property four bypasses were built out of. What it no longer does is claim
    every number of the form 3.N is about Python.
    """
    flat = re.sub(r"\s+", " ", text)
    return {m.group(0) for m in PROSE_VERSION.finditer(flat)
            if INTERPRETER_CONTEXT.search(
                flat[max(0, m.start() - 40):m.end() + 20])}


MINIMUM_CONTEXT = re.compile(
    r"minimum|requires?|required|or later|or newer|at least|\bfloor\b|"
    r"oldest|supported", re.IGNORECASE)


# A version written as `Python 3.N` — the form a requirement sentence takes.
# Emphasis marks are allowed between the two, because `**Python 3.12**+` is one
# of the spellings that walked past the pattern this backs up.
NAMED_PYTHON = re.compile(r"(?:C?Python)[\s*_`]*(3\.\d+)")


def _minimum_claims(text: str) -> set:
    """Every version these documents offer as the LOWEST supported interpreter.

    `allowed` lets the legs be named, which is right — CONTRIBUTING says a
    current macOS gives you 3.14 and that is true. It also let `Requires Python
    3.14 or later` stand, because 3.14 IS a leg: a floor stated one minor above
    the real one, in the file a reader takes it from. A version offered as a
    MINIMUM has to be the floor exactly, whatever else the document may name.

    Anchored to `Python 3.N` rather than to a bare number in a window. A window
    alone read "the locks are compiled for the floor and CI runs 3.13 and 3.14"
    as two minimum claims, because `floor` sits a few words ahead of a sentence
    that is about the matrix — the question is which version a REQUIREMENT
    names, and a requirement names its language.
    """
    flat = re.sub(r"\s+", " ", text)
    return {m.group(1) for m in NAMED_PYTHON.finditer(flat)
            if MINIMUM_CONTEXT.search(flat[max(0, m.start() - 30):m.end() + 25])}
# Which leg each conditioned step belongs on, BY NAME. The operator used to be
# counted over a FLAT list of conditions — one `==` and one `!=` had to exist
# somewhere — so swapping them between "Run tests" and "Run tests with coverage"
# satisfied the count while coverage ran on the wrong leg and the upload looked
# for a file that leg never wrote.
#
# Since W5a the condition asks the leg's own `coverage` flag rather than
# comparing the version to the floor: three hand-typed copies of the floor were
# three that failed SILENT when it moved. Which leg carries the flag is pinned
# by `test_the_floor_leg_and_only_the_floor_leg_runs_coverage`.
LEG_STEPS = {"Run tests": "!matrix.coverage",
             "Run tests with coverage": "matrix.coverage",
             "Upload coverage": "matrix.coverage"}
# A conditioned step in the matrixed job that is NOT selecting a leg goes here
# instead. `if: failure()` on a diagnostic upload is ordinary CI and the first
# draft could not express it: the set check said to add the step to LEG_STEPS,
# and LEG_STEPS then demanded its condition be an equality against the floor.
# A message prescribing a fix its own assertion refuses is worse than no
# message. Named rather than pattern-matched, so a step that quietly STOPS
# selecting a leg still has to come through here.
NON_LEG_STEPS: frozenset = frozenset()
# The jobs that legitimately set up no interpreter. NAMED, because the question
# "does this job run Python?" cannot be answered from the text of its commands:
# a regex over `run:` missed `pytest`, `tox`, `make test`, `uvx pytest` and
# `./scripts/run-suite.sh`, and saw nothing at all in a job whose only step was
# `uses:` a composite action. Each of those ran the suite on the runner default
# under a green tick. Enumerating the exceptions is the form that fails closed —
# a new job is red here until someone says which kind it is.
NO_INTERPRETER_JOBS = frozenset({"pii-scan"})


def _release(version) -> tuple:
    """`3.13` as `(3, 13)`. YAML gives 3.13 as a float and "3.13" as a string;
    both are how a human writes a version, and only one of them used to parse.
    """
    return tuple(int(part) for part in str(version).split("."))


def _is_truthy(value) -> bool:
    """GitHub's `true`, in every spelling YAML and expressions give it.

    `continue-on-error: ${{ true }}` is the CANONICAL form for this key — it is
    how a matrix drives it — and `yaml.safe_load` hands it back as the STRING
    `'${{ true }}'`, which `is not True`. So did `"true"`. A job that reports
    success whatever its legs find passed on the spelling alone.
    """
    if value is True:
        return True
    if isinstance(value, str):
        return _normalise_expr(value).strip("'").lower() == "true"
    return False


def _normalise_expr(expr: str) -> str:
    """A GitHub expression with its spelling removed, so it can be COMPARED.

    `if: ${{ x }}` and `if: x` are the same condition, `"3.13"` and `'3.13'` the
    same literal, and whitespace is free throughout. Reducing all three to one
    form is what lets an assertion demand the exact condition rather than check
    that some substring is present — and a substring check is how a condition
    acquired `&& false` while still naming the matrix and the floor.
    """
    expr = expr.strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2]
    # OUTSIDE QUOTES ONLY. Collapsing everything reached into the literal, so
    # `matrix.python-version == '3. 13'` normalised to the expected string
    # while GitHub compared each leg against `3. 13`, which no leg equals —
    # coverage and its upload then ran on NO leg under a green tick, which is
    # the exact failure this comparison exists to refuse.
    out, quote = [], ""
    for ch in expr:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = "'"
            out.append("'")
        elif not ch.isspace():
            out.append(ch)
    return "".join(out)
# `\Z` because an UNCLOSED `<!--` hides everything after it from a reader
# as thoroughly as a closed one does, and leaves it plain text to a regex.
HTML_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
ISSUE_TEMPLATE = REPO / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
# The regeneration RECIPE, which names the flag outside any lock file.
RECIPE_SOURCES = (REPO / "CONTRIBUTING.md",
                  REPO / "docs" / "contributing" / "ci-and-locks.md")

# The regeneration recipe is located as a COMMAND, never by scanning for the
# flag. `--python-version` used to be matched anywhere in the file and the
# assertion compared the SET of versions that turned up, so a recipe respelled
# out of the pattern — `--python-version  3.12` with two spaces, or
# `--python-version "3.12"` — simply left the set, and an unchanged explanatory
# sentence in the same file went on supplying the floor. Two more spellings
# passed for the same reason: a lock compiled for `3.13.9` matched as `3.13`
# because the pattern stopped there, and a file whose recipe was deleted
# outright passed on any surviving mention of the flag.
UV_COMPILE = "uv pip compile"
UV_COMPILE_RE = re.compile(r"\buv\s+pip\s+compile\b")


def _uncommented(text: str) -> str:
    """`text` with YAML/shell comments removed, quote-aware.

    THE GATE MUST NOT READ ITS OWN COMMENTS. A mutation that left the old matrix
    behind as a comment and changed the live one passed every assertion here —
    `re.search` found the comment first — and commenting out `target-version`
    while adding a new active line passed too. This repository has the failure
    recorded already: a substring check over text counts prose as code in BOTH
    directions.
    """
    out = []
    for line in text.split("\n"):
        quote = ""
        for i, ch in enumerate(line):
            if quote:
                if ch == quote:
                    quote = ""
            elif ch in "\"'":
                quote = ch
            elif ch == "#":
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)


def _block_at(text: str, anchor: str) -> str:
    """The claim the `anchor` line starts, to the end of its paragraph or item.

    A sentence in Markdown is not a line: every claim here wraps, and two of the
    three put the anchor on one line and the versions on the next. Runs to a
    blank line or to the start of the next list item, whichever comes first, so
    a neighbouring bullet's versions are never read as part of this claim.
    """
    lines = text.split("\n")
    hits = [i for i, line in enumerate(lines) if anchor in line]
    assert len(hits) == 1, (
        f"{len(hits)} lines say {anchor!r}; this is anchored to exactly one, so "
        "a rewrite comes back through here rather than leaving a check with "
        "nothing to read")
    end = hits[0]
    while end + 1 < len(lines):
        following = lines[end + 1]
        if not following.strip() or re.match(r"\s*[-*+] |\s*#{1,6} ", following):
            break
        end += 1
    return "\n".join(lines[hits[0]:end + 1])


def _visible(text: str) -> str:
    """`text` with HTML comments removed — what a reader actually sees.

    A prose claim is the one copy no job installs, so the only thing standing
    behind it is a reader's eye, and an HTML comment is invisible to that eye
    and plain text to a regex. Moving README's requirement sentence inside
    `<!-- -->` and leaving a different version visible beside it left every
    assertion below green. Same failure as `_uncommented` above, in the syntax
    Markdown uses for it.
    """
    # Entities are RESOLVED first: `Python 3&#46;12 or newer` renders exactly
    # like the claim it contradicts and carried no `.` for the pattern to find.
    return HTML_COMMENT.sub("", html.unescape(text))


def _compile_commands(text: str) -> list[list[str]]:
    """Every `uv pip compile` invocation in `text`, split into argv.

    The recipe is written across two lines with a trailing backslash, and in the
    `.in` and `.txt` files it sits inside the comment header, so a leading `#`
    comes off before the continuation is joined. shlex does the rest: it
    collapses repeated spaces and strips quoting, which is what makes
    `--python-version  "3.12"` the same token as `--python-version 3.12` rather
    than a string the caller has to have anticipated.

    A command is a line that BEGINS with the command. Finding it anywhere in the
    line would read a sentence that merely mentions `uv pip compile` as an
    invocation carrying no flags, and fail on prose that is not making a claim.

    An invocation must also NAME ITS INPUT — some `*.in` — because these
    documents wrap at about ninety columns and use the phrase in prose, and a
    wrap that lands `uv pip compile` at column zero was read as a command
    carrying no flags at all.

    And a line is SPLIT on `&&` and `;` first, so each invocation is its own
    argv. Chaining is the natural way to write "regenerate all three locks",
    and reading the line as one command hid every invocation after the first —
    the second and third could compile for any version at all.
    """
    joined: list[str] = []
    carried = ""
    for raw in text.split("\n"):
        line = re.sub(r"^\s*#\s?", "", raw).rstrip()
        if line.endswith("\\"):
            carried += line[:-1] + " "
            continue
        joined.append(carried + line)
        carried = ""
    if carried:
        joined.append(carried)
    invocations = []
    for line in joined:
        for part in re.split(r"&&|;", line):
            part = part.strip()
            # SEARCHED, not `startswith`. A prompt `$ `, a `sudo`, an env
            # assignment (`UV_PYTHON=3.12 uv pip compile ...`) and `uv  pip
            # compile` with two spaces all hid an invocation, the last one
            # pointedly: shlex collapses repeated spaces, but shlex ran AFTER
            # the prefix test, so the collapse never happened. Prose is kept
            # out by the `*.in` requirement below rather than by the prefix.
            found = UV_COMPILE_RE.search(part)
            if found:
                part = part[found.start():]
                # comments=True: a trailing `# don't drop --universal` made
                # shlex raise "No closing quotation" — a stdlib traceback with
                # no file, no line and no claim — and a comment that repeated
                # the flag made the duplicate check report two values that
                # agreed with each other.
                argv = shlex.split(part, comments=True)
                # A prose line that WRAPS onto `uv pip compile` at column zero
                # is not an invocation. Every real recipe names its input.
                if any(token.endswith(".in") for token in argv):
                    invocations.append(argv)
    return invocations


def _flag_value(argv: list[str], flag: str) -> str | None:
    """The value `flag` was given in `argv`, or None if it is not there.

    `--python-version 3.13` and `--python-version=3.13` are the same flag; a
    trailing `--python-version` with nothing after it is the flag PRESENT and
    unset, which is a different failure from absent and reads as `''`.

    RAISES on a repeat rather than picking one. Returning the first matched the
    reader's eye and not uv, which takes the LAST for a scalar argument, so
    `--python-version 3.13 --python-version 3.12` read as 3.13 here and
    compiled for 3.12. Returning the last instead would only move the blind
    spot; a recipe that gives one flag two values is a broken recipe either
    way, and saying so is the only answer that is not a guess about clap.

    NOT `crypto_importer._flag_value`, which shares the name and answers a
    different question. That one gives a user their operand or raises, so an
    absent flag and a flag given nothing are both errors, and `--flag=value`
    is not found AT ALL — `argv.index` raises on it, because the token is
    `--flag=value` and not `--flag`. That spelling is the one this test exists
    to read. Two contracts, deliberately.
    """
    found = []
    for i, token in enumerate(argv):
        if token == flag:
            found.append(argv[i + 1] if i + 1 < len(argv) else "")
        elif token.startswith(flag + "="):
            found.append(token[len(flag) + 1:])
    assert len(found) < 2, (
        f"`{' '.join(argv)}` gives {flag} the values {found}. uv takes the "
        "last for a scalar argument and a reader takes the first, so this "
        "command does not have one answer")
    return found[0] if found else None


def _ci_code() -> str:
    """`ci.yml` with its comments gone — what the runner actually executes.

    Every assertion that a step IS THERE reads this, because commenting the step
    out leaves the text in the file and a substring check green over a job that
    no longer runs. That is a claim about the whole module and it was false when
    first written: six call sites were converted and four presence checks — the
    lock installs, the Windows module slice, the cron, the per-job body — were
    left reading raw text, which is the sweep stopping where it was spotted. Assertions that something is ABSENT deliberately read the
    raw text instead: a forbidden string is worth a red even in a comment, and
    reading past comments there would only relax a check that is already safe in
    the strict direction.
    """
    return _uncommented(_ci())


def _extract_scan_step() -> str:
    """The shell of the pii-scan step, dedented, so it can be RUN in a stub
    tree rather than pattern-matched."""
    body = _ci()[_ci().index("EVENT_NAME: ${{ github.event_name }}"):]
    body = body.split("run: |\n", 1)[1]
    lines = []
    for line in body.split("\n"):
        if line.strip() and not line.startswith("          "):
            break
        lines.append(line[10:])
    return "\n".join(lines)


def _ci() -> str:
    return CI.read_text()


def _run_range(**env):
    """The range script, with a clean environment plus `env`."""
    return subprocess.run(["bash", str(RANGE_SCRIPT)], cwd=REPO,
                          capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin:/usr/local/bin", **env})


def _declared_triggers() -> "set[str]":
    """The event names under the workflow's `on:` key.

    Hand-parsed for the reason in the module docstring, and narrowly: the block
    runs from the `on:` line to the `jobs:` line, and a trigger is a key at one
    level of indentation inside it.
    """
    text = _ci()
    start = re.search(r"^on:$", text, re.M).end()
    # To the next TOP-LEVEL key, not to `jobs:` — `concurrency:` sits between
    # them and its own two-space keys were read as triggers the moment it was
    # added, which put `group` and `cancel-in-progress` into this set.
    rest = text[start:]
    end = re.search(r"^[a-z]", rest, re.M)
    block = rest[:end.start()] if end else rest
    return {m.group(1) for m in re.finditer(r"^  ([a-z_]+):", block, re.M)}


def _handled_events() -> "set[str]":
    """The event names the range script has a `case` arm for.

    The arms are `a|b)` alternations, so the split is on `|` as well.
    """
    body = RANGE_SCRIPT.read_text()
    arms = re.findall(r"^    ([a-z_|]+)\)", body, re.M)
    return {name for arm in arms for name in arm.split("|")}


class TestEveryTriggerHasAScanRange:
    """THE invariant of this change, and the one a reviewer should read first.

    A trigger added to the workflow without an arm in the range script is a CI
    run whose PII gate cannot determine what to scan. The script fails closed on
    an unknown event rather than falling back — so the failure is a red job
    rather than a silent pass — but a red job on every push is not a working CI
    either, and nothing but this test connects the two files.
    """

    def test_the_script_handles_every_event_the_workflow_declares(self):
        missing = _declared_triggers() - _handled_events()
        assert not missing, (
            f"ci.yml triggers on {sorted(missing)} and scripts/ci-pii-range.sh "
            "has no arm for it — add one there, deciding whether that event "
            "introduces commits, rather than letting it reach the `*)` refusal")

    def test_the_workflow_declares_the_four_triggers_W6_asked_for(self):
        assert _declared_triggers() == {"pull_request", "push", "schedule",
                                        "workflow_dispatch"}

    def test_an_unknown_event_is_refused_and_not_defaulted(self):
        result = _run_range(EVENT_NAME="deployment_status")
        assert result.returncode == 1
        assert "no PII-scan range is defined" in result.stderr

    def test_no_event_yields_a_range_beginning_with_the_separator(self):
        """`...HEAD` is what the inline expression produced on a non-PR event,
        and it is the shape this whole script exists to make impossible.

        Driven with the inputs each event really carries as well as with none:
        the empty-input pass alone proved only that a refusing script prints
        nothing, which is true of a script that can never produce a range."""
        sha = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=REPO,
                             capture_output=True, text=True).stdout.strip()
        supplied = {"pull_request": {"PR_BASE_SHA": sha},
                    "pull_request_target": {"PR_BASE_SHA": sha},
                    "push": {"PUSH_BEFORE_SHA": sha}}
        produced_one = False
        for event in sorted(_handled_events()):
            for extra in ({}, supplied.get(event, {})):
                result = _run_range(EVENT_NAME=event, **extra)
                assert not result.stdout.startswith("..."), (
                    f"{event} produced {result.stdout!r}")
                assert result.returncode in (0, 1, 3), (event, result.returncode)
                if result.returncode == 0:
                    produced_one = True
                    assert result.stdout.strip().endswith("...HEAD")
                    assert len(result.stdout.split("...")[0]) == 40
        assert produced_one, (
            "no event produced a range at all — this test would pass against a "
            "script that always refuses")


class TestTheRangeItselfIsRight:
    def test_a_pull_request_scans_from_its_base(self):
        base = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=REPO,
                              capture_output=True, text=True).stdout.strip()
        result = _run_range(EVENT_NAME="pull_request", PR_BASE_SHA=base)
        assert result.returncode == 0
        assert result.stdout.strip() == f"{base}...HEAD"

    @pytest.mark.parametrize("base", ["", "HEAD", "not-a-sha", "abc123"])
    def test_a_pull_request_without_a_usable_base_REFUSES(self, base):
        """The empty case is the live one: an unset workflow expression
        interpolates to the empty string, not to an error."""
        result = _run_range(EVENT_NAME="pull_request", PR_BASE_SHA=base)
        assert result.returncode == 1
        assert "refusing to guess a range" in result.stderr

    def test_a_push_scans_from_what_was_there_before(self):
        before = subprocess.run(["git", "rev-parse", "HEAD~2"], cwd=REPO,
                                capture_output=True, text=True).stdout.strip()
        result = _run_range(EVENT_NAME="push", PUSH_BEFORE_SHA=before)
        assert result.returncode == 0
        assert result.stdout.strip() == f"{before}...HEAD"

    def test_a_first_push_scans_the_whole_history_rather_than_nothing(self):
        """`before` is forty zeros when the branch did not exist. It is a valid
        sha SHAPE naming no commit, so a range built from it resolves to
        nothing — and every commit on the branch is new."""
        result = _run_range(EVENT_NAME="push", PUSH_BEFORE_SHA="0" * 40)
        assert result.returncode == 0
        root = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"],
                              cwd=REPO, capture_output=True, text=True
                              ).stdout.strip().splitlines()[-1]
        assert result.stdout.strip() == f"{root}...HEAD"

    def test_a_scheduled_run_has_nothing_to_scan_and_says_so_distinctly(self):
        """Exit 3, not 0 and not 1. A scheduled run introduces no commits, so
        scanning is neither possible nor a failure — and the workflow has to
        tell that apart from a range it could not determine."""
        assert _run_range(EVENT_NAME="schedule").returncode == 3
        assert _run_range(EVENT_NAME="workflow_dispatch").returncode == 3

    def test_being_run_with_no_event_at_all_is_refused(self):
        result = _run_range()
        assert result.returncode == 1
        assert "EVENT_NAME is unset" in result.stderr


class TestTheWorkflowUsesTheScriptRatherThanAnExpression:
    def test_the_scan_step_calls_the_range_script(self):
        assert "bash scripts/ci-pii-range.sh" in _ci_code()

    def test_the_base_sha_expression_is_only_ever_passed_as_an_input(self):
        """It may reach the script as an environment variable. It may not be
        interpolated into a range, which is what made it silently empty."""
        assert "base.sha }}...HEAD" not in _ci()
        assert "PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}" in _ci_code()

    def test_the_shared_scanner_is_still_what_runs(self):
        assert "bash scripts/pre-commit-pii-scan.sh" in _ci_code()
        assert "BLOCKED_EXTENSIONS=" not in _ci(), (
            "CI is re-implementing the file-type rules instead of calling the script")

    def test_the_nothing_to_scan_code_is_distinguished_from_a_failure(self, tmp_path):
        """Treating every non-zero alike would turn a scheduled run into a
        permanent red, and treating every non-zero as "nothing to scan" would
        turn an undeterminable range into a clean pass.

        RUN, rather than matched: the substring form passed against a `case`
        that mapped 3 to `exit 1` just as happily."""
        step = _extract_scan_step()
        stub = tmp_path / "scripts"
        stub.mkdir()
        (stub / "pre-commit-pii-scan.sh").write_text("echo SCANNED \"$1\"\n",
                                                     encoding="utf-8")
        outcomes = {}
        for code, out in ((0, "abc...HEAD"), (3, ""), (1, ""), (2, "")):
            (stub / "ci-pii-range.sh").write_text(
                f'printf "%s" "{out}"\nexit {code}\n', encoding="utf-8")
            r = subprocess.run(["bash", "-e", "-c", step], cwd=tmp_path,
                               capture_output=True, text=True)
            outcomes[code] = (r.returncode, r.stdout)
        assert outcomes[0][0] == 0 and "SCANNED abc...HEAD" in outcomes[0][1]
        assert outcomes[3][0] == 0, "a scheduled run must not be a permanent red"
        assert outcomes[1][0] != 0, "an undeterminable range must not pass"
        assert outcomes[2][0] != 0, "an unexpected code must not pass either"


class TestTheDevToolingIsLockedLikeTheRuntime:
    def test_nothing_is_installed_by_a_bare_pip_install(self):
        """Every `pip install` line, wherever it sits. Anchoring on `run: pip
        install` missed the shape the coverage step already uses — a multi-line
        `run: |` block, where an unpinned install would have been invisible."""
        # Comments are stripped first: this file DESCRIBES the unpinned
        # `pip install pytest` it replaced, and a sweep that cannot tell a
        # command from a note about one goes red on its own history. Through
        # `_ci_code`, which is the module's one answer to that question — the
        # hand-rolled filter that stood here dropped only WHOLE-line comments
        # and so still read a trailing one as a command.
        commands = _ci_code()
        bare = [m.group(0).strip() for m in
                re.finditer(r"pip install (?!-r )\S.*", commands)]
        assert not bare, f"unpinned installs in CI: {bare}"

    def test_every_python_job_installs_from_a_lock(self):
        installs = re.findall(r"pip install -r (\S+)", _ci_code())
        assert installs, "no lock is installed anywhere"
        assert set(installs) <= {"requirements.txt", "requirements-test.txt",
                                 "requirements-dev.txt"}, set(installs)

    def test_the_suite_is_never_run_beside_the_auditors_dependencies(self):
        """The lock split, pinned. Resolving pytest beside pip-audit landed
        `charset-normalizer` 3.5.1 over the 3.4.9 `requirements.txt` pins, and
        `parse_utils` refused to parse — correctly: a parser version the lock
        does not name is one the suite never ran against. Install ORDER would
        have hidden it; two locks make it impossible."""
        for job in ("  test:", "  test-windows:"):
            body = _ci_code()[_ci_code().index(job):].split("\n\n  ")[0]
            assert "requirements-test.txt" in body, job
            assert "requirements-dev.txt" not in body, (
                f"{job} installs the auditor's tree beside the runtime tree")

    def test_the_two_locks_share_no_dependency(self):
        """DISJOINT, not "they agree" — and the difference is the whole split.

        Two locks exist because resolving pytest beside pip-audit landed
        `charset-normalizer` 3.5.1 over the 3.4.9 `requirements.txt` pins.
        Agreeing today is a coincidence a transitive bump ends; sharing nothing
        is the property that makes install order irrelevant, and
        `requirements-test.in` tells a reader this test keeps it — which was
        true of the sentence before it was true of the repository.

        THIS REPLACES a test that asked whether the two locks DISAGREE about
        any shared package. An empty intersection means there is nothing to
        disagree about, so once this assertion existed beside it that one could
        never go red again — two tests where the weaker cannot fail is a pair
        whose live half a reader has to work out. What it had that was worth
        keeping is the VERSIONS in its message, which is what a maintainer
        needs to see; they are reported here.
        """
        def pins(name):
            return dict(re.findall(r"^([A-Za-z0-9._-]+)==([^\s\\]+)",
                                   (REPO / name).read_text(encoding="utf-8"),
                                   re.M))
        runtime, test = pins("requirements.txt"), pins("requirements-test.txt")
        shared = {k: (runtime[k], test[k]) for k in set(runtime) & set(test)}
        assert not shared, (
            f"the runtime and test locks now share {sorted(shared)} — "
            f"{ {k: f'{a} vs {b}' for k, (a, b) in shared.items()} }. Install "
            "order decides which version wins where they differ, and even "
            "where they agree today a transitive bump makes them differ "
            "tomorrow. That is the failure the split exists to make impossible")

    def test_the_lock_is_hash_pinned(self):
        text = DEV_LOCK.read_text(encoding="utf-8")
        assert "--hash=sha256:" in text
        pinned = re.findall(r"^([A-Za-z0-9._-]+)==", text, re.M)
        assert len(pinned) > 5, pinned

    @pytest.mark.parametrize("tool,where", [("pytest", TEST_IN), ("coverage", TEST_IN),
                                            ("ruff", DEV_IN), ("mypy", DEV_IN),
                                            ("pip-audit", DEV_IN)])
    def test_the_input_names_every_tool_ci_runs(self, tool, where):
        assert re.search(rf"^{re.escape(tool)}\b", where.read_text(encoding="utf-8"), re.M)

    @pytest.mark.parametrize("tool", ["ruff", "mypy"])
    def test_the_linters_are_version_pinned_in_the_input_too(self, tool):
        """A linter is the one dependency whose new release adds FINDINGS. The
        lock pins it for a reproducible install; the `.in` pins it so that
        REGENERATING the lock does not quietly adopt a new rule set."""
        assert re.search(rf"^{tool}==\d", DEV_IN.read_text(encoding="utf-8"), re.M)

    def test_the_lock_says_which_command_regenerates_it(self):
        assert "uv pip compile requirements-dev.in" in DEV_LOCK.read_text(encoding="utf-8")

    @pytest.mark.parametrize("lock", ["requirements.txt", "requirements-dev.txt",
                                      "requirements-test.txt"])
    def test_every_lock_was_resolved_universally(self, lock):
        assert "--universal" in (REPO / lock).read_text(encoding="utf-8")

    def test_the_lock_the_windows_job_installs_really_covers_windows(self):
        """`--universal` in the regenerate comment says what was ASKED for; a
        platform MARKER in the resolution is what shows it was granted. Scoped
        to the test lock because that is the one the windows job installs — the
        dev lock resolves to the same set on every platform and so carries no
        marker to find, which is a fact about its dependencies rather than
        evidence about the flag."""
        text = TEST_LOCK.read_text(encoding="utf-8")
        assert "platform_system" in text or "sys_platform" in text, (
            "requirements-test.txt carries no platform marker, so it was "
            "resolved for one platform only and the windows job would install "
            "a tree that does not fit it")


class TestCoverageStaysInsideThisRepository:
    def test_no_third_party_coverage_service_is_wired_up(self):
        """AGENTS.md §1: this repository's subject is real returns. A coverage
        service is a new destination for repository content AND a new credential
        in repository secrets, and neither is spent on a convenience."""
        text = _ci().lower()
        for service in ("codecov", "coveralls", "codeclimate", "sonarcloud"):
            assert service not in text, f"{service} upload added to CI"

    def test_coverage_is_produced_and_kept_as_an_artifact(self):
        assert "coverage run -m pytest" in _ci_code()
        assert "actions/upload-artifact" in _ci_code()

    def test_coverage_is_scoped_to_the_python_this_repo_ships(self):
        """Unscoped, the report measures pytest and the standard library and the
        number stops meaning anything."""
        pyproject = (REPO / "pyproject.toml").read_text()
        assert "[tool.coverage.run]" in pyproject
        assert 'source = ["agents", "evals"]' in pyproject

    def test_the_subprocess_blind_spot_is_stated_where_the_number_is_configured(self):
        """The report measures the pytest process only, and this suite drives
        the engine through subprocesses constantly — so `preflight.py` and every
        CLI arm read as unexercised when they are the most exercised code here.
        A reader who does not know that draws the opposite conclusion from the
        lowest rows, which is worse than having no report.

        Pinned because the caveat is the only thing standing between the number
        and that misreading, and a caveat nothing checks is a caveat that gets
        tidied away."""
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "SUBPROCESS" in pyproject
        assert "COVERAGE_PROCESS_START" in pyproject, (
            "name the mechanism that is absent, so a reader can tell this is a "
            "known limit rather than an oversight")

    def test_no_coverage_threshold_is_enforced(self):
        """Deliberate. A threshold picked today becomes the target; the honest
        use of this report is reading which BRANCHES are unexercised."""
        config = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "fail_under" not in config.replace("# No `fail_under`", "")


class TestWindowsIsFinallyExercised:
    def test_there_is_a_windows_job(self):
        assert "runs-on: windows-latest" in _ci_code()

    @pytest.mark.parametrize("module", WINDOWS_MODULES)
    def test_it_runs_the_modules_whose_windows_branches_were_unexercised(self, module):
        job = _ci_code()[_ci_code().index("test-windows:"):].split("\n  lint:")[0]
        assert module in job
        # A job that collects without running, or selects nothing, looks
        # identical to a passing one from outside.
        assert "--collect-only" not in job
        assert re.search(r"\s-k\s", job) is None
        assert "continue-on-error" not in job, (
            "a job allowed to fail is a job whose red nobody sees")

    def test_secure_fs_really_does_carry_a_windows_branch_to_exercise(self):
        """Guards the premise. If `secure_fs` ever stops branching on the
        platform, this job is running a POSIX module on Windows for nothing and
        the reason it exists should be re-read rather than inherited."""
        source = (REPO / "agents" / "lib" / "secure_fs.py").read_text()
        assert 'os.name == "nt"' in source


class TestTheScheduleIsRealAndWeekly:
    def test_a_cron_is_declared(self):
        assert re.search(r"- cron: \"[\d*/, ]+\"", _ci_code())

    def test_it_runs_weekly_rather_than_nightly(self):
        """A daily run on a repository that changes a few times a week reports
        the same verdict every day until nobody reads it."""
        cron = re.search(r"- cron: \"([^\"]+)\"", _ci()).group(1)
        minute, hour, dom, month, dow = cron.split()
        assert dom == "*" and month == "*"
        assert dow != "*", "a weekday field of * is a DAILY run"
        assert minute != "0", (
            "GitHub's scheduler is busiest on the hour and delays a run that "
            "asks for it; the offset is deliberate")


class TestTheEvalsStayOutOfCI:
    """The owner ruled Q2: V3's model-driven runs happen LOCALLY, on credentials
    already on the operator's machine. W6's text predates that ruling and asks
    for them on a schedule — superseded, and recorded here so the next reader of
    the backlog does not implement it.

    `tests/test_eval_run.py` pins the general form (only `ci.yml` may have a
    trigger). This pins the specific one: no job in `ci.yml` runs the model
    harness either, which the general test cannot see.
    """

    def test_no_job_dispatches_the_model_driven_runner(self):
        assert "evals/run.py" not in _ci()
        assert "evals.run" not in _ci()

    def test_no_model_credential_is_read_by_any_job(self):
        assert "ANTHROPIC_API_KEY" not in _ci()
        assert "secrets." not in _ci(), (
            "a secret in this workflow is readable by every job it ever gains")


def test_the_range_script_is_executable_and_runs_under_sh():
    """It is invoked as `bash scripts/...` in CI, but a contributor will run it
    directly. A script that is not executable fails in a way that reads like a
    missing file."""
    assert RANGE_SCRIPT.stat().st_mode & 0o111
    result = subprocess.run(["sh", str(RANGE_SCRIPT)], cwd=REPO,
                            capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin", "EVENT_NAME": "schedule"})
    assert result.returncode == 3, result.stderr


def test_this_module_is_not_selected_by_the_hook():
    """The docstring says so; this makes it true rather than believed. If the
    hook ever gains an arm for this module, that arm moves the cold-pass row and
    the claim in the docstring has to go."""
    hook = (REPO / ".claude" / "hooks" / "skill-invariants.sh").read_text(encoding="utf-8")
    # The MODULE LIST, not the file: a bare substring check once went red on
    # hook prose that merely NAMED this module. What matters is whether the
    # hook would RUN it.
    assert "=tests/test_ci_workflow.py" not in hook


# --------------------------------------------------------------------------
# The finding the windows job produced on its first run.
# --------------------------------------------------------------------------

def _unencoded_text_io(path: Path) -> "list[int]":
    """Line numbers where `path` reads or writes TEXT without saying in what.

    `open`/`read_text`/`write_text` default to `locale.getencoding()`, which is
    UTF-8 on the hosts this was developed on and **cp1252 on Windows**. This
    repository's files are full of em-dashes and section signs, so on Windows
    every one of these raises `UnicodeDecodeError: 'charmap' codec can't decode
    byte 0x8f` — which is how the first run of the windows job failed, in bulk,
    on tests that had nothing to do with Windows.

    `os.open` and `pdfplumber.open` are different functions that share a name,
    and a binary mode has no encoding to state.
    """
    import ast
    hits = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in ("open", "read_text", "write_text"):
            continue
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id in ("os", "pdfplumber")):
            continue
        if "encoding" in {kw.arg for kw in node.keywords}:
            continue
        if name == "open":
            mode = None
            # `open(path, mode)` for the builtin; `path.open(mode)` for a Path.
            index = 0 if isinstance(func, ast.Attribute) else 1
            if len(node.args) > index and isinstance(node.args[index], ast.Constant):
                mode = node.args[index].value
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            if mode and "b" in str(mode):
                continue
        hits.append(node.lineno)
    return hits


class TestTextIOSaysWhichEncoding:
    """AGENTS.md claims Windows support in 27 places, and until the windows job
    ran nothing tested it. What it found first was not an ACL difference: it was
    that the shipped engine reads `result.json`, `answers.md` and the tax-law
    cache with the platform's default encoding. On Windows that is cp1252, and a
    return carrying a `§` or an em-dash — which every template here does —
    raises rather than parsing.

    The repository has paid for this family of bug once already, in shell: the
    PII gate pins `LC_ALL=C` because one Latin-1 byte under a UTF-8 locale hid
    every match on its line. This is the same mistake in the other direction.
    """

    @pytest.mark.parametrize("area", ["agents", "evals"])
    def test_no_shipped_module_leaves_the_encoding_to_the_platform(self, area):
        offenders = {}
        for source in sorted((REPO / area).rglob("*.py")):
            lines = _unencoded_text_io(source)
            if lines:
                offenders[str(source.relative_to(REPO))] = lines
        assert not offenders, (
            "these read or write text without naming an encoding, so they use "
            f"cp1252 on Windows and raise on this repo's own content: {offenders}")

    @pytest.mark.parametrize("module", ["tests/test_secure_fs.py",
                                        "tests/test_host_guard.py"])
    def test_the_modules_the_windows_job_runs_are_clean_too(self, module):
        """Scoped to the two the windows job actually runs. The rest of the
        suite has the same call sites and is NOT swept here: it never runs on
        Windows, and a sweep of 869 call sites for a platform nothing tests them
        on is churn dressed as rigour. If the windows job ever widens, this
        parametrize list widens with it."""
        assert not _unencoded_text_io(REPO / module)


class TestTheInterpreterFloorIsOneNumber:
    """The oldest interpreter this toolkit supports, stated in many files.

    Deliberately not a NUMBER of files. This class is here because a count in
    prose drifts away from what it counts, and it would be the only claim in it
    that nothing checks.

    THERE USED TO BE TWO NUMBERS — `MIN_PYTHON` said 3.10 and the build files
    said 3.12 — and the range between them was supported in prose while no job
    and no machine had ever run it. The full account, including what the floor
    move did and did not cost, is in `docs/contributing/ci-and-locks.md`; it is
    not retold here, because a second copy is a second chance to drift.

    W5 recorded "pin the dev interpreter" as done and shipped a
    `.python-version` file — a pin no reader is obliged to consult and nothing
    compares anything against. This is the comparison.

    Each site is read the way its own CONSUMER reads it: `ci.yml` and the issue
    template as parsed YAML, `pyproject.toml` as parsed TOML, the regeneration
    recipe as a command line, and the prose as the text a reader can see, with
    its HTML comments removed. `MIN_PYTHON` is read by IMPORT — its VALUE is
    what refuses a taxpayer's return, and a regex over its assignment would pass
    while the effective value differed.
    """

    @staticmethod
    def _floor() -> str:
        """`.python-version` is the source. Everything else is checked to it."""
        return PYTHON_VERSION.read_text().strip()

    def test_the_floor_file_holds_a_release(self):
        """Owned by its own assertion rather than by all the others.

        `_floor()` used to validate as well as read, so a malformed pin reported
        six identical failures across six tests about six unrelated files, and
        the claim actually being made — that `.python-version` names a release —
        belonged to none of them.
        """
        floor = self._floor()
        assert re.fullmatch(r"3\.\d+", floor), (
            f".python-version holds {floor!r}, which is not a `3.N` release — "
            "every other assertion in this class is derived from it")

    def test_the_tool_configuration_targets_the_floor(self):
        """ruff and mypy, and BOTH of them act on this today.

        This docstring said only mypy did, and got there by calling the obvious
        justification — "a linter pointed at a newer interpreter accepts syntax
        the floor cannot parse" — false for ruff. It is not false. Under
        `--select F` alone the pinned ruff rejects `type Alias = int` at `py310`
        with "Cannot use `type` alias statement", so the target gates grammar
        too and rule selection cannot switch that off. It also resolves
        BUILTINS against the target,
        and F821 is in the `F` family `select` enables, so set above the floor
        ruff accepts names the oldest supported interpreter does not have. The
        worked example is in `pyproject.toml` beside the setting, and is not
        repeated here — it demonstrates the 3.12/3.13 boundary this floor move
        actually crosses.

        mypy's `python_version` is load-bearing for a different reason: it
        decides which `sys.version_info` branches are analysed and which stdlib
        signatures apply.
        """
        floor = self._floor()
        # PARSED, not matched. A substring check passed with the real target
        # commented out and a different one active beside it.
        # THE FILE THE TOOL WOULD LOAD, not the one this guard knows. ruff
        # resolves `.ruff.toml` / `ruff.toml` ahead of `pyproject.toml`, and
        # mypy resolves `mypy.ini` / `.mypy.ini` / `setup.cfg` ahead of it, so
        # dropping a two-line `.ruff.toml` with `target-version = "py312"`
        # changed what ruff targets while this assertion went on reading the
        # file it was pointed at and passing. Same shape as reading a comment
        # instead of code, one level up: the right FILE is part of the question.
        overriding = [name for name in (".ruff.toml", "ruff.toml", "mypy.ini",
                                        ".mypy.ini", "setup.cfg")
                      if (REPO / name).exists()]
        assert not overriding, (
            f"{overriding} exist(s) in the repository root and take precedence "
            "over pyproject.toml for ruff or mypy, so the targets asserted "
            "below are not necessarily the ones the tools load. Either fold "
            "the settings into pyproject.toml or teach this test to read the "
            "winning file")
        config = tomllib.loads(PYPROJECT.read_text())
        expected = "py" + floor.replace(".", "")
        assert config["tool"]["ruff"]["target-version"] == expected, (
            f"ruff targets {config['tool']['ruff']['target-version']!r} and the "
            f"floor is {floor} — it would resolve builtins for another version")
        assert config["tool"]["mypy"]["python_version"] == floor, (
            f"mypy types against {config['tool']['mypy']['python_version']!r} "
            f"and the floor is {floor}")

    def test_every_lock_is_compiled_for_the_floor(self):
        """The header uv wrote into each generated `.txt`.

        Echoed BY uv, so it is a fact about how the lock was made rather than a
        recipe for making the next one — the one copy of the floor W5a could
        not delete, because the lock carries no other record of it.

        A lock compiled for a version OTHER than the floor resolves markers for
        an interpreter nobody runs: too low and it carries backports the tree
        does not need, too high and a conditional dependency the floor requires
        is missing from a file that installs with `--require-hashes`.
        """
        floor = self._floor()
        assert LOCKS, "no requirements*.txt lock in the repository root"
        for path in LOCKS:
            commands = _compile_commands(_visible(path.read_text()))
            assert commands, (
                f"{path.name} holds no `{UV_COMPILE}` command in its header. "
                "uv writes the command it ran there, so a lock without one was "
                "not generated by it — or had its header edited by hand")
            for argv in commands:
                version = _flag_value(argv, "--python-version")
                assert version is not None, (
                    f"{path.name}'s `{UV_COMPILE}` command names no "
                    "--python-version, so the lock resolves for whatever "
                    "interpreter happened to run uv")
                assert version == floor, (
                    f"{path.name} gives `uv pip compile` {version!r} and the "
                    f"floor is {floor!r}, and this header is uv's record of "
                    "the version the lock was resolved for")

    def test_the_recipe_is_the_script_and_never_a_copy_of_it(self):
        """W5a: the places that tell a contributor how to regenerate a lock
        say to run the script, and NO tracked file carries the command.

        Five hand-typed copies of the floor stood in those recipes. Pointing at
        the script deleted them; a pasted `uv pip compile ... --python-version`
        is exactly the copy that is left behind at the next floor move, and a
        list of documents cannot see one pasted into a new document — so the
        absence is SWEPT. Exempt: the locks, whose header is uv's own record;
        the changelog, which is history; and the tests, which quote commands
        as fixtures.
        """
        for path in LOCK_INPUTS + RECIPE_SOURCES:
            assert "scripts/recompile-locks.sh" in _visible(path.read_text()), (
                f"{path.name} no longer tells a reader how to regenerate a "
                "lock: name scripts/recompile-locks.sh")
        # NUL-separated: a tracked path with a space in it split on whitespace
        # into names that do not exist, and the read below skipped each one.
        tracked = subprocess.run(["git", "ls-files", "-z"], cwd=REPO,
                                 check=True, capture_output=True,
                                 text=True).stdout.split("\0")
        copies = []
        for name in filter(None, tracked):
            path = REPO / name
            if path in LOCKS or name.startswith(("tests/", "docs/changelog/")):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if UV_COMPILE_RE.search(text) and _compile_commands(_visible(text)):
                copies.append(name)
        assert not copies, (
            f"{copies} carry a `{UV_COMPILE}` invocation. The flags and the "
            "floor live in scripts/recompile-locks.sh; a second copy of the "
            "command is one more to move by hand")

    @staticmethod
    def _run_recompile(tmp_path, floor="3.13", inputs=("requirements.in",
                                                        "requirements-dev.in"),
                       args=()):
        """The script run in a COPY of its layout, with `uv` a recorder.

        A copy, so `cd "$(dirname "$0")/.."` lands somewhere a test owns and a
        real lock is never rewritten; a stub `uv`, because what is under test
        is what the script ASKS uv for, and that is the argv.
        """
        (tmp_path / "scripts").mkdir()
        script = tmp_path / "scripts" / RECOMPILE.name
        script.write_text(RECOMPILE.read_text())
        (tmp_path / ".python-version").write_text(floor + "\n")
        for name in inputs:
            (tmp_path / name).write_text("")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = tmp_path / "uv.log"
        stub = bin_dir / "uv"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "' + str(log) + '"\n')
        stub.chmod(0o755)
        result = subprocess.run(
            ["sh", str(script), *args], capture_output=True, text=True,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
        calls = log.read_text().splitlines() if log.exists() else []
        return result, calls

    def test_the_script_compiles_every_lock_for_the_floor(self, tmp_path):
        """Each `requirements*.in` once, into its own `.txt`, for the version
        `.python-version` holds — a version no lock uses, so a script that
        typed the floor instead of reading it cannot pass."""
        names = ("requirements", "requirements-dev", "requirements-test")
        result, calls = self._run_recompile(
            tmp_path, floor="3.99", inputs=tuple(f"{n}.in" for n in names))
        assert result.returncode == 0, result.stderr
        compiled = {}
        for call in calls:
            argv = shlex.split(call)
            assert _flag_value(argv, "--python-version") == "3.99", call
            compiled[argv[2]] = _flag_value(argv, "-o")
        assert len(calls) == len(names), calls
        assert compiled == {f"{n}.in": f"{n}.txt" for n in names}, compiled

    def test_the_script_asks_for_what_every_lock_header_records(self,
                                                               tmp_path):
        """The script's argv against the command uv echoed into each lock, with
        the real inputs and the real floor — the flags live in the script and
        the headers and nowhere in this test. Different flags would make the
        first regeneration a silent re-resolution rather than a reproduction.
        """
        result, calls = self._run_recompile(
            tmp_path, floor=self._floor(),
            inputs=tuple(path.name for path in LOCK_INPUTS))
        assert result.returncode == 0, result.stderr
        asked = [["uv", *shlex.split(call)] for call in calls]
        assert LOCKS, "no requirements*.txt lock in the repository root"
        for lock in LOCKS:
            recorded = _compile_commands(lock.read_text())[0]
            ran = [argv for argv in asked
                   if _flag_value(argv, "-o") == lock.name]
            assert len(ran) == 1, (
                f"scripts/recompile-locks.sh does not produce {lock.name}")
            assert sorted(ran[0]) == sorted(recorded), (
                f"{lock.name} was compiled with `{' '.join(recorded)}` and "
                f"the script runs `{' '.join(ran[0])}`")

    def test_the_script_passes_extra_arguments_to_every_compile(self, tmp_path):
        result, calls = self._run_recompile(
            tmp_path, args=("--upgrade-package", "urllib3"))
        assert result.returncode == 0, result.stderr
        assert len(calls) == 2 and all(
            c.endswith(" --upgrade-package urllib3") for c in calls), calls

    @pytest.mark.parametrize("floor", ["", "3", "3.13.1", "python3.13", "4.0"])
    def test_the_script_refuses_a_floor_that_is_not_a_release(self, tmp_path,
                                                              floor):
        """Compiling for `''` lets uv pick an interpreter; `3.13.1` is a patch
        the headers do not record. Neither reaches uv."""
        result, calls = self._run_recompile(tmp_path, floor=floor)
        assert result.returncode == 2, (result.stdout, result.stderr)
        assert not calls, calls
        assert ".python-version" in result.stderr

    def test_the_script_refuses_a_tree_with_no_lock_input(self, tmp_path):
        result, calls = self._run_recompile(tmp_path, inputs=())
        assert result.returncode == 2
        assert not calls

    def test_the_engine_refuses_below_the_floor_and_not_below_something_else(
            self):
        """`MIN_PYTHON` is the only one of these a taxpayer meets.

        Every other copy fails a contributor's build; this one blocks a return
        with a remedy naming a version to install. If the two ever disagree,
        decide which MOVES rather than which is wrong — this assertion is
        symmetric, and `MIN_PYTHON` is the side with a taxpayer behind it.
        """
        floor = self._floor()
        # The STRING is what every other assertion here compares against, and
        # the TUPLE is what refuses the return. Decoupling them — `MIN_PYTHON =
        # (3, 12)` beside `MIN_PYTHON_STR = "3.13"` — left this class green and
        # the refusal test self-consistent at 3.12, so the toolkit accepted an
        # interpreter below the locks it installs from. They are derived from
        # each other in the module; this is what makes that load-bearing.
        assert P.MIN_PYTHON_STR == ".".join(str(n) for n in P.MIN_PYTHON), (
            f"MIN_PYTHON_STR is {P.MIN_PYTHON_STR!r} and MIN_PYTHON is "
            f"{P.MIN_PYTHON}; the string is what this class compares and the "
            "tuple is what refuses a taxpayer's interpreter, so a gap between "
            "them is a floor that is checked in one place and enforced in "
            "another")
        assert P.MIN_PYTHON_STR == floor, (
            f"preflight refuses below {P.MIN_PYTHON_STR} and .python-version "
            f"says {floor}; these two disagree and one of them has to move — "
            "MIN_PYTHON is the one that refuses a return")

    def test_the_refusal_fires_at_the_floor_and_one_minor_below_it(self):
        """The BEHAVIOUR, not the constant that is supposed to produce it.

        The test above compares `MIN_PYTHON_STR` against `.python-version` and
        nothing else, so `check()` could compare `sys.version_info` against any
        version at all and stay green: rewriting the comparison to
        `>= (3, 12)` left both of this file's modules passing. A constant is
        only the floor if the refusal uses it.

        Driven through `check()` with a substituted `sys.version_info` rather
        than by reading the source, because what a taxpayer meets is the
        verdict. `modules=()` keeps it to the version question — the engine
        roster has its own tests and loading it here would make this one slow
        and about something else.
        """
        major, minor = P.MIN_PYTHON
        for probe, expected in (((major, minor - 1, 9, "final", 0), False),
                                ((major, minor, 0, "final", 0), True),
                                ((major, minor + 1, 0, "final", 0), True)):
            with mock.patch.object(sys, "version_info", probe):
                report = P.check(modules=())
            assert report.python_ok is expected, (
                f"on Python {probe[0]}.{probe[1]} the engine reports "
                f"python_ok={report.python_ok}; the floor is "
                f"{P.MIN_PYTHON_STR}, so it should be {expected}. The refusal "
                "is comparing against something other than MIN_PYTHON")

    def test_the_matrix_runs_the_floor_and_never_anything_older(self):
        """The floor is the LOWEST leg, there is a leg above it, and the job
        actually INSTALLS what the matrix declares.

        "3.13 is supported", "3.13 is declared" and "3.13 is tested" are three
        different claims and only the last is worth making. A declared leg that
        nothing installs is the same lie as no leg at all, and review found two
        ways to have one: hardcode `python-version: "3.13"` in the setup step so
        both legs install the same interpreter, or add a matrix `exclude` that
        deletes the floor leg. Both left every assertion in this class green
        while the floor ran nowhere.
        """
        floor_release = _release(self._floor())
        job = self._matrix_job()
        legs = self._legs()
        assert min(legs) == floor_release, (
            f"CI's oldest leg is {min(legs)} and the floor is {self._floor()}")
        assert max(legs) > floor_release, (
            "the matrix has collapsed to the floor alone — the interpreter a "
            "contributor is actually on would then be tested nowhere")

        excluded = job.get("strategy", {}).get("matrix", {}).get("exclude", [])
        assert not excluded, (
            f"the matrix excludes {excluded}. An exclusion can delete the floor "
            "leg while leaving it declared above, which is the shape of a "
            "version this repository claims and never runs")

        setup = [s for s in job.get("steps", [])
                 if str(s.get("uses", "")).startswith("actions/setup-python")]
        assert setup, "the test job no longer sets up Python at all"
        for step in setup:
            pinned = str(step.get("with", {}).get("python-version", ""))
            # EXACTLY the matrix value. A substring check passed
            # `${{ matrix.python-version == '3.13' && '3.14' ||
            # matrix.python-version }}`: it mentions the matrix and evaluates
            # to 3.14 on BOTH legs, which is the matrix as decoration — the one
            # thing this assertion exists to refuse.
            assert _normalise_expr(pinned) == "matrix.python-version", (
                f"the setup step installs {pinned!r} rather than the matrix's "
                "leg verbatim, so the legs can be made to resolve to the same "
                "interpreter and the matrix is decoration")

    @staticmethod
    def _workflow() -> dict:
        """The workflow PARSED, not matched.

        Nine spellings of a version slipped past the regexes that stood here,
        every one of them valid YAML — flow style (`"python-version": "3.12"`),
        a folded scalar, `${{ '3.12' }}`, a bare scalar, a matrix exclusion. A
        pattern can always be given one more spelling to miss; a parser reads
        the document GitHub reads.
        """
        return yaml.safe_load(_ci())

    @classmethod
    def _jobs(cls) -> dict:
        jobs = cls._workflow().get("jobs")
        assert jobs, "the workflow declares no jobs"
        return jobs

    @staticmethod
    def _matrix_versions(job: dict) -> "list[tuple]":
        """Every interpreter `job`'s matrix runs: the bare list, plus each
        `include:` leg, minus each `exclude:` — the one reading of a matrix
        this class has, so "is this job matrixed" and "which legs" cannot
        disagree.

        Reading the bare list alone let an `include:` entry add a THIRD leg on
        3.12 — below the floor, running the suite, with `min(legs)` still
        reading 3.13 off the list and every other assertion still agreeing.
        Closing `exclude:` and leaving `include:` open was the fix carrying the
        next defect, which is this repository's most reliable shape. And since
        W5a this workflow declares its legs through `include:` alone, so a
        reader that asked only for the list found no matrixed job at all.
        """
        matrix = job.get("strategy", {}).get("matrix", {})
        if not isinstance(matrix, dict):
            return []
        legs = [_release(leg) for leg in matrix.get("python-version", []) or []]
        for entry in matrix.get("include", []) or []:
            if isinstance(entry, dict) and "python-version" in entry:
                version = _release(entry["python-version"])
                if version not in legs:
                    legs.append(version)
        for entry in matrix.get("exclude", []) or []:
            if isinstance(entry, dict) and "python-version" in entry:
                version = _release(entry["python-version"])
                if version in legs:
                    legs.remove(version)
        return legs

    @classmethod
    def _matrix_job(cls) -> dict:
        matrixed = [j for j in cls._jobs().values() if cls._matrix_versions(j)]
        assert len(matrixed) == 1, (
            f"{len(matrixed)} jobs declare a python-version matrix; this class "
            "is written for exactly one and would otherwise assert about "
            "whichever it found first")
        return matrixed[0]

    @classmethod
    def _legs(cls) -> "list[tuple]":
        """Every interpreter the matrixed job runs."""
        return cls._matrix_versions(cls._matrix_job())

    def test_every_single_version_job_pins_the_floor(self):
        """Lint, Windows and the audit run on ONE interpreter each.

        Each is the same choice for the same reason — one interpreter, so the
        result is reproducible — so a pin left behind here runs a version
        nothing else in the file names.

        Read from the parsed document, so a pin written in flow style, as a
        folded scalar, or wrapped in `${{ }}` is a pin like any other. The
        regex that stood here silently SKIPPED the spellings it did not know,
        and the other jobs then satisfied its non-empty check.
        """
        floor = self._floor()
        pinned = {}
        for name, job in self._jobs().items():
            if self._matrix_versions(job):
                continue                      # the matrixed job is not a pin
            steps = job.get("steps", [])
            setups = [step for step in steps
                      if str(step.get("uses", "")).startswith(
                          "actions/setup-python")]
            # EVERY setup step, keyed per step. Keying by job kept the LAST one,
            # so a job that sets up twice could pin anything on the first.
            for i, step in enumerate(setups):
                with_ = step.get("with", {}) or {}
                # THE FILE, NOT A COPY OF IT (W5a). A literal here is a second
                # statement of the floor that has to be moved by hand, and
                # setup-python takes `python-version` OVER the file when both
                # are given — so a literal left beside the file wins silently.
                assert "python-version" not in with_, (
                    f"{name} sets up Python {with_['python-version']!r} by "
                    "hand. Single-version jobs read `python-version-file: "
                    ".python-version`, and setup-python prefers a literal "
                    "over the file when both are present")
                source = str(with_.get("python-version-file", ""))
                assert source, (
                    f"{name} sets up Python without naming a version, so it "
                    "runs whatever the runner defaults to")
                pinned[f"{name}[{i}]"] = source
            if setups:
                continue
            # A job with NO setup step contributed nothing and was invisible:
            # deleting lint's setup-python left ruff and mypy on the runner
            # default under a green tick, because the other jobs still
            # satisfied the non-empty check below.
            assert name in NO_INTERPRETER_JOBS, (
                f"{name} has no actions/setup-python step and is not in "
                f"NO_INTERPRETER_JOBS, so if it runs anything Python it uses "
                f"whatever the runner ships rather than the floor ({floor}). "
                "Add the setup step, or add the job to that set to say it "
                "needs no interpreter")
        assert pinned, "no job pins a single interpreter any more"
        wrong = {n: v for n, v in pinned.items()
                 if v.strip("\"' ") != PYTHON_VERSION.name}
        assert not wrong, (
            f"single-version jobs read their interpreter from {wrong}, and "
            f"the floor ({floor}) is named in {PYTHON_VERSION.name}")

    def test_the_documents_that_name_both_legs_name_the_matrix(self):
        """The MATRIX claim, which is not the floor claim and drifts alone.

        `.python-version` cannot settle this one: "CI runs 3.13 and 3.14" is
        true or false about the matrix, and adding a leg leaves every sentence
        saying it correct about the floor and wrong about the run. It was
        checked in the issue template, which a REPORTER reads, and nowhere in
        the three documents a CONTRIBUTOR reads.
        """
        legs = {"%d.%d" % leg for leg in self._legs()}
        for path, anchor in MATRIX_PROSE:
            block = _block_at(_visible(path.read_text()), anchor)
            named = set(PROSE_VERSION.findall(block))
            assert named == legs, (
                f"{path.name}, under {anchor!r}, names {sorted(named)} in the "
                f"claim about which interpreters CI runs; the matrix is "
                f"{sorted(legs)}. If the document is RIGHT and this is wrong, "
                "the claim has been reformatted out of the paragraph this "
                "reads — it runs from the anchor line to a blank line or the "
                "next list item, so a claim moved into a table or a bullet "
                "list needs its anchor moved with it")

    def test_the_prose_names_no_interpreter_but_the_floor_and_the_legs(self):
        """The claim `FLOOR_CLAIM` cannot see, which is every one it was not
        taught.

        Spellings of a SECOND, contradictory minimum were added to these
        documents and left the pattern above green: `3.12 or later`, `Requires
        **Python 3.12**+` (the emphasis marks sit between the number and the
        `+`), and the same sentence broken across a soft line wrap. Widening
        `FLOOR_CLAIM` buys exactly one round; this repository has learned that
        three times over.

        So the question is asked the other way round, where the answer does not
        depend on anticipating a spelling: these four documents may name the
        floor and the legs, and no other interpreter version at all. A reader
        cannot act on a version that is not written down, and the set of
        versions written down is a thing this can enumerate exactly.
        """
        floor = self._floor()
        allowed = {floor} | {"%d.%d" % leg for leg in self._legs()}
        for path in FLOOR_PROSE:
            visible = _visible(path.read_text())
            minimums = _minimum_claims(visible)
            assert minimums <= {floor}, (
                f"{path.name} offers {sorted(minimums)} as a minimum "
                f"interpreter and the floor is {floor}. A minimum stated ABOVE "
                "the floor is as wrong as one below it, and naming a matrix "
                "leg does not make it a floor")
            named = _interpreter_versions(visible)
            assert named <= allowed, (
                f"{path.name} names {sorted(named - allowed)}, which is "
                f"neither the floor nor a matrix leg ({sorted(allowed)}). A "
                "reader takes the interpreter they need from these four files, "
                "so a version mentioned here is a version claimed here. IF "
                "THAT IS NOT AN INTERPRETER VERSION — a dependency pin like "
                "`openpyxl==3.1.5`, a rate, a section number — this assertion "
                "has misdiagnosed itself — say so in the sentence, so the "
                "number does not sit next to `python`, `interpreter`, `floor`, "
                "`leg` or `venv` within a line of it. Do not widen `allowed` "
                "to silence it and do not make the PATTERN cleverer about "
                "spelling: being independent of spelling is the property four "
                "bypasses were built out of")

    def test_the_steps_that_select_a_leg_name_the_floor(self):
        """The copies a `python-version:` scan cannot see, and they FAIL SILENT.

        Coverage runs on the floor leg and the plain pytest run on every other,
        selected by `if:` conditions on `matrix.python-version`. Left behind at
        the next floor move they do not go red: the negation is true on both
        legs so pytest runs twice, the equality is true on neither, and the
        coverage run and its artifact upload simply never execute under a green
        tick.

        EACH CONDITION IS BOUND TO THE STEP IT GATES, by name. Collecting them
        into a flat list and requiring one `==` and one `!=` somewhere in it
        left the operators free to SWAP: coverage then runs on the newest leg
        while the upload step still waits for the floor leg to produce a file
        it never writes. And the condition is compared WHOLE rather than
        searched, because `matrix.python-version != '3.13' && false` mentions
        the matrix, names only the floor, and runs the step on no leg at all.
        """
        floor = self._floor()
        job = self._matrix_job()

        # The JOB's own gates, which no amount of reading its steps can see.
        # `if: false` on the job skips both legs, and `continue-on-error: true`
        # runs them and stops their failures mattering. Either is a green tick
        # over a matrix that tested nothing.
        assert "if" not in job, (
            f"the matrixed job is gated on {job.get('if')!r}. A job-level "
            "condition decides whether EVERY leg runs, and nothing else here "
            "reads it")
        # ...and neither does a job it WAITS ON. A gate job carrying
        # `if: github.event_name == 'never'` with `needs: [gate]` here skips
        # both legs without this job holding a condition of its own: GitHub
        # skips a job whose dependency was skipped. The assertion above was
        # exactly right and exactly one hop short.
        jobs = self._jobs()
        pending = list(job.get("needs") or [])
        seen = set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            upstream = jobs.get(name, {})
            assert "if" not in upstream, (
                f"the matrixed job needs {name!r}, which is gated on "
                f"{upstream.get('if')!r}. A skipped dependency skips this job, "
                "so that condition decides whether either leg runs")
            pending.extend(upstream.get("needs") or [])
        assert not _is_truthy(job.get("continue-on-error")), (
            f"the matrixed job is continue-on-error "
            f"({job.get('continue-on-error')!r}), so both legs report success "
            "whatever they find")

        # Keyed by name, so DUPLICATE names have to be refused before the
        # dict is built: two steps called "Run tests", the real one gated on
        # `&& false` and a decoy carrying the clean condition, left the decoy
        # in the dict and the real one unread. The 3.14 leg ran nothing.
        named_steps = [str(step.get("name", "")) for step in job.get("steps", [])
                       if "if" in step]
        assert len(named_steps) == len(set(named_steps)), (
            f"the matrixed job has two conditioned steps sharing a name: "
            f"{sorted(n for n in named_steps if named_steps.count(n) > 1)}. "
            "This asserts about steps BY name, so a duplicate hides whichever "
            "of the pair it does not reach")
        conditioned = {str(step.get("name", "")): str(step["if"])
                       for step in job.get("steps", []) if "if" in step}
        assert set(conditioned) == set(LEG_STEPS) | NON_LEG_STEPS, (
            f"the matrixed job's conditioned steps are {sorted(conditioned)}; "
            f"this asserts about {sorted(LEG_STEPS)} as leg-selecting and "
            f"{sorted(NON_LEG_STEPS)} as conditioned for some other reason. "
            "Renaming or adding one comes back through here on purpose — which "
            "leg a step belongs on cannot be asked of a step this does not "
            "know. A step gated on something that is NOT the matrix leg "
            "(`if: failure()`, a trigger guard) belongs in NON_LEG_STEPS")
        for name, expected in LEG_STEPS.items():
            # Split on top-level `&&`: the leg term must be one of the
            # conjuncts EXACTLY, and every other conjunct must read a real
            # context. That admits `&& github.event_name != 'schedule'`, which
            # is what this workflow already does to `pii-scan` and which the
            # first draft refused; it still refuses `&& false`, which names no
            # context, cannot vary per leg, and runs the step nowhere while
            # reading as a condition about the matrix.
            conjuncts = _normalise_expr(conditioned[name]).split("&&")
            assert expected in conjuncts, (
                f"{name!r} is gated on {conditioned[name]!r}, which has no "
                f"`{expected}` term. That step belongs on "
                f"{'every other leg' if expected.startswith('!') else 'the coverage leg'}"
                f", and the coverage leg is the floor, {floor}")
            others = [c for c in conjuncts if c != expected]
            unvarying = [c for c in others
                         if not re.match(r"(github|runner|inputs|env|needs|"
                                         r"steps|job|matrix)\.", c)]
            assert not unvarying, (
                f"{name!r} is gated on {conditioned[name]!r}, which ANDs on "
                f"{unvarying} — terms that read no context, so they cannot "
                "vary per leg and per run. `&& false` is the version of that "
                "which reports success while the step runs nowhere")
        for step in job.get("steps", []):
            assert not _is_truthy(step.get("continue-on-error")), (
                f"step {step.get('name')!r} is continue-on-error inside the "
                "matrixed job")

    def test_the_floor_leg_and_only_the_floor_leg_runs_coverage(self):
        """Where the floor went when it left the step conditions (W5a).

        The steps ask `matrix.coverage`, so the floor is stated once, on the
        leg, and this is what makes that one statement the right one: every leg
        carries the flag as a real boolean, and exactly one carries `true`.
        None is the coverage run and its upload executing nowhere under a green
        tick; two is two reports and no decision about which is the one.
        """
        floor = _release(self._floor())
        matrix = self._matrix_job()["strategy"]["matrix"]
        flags = {}
        for entry in matrix.get("include", []) or []:
            if "python-version" not in entry:
                continue
            leg = _release(entry["python-version"])
            assert "coverage" in entry, (
                f"the {entry['python-version']} leg carries no `coverage` "
                "flag, so whether it runs coverage is decided by an absence")
            assert isinstance(entry["coverage"], bool), (
                f"the {entry['python-version']} leg's coverage flag is "
                f"{entry['coverage']!r}, not a boolean; GitHub reads a "
                "non-empty string as true")
            flags[leg] = entry["coverage"]
        legs = self._legs()
        assert set(flags) == set(legs), (
            f"the legs are {sorted(legs)} and the coverage flag is set on "
            f"{sorted(flags)}. A leg from the bare list has no flag, so the "
            "steps cannot tell which leg it is")
        covered = [leg for leg, on in flags.items() if on]
        assert covered == [floor], (
            f"coverage runs on {covered} and the floor is {floor}. The "
            "report is for the interpreter the locks are compiled for, and "
            "on exactly one leg")

    def test_the_documents_a_reader_believes_state_the_floor(self):
        """AGENTS.md, README, CONTRIBUTING and QUICKSTART — where 3.10+ lived.

        The claim went unchecked for the life of the repository because prose is
        the one copy no job installs. Every `Python 3.N+` and `Python 3.N or
        newer` in these four has to BE the floor — a version named as one leg of
        the matrix reads differently and is not matched here.
        """
        floor = self._floor()
        for path in FLOOR_PROSE:
            claims = set(FLOOR_CLAIM.findall(_visible(path.read_text())))
            assert claims, (
                f"{path.name} states no minimum interpreter in a form this "
                "recognises — `Python X.Y+` or `Python X.Y or newer`. A reader "
                "takes the floor from these four files, so if the sentence is "
                "there in another wording, spell it one of those two ways "
                "rather than widening the pattern; the test below is what "
                "catches a second claim in any wording at all")
            assert claims == {floor}, (
                f"{path.name} claims support for {sorted(claims)} and the "
                f"floor is {floor}")

    def test_the_second_floor_claim_in_a_document_moves_with_the_first(self):
        """The half-sentence and the heading `FLOOR_CLAIM` cannot see.

        Not a widening of that pattern. A document names OTHER versions
        legitimately — CONTRIBUTING's next sentence says a current macOS gives
        you 3.14, which is true and is CI's other leg — so the question is asked
        of a located SENTENCE rather than of the file, and the sentence is found
        by the claim it makes instead of by the number in it.
        """
        floor = self._floor()
        for path, anchor in FLOOR_SENTENCES:
            lines = [line for line in _visible(path.read_text()).split("\n")
                     if anchor in line]
            assert len(lines) == 1, (
                f"{path.name} has {len(lines)} lines saying {anchor!r}; this "
                "assertion is anchored to exactly one, so a rewrite that moves "
                "or DUPLICATES the claim has to come back through here rather "
                "than leave a check reading nothing. `The floor is` is anchored "
                "without its heading marks for that reason: a second sentence "
                "opening that way is a second floor claim, wherever it sits")
            named = set(re.findall(r"\b3\.\d+\b", lines[0]))
            assert named == {floor}, (
                f"{path.name} says {lines[0].strip()!r}, naming "
                f"{sorted(named)}; the floor is {floor}")

    def test_this_suite_is_running_on_an_interpreter_ci_runs(self):
        """The claim nobody was making, and `>=` is not it.

        The first draft asserted the running interpreter was at or above the
        floor. That is the CONTRACT, but it is not the finding: on this machine
        before this change `.python-version` said 3.12, the interpreter was
        3.14, and `(3,14) >= (3,12)` was green throughout the period the
        docstring describes as the defect. It would have detected nothing.

        The finding was about MEMBERSHIP — a local green being evidence about a
        version CI actually runs — so that is what this asserts. Red on an
        interpreter outside the matrix is the correct verdict and the actionable
        one: either run on a leg, or the matrix is missing a version this
        project means to support. This module is not selected by the hook, so
        being red here costs a full run and never a tool call.
        """
        running = sys.version_info[:2]
        legs = self._legs()
        assert running in legs, (
            f"this suite is running on {running[0]}.{running[1]}, which CI does "
            f"not: its legs are {sorted(legs)}. A green here is not evidence "
            "about either of them")

    def test_the_issue_template_names_the_versions_ci_actually_runs(self):
        """The one copy a REPORTER reads, and it is a matrix claim not a floor.

        A bug report's first field asks which interpreter the reporter is on,
        and the hint tells them which ones CI covers. Left behind it invites a
        report against a version nobody runs, filed in good faith.
        """
        legs = ["%d.%d" % leg for leg in self._legs()]
        # PARSED, so the text has to be text GitHub renders. Scanning the file
        # for a line saying "CI runs" passed with that whole line commented
        # out: the field then reaches a reporter with no hint at all, and the
        # check went on reading the hint in the comment.
        body = yaml.safe_load(ISSUE_TEMPLATE.read_text())["body"]
        fields = {field.get("id"): field for field in body}
        assert "python" in fields, (
            "the bug-report template has no field with id `python`, so a "
            f"reporter is never asked which interpreter they are on; CI runs "
            f"{legs}")
        hint = str(fields["python"].get("attributes", {}).get("description", ""))
        assert "CI runs" in hint, (
            f"the `python` field's description is {hint!r}, which no longer "
            "tells a reporter which interpreters CI runs")
        named = re.findall(r"\d+\.\d+", hint)
        assert named == legs, (
            f"the template tells reporters CI runs {named} and the matrix is "
            f"{legs}")
