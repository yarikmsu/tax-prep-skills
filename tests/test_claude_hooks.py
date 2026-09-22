"""Claude Code hook suite — drives the real scripts in `.claude/hooks/`.

These hooks fire at TOOL time, which is the one thing the git hooks cannot do.
`scripts/pre-commit-pii-scan.sh` runs at COMMIT time and every file the
permissions hook protects is gitignored, so none of them ever reaches a commit
for that scan to see. The two layers are complements, and neither substitutes
for the other: the git hooks work for Codex, Cursor, Kimi and Copilot, and these
do not.

Every assertion below runs the actual shell script. A hook is shipped
configuration that nothing else exercises — no import graph reaches it, no type
checker reads it — so a test that inspected the source text instead would be
satisfied by a script that cannot run at all.

The failure paths are the load-bearing half. A hook that always exits 0 is
indistinguishable from a hook that never fires, and the concrete regression
guarded here was measured, not imagined: `pytest … | tail -20 || exit 2` is a
pipeline whose status is `tail`'s unless `pipefail` happens to be set, so the
failure branch never runs and a red suite passes silently. Mutating the script
back to that form turns two assertions below red.
"""
import ast
import fnmatch
import functools
import importlib.util
import json
import math
import os
import re
import resource
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

# W4: this module drives the real hook in a subprocess once per case and a
# handful of cases sleep on purpose, which is why it dominates every total in
# skill-invariants.sh MEASURED COSTS. `-m "not slow"` is the engine-only run.
pytestmark = pytest.mark.slow


REPO = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO / ".claude" / "hooks"
SETTINGS = REPO / ".claude" / "settings.json"
SKILL_HOOK = HOOKS_DIR / "skill-invariants.sh"
PERMS_HOOK = HOOKS_DIR / "harden-runtime-perms.sh"
# The port the script runs its selection through, since W3 slice 2. The
# shell builds this path a third time from `$0` — it must resolve beside the
# script rather than against the repository — but the Python side reads it
# here and nowhere else.
SELECTION_PORT = HOOKS_DIR / "lib" / "hook_selection.py"
SKILL_STAMP = Path(".claude") / ".skill-invariants-stamp"


def _perms_hook_protected_names():
    """The permissions hook's own `for f in …` list, split into exact filenames
    and shell globs.

    Parsed from the loop rather than scraped out of the whole file, and the
    asymmetry is the reason. A loose findall over the script is sound for "is X
    present?" — a name quoted in a comment answers yes and the answer stays
    true — and useless for "is anything present that should NOT be?", because
    it also matches prose: `tax_law_<year>.json` in a comment yields
    `year.json`. The reverse direction needs the list git actually chmods, so
    it has to come from the loop itself.

    Anchored on the first name rather than on `for f in` alone: the hook runs a
    second such loop over `./*.pdf ./*.bak …`, and a regex that could match
    either would compare the wrong list the day the two are reordered.
    """
    m = re.search(r"^for f in (taxpayer_profile\.json(?:[^\n]*\\\n)*[^\n]*?); do$",
                  PERMS_HOOK.read_text(), re.M)
    assert m, "the permissions hook's protected-file loop has moved"
    words = m.group(1).replace("\\\n", " ").split()
    exact = {w for w in words if not re.search(r"[*?\[\]]", w)}
    globs = {w for w in words if re.search(r"[*?\[\]]", w)}
    return exact, globs

# Read out of the hook rather than written down here, so bumping the format
# without updating the tests is a collection error rather than a suite that
# silently starts asserting against a stamp shape the hook no longer writes.
# The version is load-bearing twice over: line 1 of the stamp IS this string,
# and every line after it is a check the previous pass left red.
_STAMP_VERSION_MATCH = re.search(r"^STAMP_VERSION='(\d+)'",
                                 SKILL_HOOK.read_text(), re.M)
assert _STAMP_VERSION_MATCH, "the skill hook no longer declares STAMP_VERSION"
SKILL_STAMP_VERSION = _STAMP_VERSION_MATCH.group(1)

# The seven modules the skill hook can run, named here so a test can say which
# one a given edit is supposed to select. It said "four" for several rounds
# after the fifth, sixth and seventh were added directly beneath it. `test_the_hook_runs_test_files_that
# _exist` pins that these paths resolve; this set pins what they MEAN.
PORTABILITY = "tests/test_portability.py"
CAPABILITIES = "tests/test_capabilities.py"
AGENTS = "tests/test_claude_agents.py"
HOOKS = "tests/test_claude_hooks.py"
PREFLIGHT = "tests/test_preflight.py"
SECUREFS = "tests/test_secure_fs.py"
HOSTGUARD = "tests/test_host_guard.py"

# The same seven, paired with the shell variable each one sets. The `RUN_`/`=1`
# decoration is what it is because a deleted text scanner searched an arm's
# BODY for that exact line; nothing reads an arm body now — `_ARMS` is data —
# and `_PORT_NAME` below strips both ends back off. Kept in this shape because
# the DECORATED form is still what the script declares and what
# `test_the_check_name_lists_agree` matches on; the strip is stated once.
_RUN_FLAG = {
    PORTABILITY: "RUN_PORTABILITY=1",
    CAPABILITIES: "RUN_CAPABILITIES=1",
    AGENTS: "RUN_AGENTS=1",
    HOOKS: "RUN_HOOKS=1",
    PREFLIGHT: "RUN_PREFLIGHT=1",
    SECUREFS: "RUN_SECUREFS=1",
    HOSTGUARD: "RUN_HOSTGUARD=1",
}
ALL_MODULES = tuple(_RUN_FLAG)

# ...and the same seven under the names the PORT uses, which are the shell
# flag suffixes. Stated once: the conversion is a slice off both ends of a
# `_RUN_FLAG` value, and written inline it is an undocumented `[4:-2]` at every
# site. It also DROPS `HOOKSDEPS`, which the port has and this map does not,
# because that flag selects node ids rather than a module file — one stated
# omission instead of one silent one per copy.
_PORT_NAME = {m: flag[4:-2] for m, flag in _RUN_FLAG.items()}


def _module_paths(port_modules) -> set:
    """The test files a set of the port's module names stands for."""
    return {m for m in ALL_MODULES if _PORT_NAME[m] in port_modules}

# THE ARMS, PINNED. A second deliberate statement of `hook_selection._ARMS`,
# in source order, each arm's modules sorted.
#
# This is what the flip cost and what gets it back. Until W3 slice 2 the arms
# existed twice — once in bash, once in the port — and a differential required
# them to agree, so deleting or narrowing one meant editing two files. With one
# implementation left, an arm deleted from `_ARMS` takes its own coverage
# obligation with it and every test here stays green. The obligation lives
# here now.
#
# A second COPY OF THE DATA, not a second implementation: it cannot catch a
# wrong answer the table itself states, and it is not trying to. It catches the
# edit nobody meant to make, which is the half the differential was actually
# earning its keep on.
#
# ORDER IS LOAD-BearING and so it is a sequence, not a mapping: `docs/ops` and
# `tests` each appear in TWO arms with different answers, and first match wins.
# Keyed by pattern, the second of each pair would overwrite the first and the
# pin would assert the wrong one.
#
# A DICT OR A NESTED SEQUENCE, never a flat list of path strings. Every
# repository path written as an element of a flat module-level tuple or list is
# recovered by `_path_constants` and read as a file THIS module opens, which
# would then demand that each of these patterns select the hooks module. Nested
# one level deeper it is invisible to that locator — checked, not assumed.
_ARM_SNAPSHOT = (
    (('.agents/skills', '.agents/skills/*', '.claude/skills',
      '.claude/skills/*',),
     ('AGENTS', 'CAPABILITIES', 'PORTABILITY', 'PREFLIGHT', 'SECUREFS',)),
    (('.claude/agents', '.claude/agents/*.md',), ('AGENTS',)),
    (('docs/ops/review-subagent-acceptance.md',),
     ('AGENTS', 'PORTABILITY', 'PREFLIGHT',)),
    (('docs/contributing/residency-guardrails.md',), ('CAPABILITIES',)),
    (('docs/contributing/parse-boundary.md',), ('AGENTS',)),
    (('docs/ops/season-open-runbook.md',),
     ('AGENTS', 'PORTABILITY', 'PREFLIGHT',)),
    (('docs/ops',), ('AGENTS', 'PORTABILITY', 'PREFLIGHT',)),
    (('.claude/hooks', '.claude/hooks/*', '.claude/settings.json',),
     ('HOOKS',)),
    (('tests/test_claude_hooks.py',), ('HOOKS', 'HOSTGUARD',)),
    (('scripts/pre-commit-pii-scan.sh',),
     ('HOOKS', 'PORTABILITY', 'SECUREFS',)),
    (('agents/lib/capabilities.py', 'agents/lib/rollover.py',),
     ('AGENTS', 'CAPABILITIES', 'HOSTGUARD', 'PORTABILITY', 'PREFLIGHT',
      'SECUREFS',)),
    (('agents/lib/tax_law_fetcher.py',),
     ('AGENTS', 'HOSTGUARD', 'PORTABILITY', 'PREFLIGHT', 'SECUREFS',)),
    (('agents/lib/ptet.py',),
     ('AGENTS', 'HOSTGUARD', 'PORTABILITY', 'PREFLIGHT', 'SECUREFS',)),
    (('agents/lib/tax_computations', 'agents/lib/tax_computations/*',),
     ('AGENTS', 'HOSTGUARD', 'PORTABILITY', 'PREFLIGHT', 'SECUREFS',)),
    (('tests/test_claude_agents.py',), ('AGENTS', 'HOOKSDEPS', 'HOSTGUARD',)),
    (('agents/lib/secure_fs.py',),
     ('AGENTS', 'CAPABILITIES', 'HOSTGUARD', 'PORTABILITY', 'PREFLIGHT',
      'SECUREFS',)),
    (('agents', 'scripts',), ('SECUREFS',)),
    (('agents/lib',),
     ('AGENTS', 'CAPABILITIES', 'HOSTGUARD', 'PORTABILITY', 'PREFLIGHT',
      'SECUREFS',)),
    (('requirements.in', 'requirements.txt',), ('PREFLIGHT',)),
    (('agents/lib/*.sh',), ('PORTABILITY', 'PREFLIGHT', 'SECUREFS',)),
    (('agents/lib/*',),
     ('HOSTGUARD', 'PORTABILITY', 'PREFLIGHT', 'SECUREFS',)),
    (('tests/conftest.py',),
     ('AGENTS', 'CAPABILITIES', 'HOOKS', 'HOOKSDEPS', 'HOSTGUARD',
      'PORTABILITY', 'PREFLIGHT', 'SECUREFS',)),
    (('tests/test_preflight.py',), ('HOOKSDEPS', 'HOSTGUARD', 'PREFLIGHT',)),
    (('tests/test_secure_fs.py',), ('HOOKSDEPS', 'HOSTGUARD', 'SECUREFS',)),
    (('tests/test_host_guard.py',),
     ('HOOKSDEPS', 'HOSTGUARD', 'PORTABILITY',)),
    (('agents/templates/taxpayer_profile_template.json',), ('CAPABILITIES',)),
    (('tests/test_capabilities.py',),
     ('CAPABILITIES', 'HOOKSDEPS', 'HOSTGUARD',)),
    (('agents/templates/result_template.json',),
     ('CAPABILITIES', 'PORTABILITY',)),
    (('agents/templates/projection_inputs_template.json',),
     ('PORTABILITY',)),
    (('tests/skill_text.py',),
     ('AGENTS', 'CAPABILITIES', 'HOSTGUARD', 'PORTABILITY', 'PREFLIGHT',)),
    (('tests/engine_tree.py',),
     ('HOSTGUARD', 'PORTABILITY', 'PREFLIGHT', 'SECUREFS',)),
    (('docs/QUICKSTART.md', 'docs/contributing/agents-compat.md',),
     ('PORTABILITY', 'PREFLIGHT',)),
    (('CONTRIBUTING.md', 'README.md',), ('PORTABILITY', 'PREFLIGHT',)),
    (('agents/install.sh',), ('PORTABILITY', 'SECUREFS',)),
    (('CLAUDE.md',), ('AGENTS', 'PORTABILITY',)),
    (('pyproject.toml',), ('HOOKS',)),
    (('AGENTS.md',), ('PORTABILITY', 'PREFLIGHT',)),
    (('.github/ISSUE_TEMPLATE', '.github/ISSUE_TEMPLATE/bug_report.yml',),
     ('PREFLIGHT',)),
    (('.github/workflows/ci.yml',), ('AGENTS',)),
    (('LICENSE', '.gitignore', '.pii-allowlist',
      '.vscode/settings.json', 'docs/contributing',
      'docs/contributing/*.md', 'docs/ops', 'docs/ops/*.md',
      'docs/roadmap', 'docs/roadmap/*.md',),
     ('PORTABILITY',)),
    (('tests/test_portability.py',),
     ('HOOKSDEPS', 'HOSTGUARD', 'PORTABILITY',)),
    (('tests',), ('HOSTGUARD', 'PORTABILITY',)),
    (('agents/*.py', 'agents/*.sh', 'scripts/*.py', 'scripts/*.sh',),
     ('SECUREFS',)),
    (('tests', 'tests/*.py',), ('HOSTGUARD',)),
)

# The stamp is deliberately backdated one second, so a file written in the same
# coarse filesystem tick as the pass start still reads as newer than it. A test
# that wants a QUIESCENT tree therefore has to put its watched files more than
# that overlap into the past before the stamp-writing call — and `os.utime`
# cannot do it, because the scan compares CTIME (`-newercm`) and utime moves
# ctime to now. Sleeping is the only way to age a ctime.
STAMP_OVERLAP_SECONDS = 1.3

# The hook reads its payload with jq and no-ops without it — by design, since a
# hook that cannot identify its target must not guess. Skipping here matches
# that documented degrade rather than papering over it.
needs_jq = pytest.mark.skipif(shutil.which("jq") is None,
                              reason="the hook is a deliberate no-op without jq")

# GNU chmod's getopt permutes argv, so it keeps scanning for options past the
# mode argument and reads a bare `-return.pdf` as an unrecognized flag. BSD
# chmod (this project's macOS dev hosts) stops option scanning once it hits
# the mode and treats everything after as a filename, so it cannot reproduce
# the failure this guards against. `--version` is a cheap, direct GNU/BSD
# discriminator: GNU chmod understands it, BSD chmod does not.
needs_gnu_chmod = pytest.mark.skipif(
    subprocess.run(["chmod", "--version"], capture_output=True).returncode != 0,
    reason="BSD chmod does not permute argv, so it cannot reproduce GNU "
           "chmod reading a leading '-' as an option")


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _run_skill_hook(project_dir: Path, edited: str, path=None,
                    env_extra=None, script=None):
    """`script` drives a COPY of the hook, which the fail-closed tests need.

    They replace the selector beside the script — the only way to do that
    without touching the one the rest of this module runs — and everything
    else about the call is identical, so the payload shape and the env
    contract stay stated once rather than transcribed per test.

    `env_extra` is the other half of that: the inherited-environment tests
    need a contributor's profile in the call rather than a second copy of it.
    """
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir))
    if path is not None:
        env["PATH"] = path
    # For the inherited-environment tests: the hook has to survive whatever a
    # contributor's profile leaves lying around, which it was not doing.
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(script or SKILL_HOOK)],
        input=json.dumps({"tool_input": {"file_path": edited}}),
        text=True, capture_output=True, env=env)


def _chmod_logging_shim(root: Path):
    """A PATH shim recording every chmod the hook makes, then performing it.

    The mode a file ends up with cannot distinguish "the bounded branch hardened
    exactly this file" from "the whole-archive fallback hardened everything,
    this file included". The call log can, which is what makes it the only
    instrument that sees a bounded optimization quietly stop being bounded.
    """
    shim = root / "bin"
    shim.mkdir(exist_ok=True)
    log = root / "chmod.log"
    (shim / "chmod").write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$PERMS_CHMOD_LOG\"\n"
        "exec /bin/chmod \"$@\"\n"
    )
    (shim / "chmod").chmod(0o755)
    return {"PERMS_CHMOD_LOG": str(log),
            "PATH": f"{shim}:{os.environ['PATH']}"}, log


def _run_skill_hook_bash(project_dir: Path, command="python helper.py",
                         indent=None, env=None):
    """Drive the hook with a Bash payload — the branch with no file_path.

    A Bash payload is a command string, so nothing in it says which files the
    call touched. Everything the hook decides on this branch comes from the
    stamp scan, which is why these tests are the only ones that can tell the
    fast path from a hook that stopped running.
    """
    return subprocess.run(
        ["bash", str(SKILL_HOOK)],
        input=json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": command}}, indent=indent),
        text=True, capture_output=True,
        env=dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir),
                 **(env or {})))


def _shared_test_helpers() -> list:
    """Every non-test module under `tests/` that another module can import.

    DERIVED, because a hardcoded pair is the very thing the assertion below
    exists to replace: a third helper added tomorrow would have no arm, fall
    through to the generic `tests` arm, and be covered by nothing — while a
    two-name list went on reading as complete.

    `conftest.py` is out because it has its own arm that selects EVERY module
    (it can end collection before one test runs, so nothing narrower is safe),
    and `__init__.py` is out because it is the package marker and holds no
    code anyone imports by name.
    """
    # `Path(__file__).parent`, not `REPO / "tests"`: the watched-set locator
    # recovers `REPO / "<literal>"` chains out of this module's source and
    # would read that spelling as this module depending on the whole `tests`
    # directory — which would put the 115-second hooks module on every test
    # edit in the repository. The helpers live beside this file; say so.
    here = Path(__file__).resolve().parent
    return sorted(f"tests/{p.name}" for p in here.glob("*.py")
                  if not p.name.startswith("test_")
                  and p.name not in ("__init__.py", "conftest.py"))


@functools.cache
def _selection_port():
    """`.claude/hooks/lib/hook_selection.py`, loaded once by PATH.

    By path and not by import: `.claude` is not an identifier, so the
    directory holding the hook's Python has no dotted name to import it under.
    """
    spec = importlib.util.spec_from_file_location(
        "hook_selection", SELECTION_PORT)
    port = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(port)
    return port


def _first_arm(path: str):
    """The `(pattern, modules)` of the FIRST arm matching `path`, or None.

    First match wins, and `hook_selection`'s own docstring calls that order
    load-bearing — so it is encoded once here rather than walked separately by
    every test that needs it. Reading `_ARMS` rather than calling
    `select_modules_for` is deliberate where a caller is checking the ARMS: a
    test that calls the port to check the port compares it against itself.
    """
    for patterns, modules in _selection_port()._ARMS:
        hit = next((q for q in patterns if fnmatch.fnmatchcase(path, q)), None)
        if hit is not None:
            return hit, modules
    return None


def _arm_selecting(pattern: str):
    """The modules of the arm whose PATTERN LIST contains `pattern`, or None.

    W3 slice 2 deleted the `case` this used to parse out of the shell. Two
    cry-wolf tests stood here because a text scan over `case` arms cannot tell
    prose from code: `# RUN_SECUREFS=1  (temporarily off)` counted as a
    selection, and an arm written `a|b)` rather than `a)` read as an
    unparseable script. `_ARMS` is DATA — a tuple of pattern tuples and
    frozensets — so the PROSE half of that cannot happen any more, and the
    test that pinned it went with the parser it validated.

    THE OTHER HALF DID NOT GO AWAY, and this docstring said it had. Reading a
    multi-pattern arm is still a question that can be asked wrongly: matching
    the arm's FIRST pattern, or the whole tuple, reproduces the `a|b)` failure
    exactly, over data instead of over text. Both mutations were run and both
    are caught — by a test that had to be repaired first, because it asked
    about a path sitting in a ONE-pattern arm and passed either way.

    Membership in the pattern LIST, not equality, is still the question: the
    dominant arm shape groups several patterns that select the same modules,
    and an arm gaining one more alternative must not read as a missing arm.

    Returns None rather than asserting, so a caller can tell "this path has no
    arm at all" — a finding about coverage — from "the arm is there and
    selects too little", which is a different finding.
    """
    for patterns, modules in _selection_port()._ARMS:
        if pattern in patterns:
            return _module_paths(modules)
    return None


def _imports(path, helper: str) -> bool:
    """Does this module import `tests/<helper>.py`, in ANY spelling it may use?

    Parsed rather than grepped: the name of a shared helper turns up in tables,
    comments and assertion messages across this suite, and none of those is a
    dependency.

    Every spelling, because `tests/` is on `sys.path` for part of this suite
    and the bare form is LIVE — `tests/test_projection.py` and
    `tests/test_answers.py` both write `from skill_text import ...`. A rule
    that only knew `from tests.skill_text import ...` would answer "does not
    read it" for a module that does, which is a silent NARROWING of exactly
    the kind being asserted against. Verified by rewriting a watched module to
    each form in turn.
    """
    stem = helper[len("tests/"):-len(".py")]
    dotted = f"tests.{stem}"
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            # `from .engine_tree import x` and `from . import engine_tree`
            # both carry level >= 1 and a module of None or the stem.
            if node.module in (dotted, stem):
                return True
            if node.module in (None, "tests") and any(
                    a.name == stem for a in node.names):
                return True
        if isinstance(node, ast.Import) and any(
                a.name in (dotted, stem) for a in node.names):
            return True
    return False


def _selected_modules(result) -> list:
    """The module list the hook handed pytest, read back from an echoing shim.

    Substring-matching the reported tail instead is wrong in one direction that
    matters: the `--deselect` arguments name `tests/test_portability.py`
    unconditionally, so `"tests/test_portability.py" in stderr` is satisfied by
    a hook that selected something else entirely.
    """
    m = re.search(r"-m pytest (.+?) --deselect", result.stderr)
    return m.group(1).split() if m else []


def _selects(module, selected) -> bool:
    """Is `module` covered by `selected` — as the whole file, or as node ids in
    it?

    The hook selects tests/test_claude_hooks.py two ways. A path that module
    ASSERTS about gets the module; a path it merely READS or NAMES — the other
    five test modules, whose source it parses and whose deselected ids it
    resolves — gets the three assertions that do the reading, because the module
    dominates every total in MEASURED COSTS and a save of a test file was paying
    all of it for three checks. A node id under a module is that module's
    assertions running, so the lower-bound tests are asked this rather than an
    exact string match.
    """
    return any(word == module or word.startswith(module + "::")
               for word in selected)


# The membership `HOOKS_DEPENDENTS` is pinned to, one entry per member with the
# reason it is there. A SET, and both directions of it: the pin that stood here
# was a lower bound over two hardcoded names, and a lower bound cannot see an
# ADDITION. So the list called itself "the whole of what an edit to one of those
# modules can turn red" while omitting the deselect pin — the one assertion in
# this module that names a test module by path — and nothing could say so.
HOOKS_DEPENDENTS = {
    # Recovers every `REPO / "literal"` and constant-tuple path out of all six
    # modules and drives the real hook once per path. An edit to any of the five
    # can add such a literal, which is the CI-only failure the arms exist for.
    "TestSkillInvariantsHook::test_the_watched_set_still_covers_what_the_modules_read",
    # The same claim from the other side: every watched path reaches the module
    # that reads it.
    "TestSkillInvariantsHook::test_every_watched_source_path_reaches_its_module",
    # ast-parses tests/test_portability.py to prove both `--deselect` ids still
    # resolve. pytest ignores a `--deselect` that matches nothing, so renaming
    # either of the two deselected tests silently puts the registry pair back
    # inside the gate for every skill edit.
    "TestHookRegistration::test_only_the_registry_pair_is_deselected_and_both_ids_resolve",
    # The third direction: a watched module that grows an import of a shared
    # test helper must be selected by that helper's arm. The invalidating edit
    # lands in a test module, not in the helper, so this row is what gives the
    # assertion a local signal at all.
    "TestSkillInvariantsHook::test_every_watched_module_that_reads_a_shared_helper_is_in_its_arm",
}

# NOT in it, and the absence is a decision rather than an oversight — see the
# HOOKS_DEPENDENTS comment in the hook, which states it at length.
# `test_the_measured_costs_counts_match_live_collection` goes red the instant a
# test is ADDED to one of the five and stays red until a six-place hand refresh
# of the MEASURED COSTS block, which can only be made coherently once a round's
# tests are final. Selected on those arms it blocked every tool call in between,
# on correct in-progress work — the gates-that-cry-wolf pattern reached through
# a check that is factually right. CI, the review, and every `RUN_HOOKS` edit
# still run it.
HOOKS_DEPENDENTS_EXCLUDED = \
    "TestHookRegistration::test_the_measured_costs_counts_match_live_collection"


def _unresolvable(listed, collected):
    """The node ids in `listed` that `collected` does not hold, EXACTLY.

    A SET difference, and it was a substring search — `node not in collected`
    against the joined `--collect-only` output. An id that is a strict PREFIX of
    a real one satisfies that and collects nothing, so renaming a dependent by
    APPENDING to its name (`..._reaches_its_module` ->
    `..._reaches_its_module_or_nothing`) left the pin green while the hook
    handed pytest an id naming no test. pytest answers a selection like that
    with exit 4 for the WHOLE focused pass — a usage error, not a verdict — and
    the hook holds its stamp on 4 and exits 2, so every later tool call
    re-blocks on a wedge nothing in the suite could see.

    A PARAMETRIZED id is resolved the way pytest resolves it: `a::b::c` selects
    every `a::b::c[case]`, so the bracket-stripped form of each collected id is
    a member too. Without that the exactness above would report a dependent that
    grew a `parametrize` as naming no test at all.
    """
    have = set()
    for node in collected:
        have.add(node)
        have.add(node.split("[", 1)[0])
    return sorted(set(listed) - have)


def _fake_venv(root: Path, exit_code: int, lines=()):
    """A stand-in for `.venv/bin/python` with a chosen exit status.

    Shimming the interpreter rather than running the real suite is what makes
    the failure path deterministic AND fast: the check under test is how the
    hook reacts to a non-zero status, not whether the suite is currently green.

    `lines=["$*"]` turns it into an argv echo, which is how `_selected_modules`
    reads back WHICH modules a given edit chose — the shim is inside a `sh`
    script, so the line is expanded rather than printed literally.

    SELECTION IS FORWARDED TO A REAL INTERPRETER, and the split is what keeps
    the shim honest now that the hook runs `.venv/bin/python` for two unlike
    reasons. Shimming pytest is the whole point — it makes the failure path
    deterministic and fast — but shimming the SELECTOR would mean every
    workspace here answered every path with the fail-closed "cannot answer",
    and a hook that selects everything satisfies nothing this file asserts.
    THE DISPATCH IS A PINNED ARGV SHAPE, and saying otherwise was wrong. An
    earlier version of this docstring claimed it dispatched "on the script
    being run rather than on a flag" so that no argv shape was pinned — but
    matching the selector at all IS matching argv. So it matches ANY argument
    rather than the first: `python -X faulthandler "$SELECTOR"` would
    otherwise reach the pytest branch, `out` would come back `288 passed`, the
    charset guard would refuse it, every module would run, and the
    silence-asserting tests would stay green over a hook whose selection never
    ran at all.

    One thing it cannot exercise: the shim execs THIS interpreter, so "the
    selector runs under the workspace venv" is asserted nowhere here.
    """
    bindir = root / ".venv" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    python = bindir / "python"
    body = ["#!/bin/sh",
            "for a in \"$@\"; do",
            "  case \"$a\" in",
            f"    *hook_selection.py) exec {sys.executable} \"$@\" ;;",
            "  esac",
            "done"]
    body += [f'echo "{line}"' for line in lines] + [f"exit {exit_code}"]
    python.write_text("\n".join(body) + "\n")
    python.chmod(0o755)
    return python


def _stamp_body(project_dir: Path) -> list:
    """The skill stamp's lines: the format version, then the red carry-forward.

    Read as text rather than inferred from a later selection, because the stamp
    holds two things that fail in opposite directions. Its MTIME is the change
    boundary; its BODY is which checks the pass that wrote it left red. A test
    that only watched selection would call a stamp recording nothing correct
    for as long as the next edit happened to select the red module anyway.
    """
    return (project_dir / SKILL_STAMP).read_text().splitlines()


def _clear_red_carry_forward(project_dir: Path):
    """Blank the stamp's red set while keeping its mtime to the microsecond.

    Every assertion about an EXACT module list needs this, because the hook
    deliberately unions the previous pass's red checks into the next selection.
    With a shim that always exits 1 — which is what makes the argv readable —
    the run before this one recorded its own modules as red, so the next one
    legitimately selects more than its payload names.

    `mv` of a candidate carrying the old timestamp is the only shape that
    works, and it is the shape the hook itself uses. Writing the file in place
    sets its mtime to now, which the next call reads as a stamp from the FUTURE
    — the clock-skew branch — and answers by forcing all four modules: the very
    symptom this helper exists to remove, arriving from the other side.
    """
    stamp = project_dir / SKILL_STAMP
    before = stamp.stat()
    candidate = stamp.with_name(stamp.name + ".cleared")
    candidate.write_text(f"{SKILL_STAMP_VERSION}\n")
    os.utime(candidate, (before.st_atime, before.st_mtime))
    candidate.replace(stamp)


@pytest.fixture(scope="module")
def quiescent_workspace(tmp_path_factory):
    """A workspace whose stamp already covers everything under it.

    Three conditions have to hold before the PAYLOAD's own module selection can
    be observed at all, and each is a path the hook takes deliberately:

      - `.claude/` exists, or no stamp can be written and every pass is a full
        pass;
      - at least one watched root exists, or the scan has nothing to compare and
        reports "unknown", which also forces a full pass;
      - the watched roots are older than the stamp's one-second overlap, which
        only sleeping can arrange (see STAMP_OVERLAP_SECONDS).

    Miss any of them and a Write payload selects its two modules while the scan
    adds the other two, so every assertion about selection passes for the wrong
    reason — verified, and the reason this is a fixture rather than four copies.

    Module-scoped because that sleep is real time, and the sharing is safe in
    the direction that mattered when this was written: the tests below send
    payloads and never write into the tree, and the stamp the hook advances on
    each call is not itself a watched path.

    It is NOT safe in the other direction, which is the correction. That stamp
    also carries the red carry-forward, and every caller here drives a shim
    that exits 1, so each call records its own modules as red for the next one.
    Any assertion about an exact module list must call
    `_clear_red_carry_forward` first — without it those assertions depend on
    execution order, which pytest-randomly reshuffles on every run.
    """
    workspace = tmp_path_factory.mktemp("quiescent")
    (workspace / ".claude").mkdir()
    (workspace / "AGENTS.md").write_text("# a watched root\n")
    _fake_venv(workspace, exit_code=0, lines=["288 passed"])
    time.sleep(STAMP_OVERLAP_SECONDS)

    bootstrap = _run_skill_hook_bash(workspace)
    assert bootstrap.returncode == 0, bootstrap.stderr
    assert (workspace / SKILL_STAMP).is_file()

    _fake_venv(workspace, exit_code=1, lines=["$*"])
    settled = _run_skill_hook_bash(workspace)
    assert settled.returncode == 0 and _selected_modules(settled) == [], (
        "the workspace is not quiescent, so nothing below measures selection")
    return workspace


@pytest.fixture(scope="module")
def packaged_workspace(tmp_path_factory):
    """A quiescent workspace that actually HOLDS packages.

    `quiescent_workspace` is deliberately almost empty, and a package-directory
    payload is the one case whose answer depends on the TREE: the hook rewrites
    `agents/lib/<pkg>` to its `__init__.py` only when the directory is really
    there. Asserting that against an empty workspace would measure the absence
    of the directory and report it as the rule working.

    The packages are created BEFORE the stamp settles, for the reason the other
    fixture's docstring gives: writing into a watched root afterwards makes the
    tree non-quiescent, the scan unions in its own modules, and every selection
    assertion then passes for the wrong reason.
    """
    workspace = tmp_path_factory.mktemp("packaged")
    (workspace / ".claude").mkdir()
    (workspace / "AGENTS.md").write_text("# a watched root\n")
    lib = workspace / "agents" / "lib"
    for package in ("projection", "tax_computations", "tax_computations/state",
                    "a_package_no_arm_names"):
        (lib / package).mkdir(parents=True)
        (lib / package / "__init__.py").write_text("x = 1\n")
    # A package that has LOST its initialiser, and the bytecode directories
    # every engine run rewrites.
    (lib / "lost_its_initialiser").mkdir()
    (lib / "__pycache__").mkdir()
    (lib / "projection" / "__pycache__").mkdir()
    (lib / "tie_out.py").write_text("x = 1\n")
    (lib / "__init__.py").write_text("x = 1\n")
    # A package REPLACED BY A FILE, and a module replaced by a DIRECTORY. Both
    # are what a broken engine looks like mid-edit, and both were found by
    # review rather than imagined: the first selected nothing at all once the
    # hand-written arms came out, and the second normalised into the generic
    # arm and dropped the two modules only its own arm names.
    (lib / "capabilities.py").mkdir()
    (lib / "was_a_package").write_text("x = 1\n")

    _fake_venv(workspace, exit_code=0, lines=["288 passed"])
    time.sleep(STAMP_OVERLAP_SECONDS)
    bootstrap = _run_skill_hook_bash(workspace)
    assert bootstrap.returncode == 0, bootstrap.stderr

    _fake_venv(workspace, exit_code=1, lines=["$*"])
    settled = _run_skill_hook_bash(workspace)
    assert settled.returncode == 0 and _selected_modules(settled) == [], (
        "the workspace is not quiescent, so nothing below measures selection")
    return workspace


@needs_jq
class TestAPackageDirectoryReadsAsItsInitialiser:
    """`find` prints the DIRECTORY when a file is added to or deleted from a
    package, and that string matched no arm.

    The generic engine arm reaches a module at any depth — a `case` glob's `*` spans
    `/` — but never the directory itself, so every package carried a
    hand-written arm repeating the generic selections, W2 adding one per slice.
    The arm that gets forgotten selects NOTHING and looks exactly like a clean
    path, which is this hook's whole failure mode.

    The payload is normalised to `<dir>/__init__.py` instead, so a package
    reaches the arms that already exist and no package needs an entry anywhere
    again.
    """

    def _selects_for(self, workspace, relative):
        _clear_red_carry_forward(workspace)
        return set(_selected_modules(
            _run_skill_hook(workspace, str(workspace / relative))))

    def test_a_package_selects_exactly_what_its_initialiser_does(
            self, packaged_workspace):
        """DERIVED, not a listed set: the claim is that the two spellings are
        the same edit, and a hardcoded expectation would pass just as happily
        over two arms that had drifted apart."""
        for package in ("projection", "tax_computations",
                        "tax_computations/state"):
            directory = self._selects_for(
                packaged_workspace, f"agents/lib/{package}")
            initialiser = self._selects_for(
                packaged_workspace, f"agents/lib/{package}/__init__.py")
            assert directory == initialiser and directory, (
                f"agents/lib/{package} selects {sorted(directory)} and its "
                f"initialiser {sorted(initialiser)} — a file added to or "
                "deleted from the package reaches a different set of checks "
                "than editing it does, and the add is the case with no other "
                "signal")

    def test_the_agents_module_still_rides_the_tax_computations_arm(
            self, packaged_workspace):
        """The normalisation must not flatten a package that needs MORE than
        the generic arm. `test_claude_agents` asserts what the shipped
        `compute_qbi_deduction` returns, and nothing else selects it."""
        for package in ("tax_computations", "tax_computations/state"):
            assert _selects(AGENTS, self._selects_for(
                packaged_workspace, f"agents/lib/{package}")), package

    def test_a_NEW_package_needs_no_arm(self, packaged_workspace):
        """The point of the change. This directory is named in no arm, in no
        test but this one, and in nothing the hook reads."""
        selected = self._selects_for(
            packaged_workspace, "agents/lib/a_package_no_arm_names")
        for module in (PREFLIGHT, HOSTGUARD, SECUREFS, PORTABILITY):
            assert _selects(module, selected), (
                f"a package no arm names misses {module} — the hand list was "
                "retired on the promise that this holds without one")

    def test_a_package_that_lost_its_initialiser_still_selects(
            self, packaged_workspace):
        """The rewrite is NOT conditional on the initialiser existing, and this
        is why. A package whose `__init__.py` has just been deleted is exactly
        the edit preflight and the host guard exist to report; a rule that
        declined there would go quiet at the one moment it matters."""
        assert self._selects_for(
            packaged_workspace, "agents/lib/lost_its_initialiser"), (
            "a package missing its initialiser selected nothing — the engine "
            "cannot import and the hook said so to no one")

    @pytest.mark.parametrize("relative", ["agents/lib/__pycache__",
                                          "agents/lib/projection/__pycache__"])
    def test_bytecode_directories_select_nothing(self, packaged_workspace,
                                                 relative):
        """The cost half, and the defect that killed the first attempt at this.

        Widening the arm to a bare `agents/lib/*` was the other candidate: it
        swallows `__pycache__`, which EVERY engine run rewrites, so the next
        tool call would run a pytest module on the strength of compiled
        bytecode nothing reads. `__pycache__` is named explicitly now, and this
        is what says so — at both depths, since a nested one is what an engine
        run inside a package leaves.
        """
        assert self._selects_for(packaged_workspace, relative) == set(), (
            f"{relative} selected a module. Every engine run rewrites it, so "
            "this puts a pytest module behind the next tool call for as long "
            "as nobody notices")

    def test_a_path_under_the_engine_that_is_not_a_py_FILE_still_selects(
            self, packaged_workspace):
        """Found by review, and it is why the generic arm says `agents/lib/*`.

        Scoping that arm to `.py` was never about `.py`: it was dodging
        `__pycache__`, which the bytecode return now settles once. The cost was
        paid in the direction nobody watches — a package replaced by a file,
        or a directory whose initialiser was just deleted, matched NOTHING and
        the hook exited 0 over an engine that cannot import.
        """
        for relative in ("agents/lib/was_a_package",
                         "agents/lib/lost_its_initialiser"):
            selected = self._selects_for(packaged_workspace, relative)
            assert _selects(PREFLIGHT, selected), (
                f"{relative} does not reach preflight. The engine cannot "
                "import in this state and the gate that exists to say so was "
                "not run.")

    def test_a_module_replaced_by_a_DIRECTORY_keeps_its_own_arm(
            self, packaged_workspace):
        """The other half of the same finding, in the opposite direction.

        `capabilities.py` as a directory is still `capabilities.py` to every
        arm that names it. Normalising it to `capabilities.py/__init__.py`
        would drop it into the generic arm and lose the two modules only its
        own arm selects — a NARROWING dressed as a generalisation, which is
        the shape this whole mechanism keeps producing.
        """
        selected = self._selects_for(packaged_workspace,
                                     "agents/lib/capabilities.py")
        for module in (CAPABILITIES, AGENTS):
            assert _selects(module, selected), (
                f"agents/lib/capabilities.py lost {module} once it became a "
                "directory — the normalisation reached a path that already "
                "had a better arm")

    def test_the_engine_ROOT_keeps_its_own_meaning(self, packaged_workspace):
        """`agents/lib` holds an `__init__.py` too, and folding it in would
        have been the quiet loss: its arm means "a module was added to or
        removed from the engine" and names two modules the generic arm does
        not."""
        root = self._selects_for(packaged_workspace, "agents/lib")
        for module in (CAPABILITIES, AGENTS):
            assert _selects(module, root), (
                f"agents/lib no longer selects {module} — the root was "
                "normalised into its own initialiser and lost the two modules "
                "only it names")


@needs_jq
class TestTheSelectionPortIsTheHooksOwnDecision:
    """W3 slice 2: the port is wired in, and the bash `case` is gone.

    Slice 1 landed `.claude/hooks/lib/hook_selection.py` beside a 218-line
    `case` that was still what ran, held to it by a differential over every
    tracked path in the repository. That differential was a MIGRATION PROOF
    and this commit spends it: with one implementation left there is no second
    answer to compare against, and keeping it would have meant pinning the
    port to a shell revision that can never grow the next arm.

    What replaces it is not weaker in the direction that matters. The
    differential compared two functions; these tests run the REAL HOOK and
    read the pytest argv it produces, so they cover the wiring the
    differential could not see — the process substitution that would have lost
    every flag to a subshell, the union across a scan of many paths, and the
    direction a selector that cannot answer fails in.

    WHAT REPLACES IT, precisely, because two drafts of this paragraph claimed
    more than the tests below do. The differential compared two
    implementations over every path. Nothing here does that, and nothing can.
    What is here instead:

    - `_ARM_SNAPSHOT` pins the arms as DATA, so deleting or narrowing one
      still takes a deliberate second edit. That is the property the
      differential was really earning its keep on, and it is the only one of
      these that replaces it rather than adding something new.
    - Four tests drive the REAL HOOK and read the pytest argv it produces —
      the three fail-closed directions, and the argv rule. Those cover wiring
      the differential could not see.
    - One drives the CLI alone, for the union, because there is no per-path
      loop left in the shell for that defect to live in.

    The SUBSHELL defect is not in this list, and an earlier draft put it
    there. It is covered — rewriting either call site as a pipe turns thirty
    tests in this module red, measured — by the tests that already drive the
    hook, not by anything written for the flip.

    WHAT IS DERIVED, and what is not. The flag roster comes from the script's
    own declarations and the corpus from `git ls-files` plus every ancestor
    directory. The per-pattern coverage obligation is NOT derived from
    anything outside the table any more; that is what `_ARM_SNAPSHOT` is for,
    and `test_every_arm_pattern_is_reached_by_the_corpus` says so at length.
    """

    def _flags(self):
        """The RUN_ flags, from their DECLARATIONS at the top of the script.

        The declarations are where a new module has to appear for `set -u` to
        survive one, which makes them the definition. They are also what
        `select_modules_from` validates the port's output against, so a flag
        the port names and this script does not declare is refused there
        rather than silently `eval`ed into existence.
        """
        flags = re.findall(r"^(RUN_[A-Z0-9_]+)=0$",
                           SKILL_HOOK.read_text(encoding="utf-8"), re.M)
        assert len(flags) > 5, flags
        assert len(set(flags)) == len(flags), f"duplicate flags: {flags}"
        return flags

    def _tracked(self):
        """NUL-delimited, because `.split()` splits on WHITESPACE.

        No tracked path carries a space today, so the corpus looks complete —
        and the day one does, `docs/contributing/a b.md` enters as two
        fragments that match no arm, both answers are nothing, and a guard
        comparing them accepts it. The real path goes unchecked while the
        count still looks right.
        """
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
            text=True, check=True).stdout
        return [p for p in out.split("\0") if p]

    # Patterns no corpus path reaches, each with the reason. Empty today, and
    # the test asserts that BOTH ways so an exemption cannot outlive its cause.
    UNREACHABLE: dict = {}

    def _corpus(self):
        tracked = self._tracked()
        # EVERY ancestor, not the immediate parent. `Path(p).parent` left
        # `.agents/skills` out of the corpus — a WATCHED ROOT with its own arm
        # selecting five modules — because nothing is tracked directly inside
        # it. Deleting that arm from the port passed every test here.
        dirs = sorted({str(a) for p in tracked for a in Path(p).parents
                       if str(a) != "."})
        adversarial = [
            # The prologue's three, UNDER A MATCHING PREFIX. Written as
            # `a/../b` they select nothing whether or not the guard exists, so
            # all three passed with it deleted: an adversarial case that cannot
            # distinguish the code from its absence is decoration.
            ".agents/skills/../x", ".agents/skills/./x", ".agents/skills//x",
            "a/../b", "a/./b", "a//b",
            "agents/lib/__pycache__/x.pyc",
            ".claude/hooks/lib/__pycache__/hook_selection.pyc",
            "agents/lib", "agents/lib/state", "agents/lib/tax_computations",
            "tests", "docs/ops",                  # ids with TWO arms each
            # Patterns no TRACKED path reaches. The hook is fed `find` output,
            # which includes untracked and gitignored paths, so "nothing
            # tracked matches it" is not "nothing reaches it".
            "scripts/new.py", "agents/top.py", "agents/top.sh",
            "docs/ops/brand-new.md", "tests/sub/x.py", "agents/lib/has.dot",
            "requirements.in", ".github/ISSUE_TEMPLATE",
            "no/such/path.py", "",
        ]
        return tracked + dirs + adversarial

    def test_the_module_roster_is_the_scripts_own(self):
        """A ninth module must not be invisible.

        `MODULES` used to be a hand-written copy in the port and again in a
        shell probe. Adding `RUN_NEWMODULE=1` to an arm passed every test in
        this class: the new flag was set by the shell, read by nothing, and
        the contract silently stopped covering it.

        BOTH DIRECTIONS, and since the flip they are the same assertion: the
        script declares the flags, the port names the modules, and
        `select_modules_from` refuses any name that is in one and not the
        other. A module the port knows and the script does not declare now
        makes the hook select EVERYTHING rather than go quiet — checked
        directly by `test_a_selector_naming_an_undeclared_flag_runs_everything`
        — but it is still a defect, and this is where it is named.
        """
        assert [f"RUN_{m}" for m in _selection_port().MODULES] == self._flags()

    def test_every_arm_pattern_is_reached_by_the_corpus(self):
        """Per-PATTERN coverage — not a count of paths.

        The vacuity guards that stood here counted the corpus and the paths
        selecting anything, and both were satisfied by the tracked list alone:
        deleting every ancestor directory AND the whole adversarial list left
        them green. What they could not see is a PATTERN nothing exercises,
        which is the thing that actually goes unverified — `.agents/skills`
        was one, and deleting it from the port passed everything.

        WHAT THE FLIP COST, stated rather than left to be discovered. The
        patterns used to be read out of the SHELL, so deleting one from the
        port left its obligation standing and this test went red. There is no
        second copy to read now, and an arm deleted from `_ARMS` takes its own
        obligation with it. This test keeps the narrower job it can still do
        honestly: no arm in the table is unreachable prose.

        WHAT STILL HOLDS THE ARMS TO SOMETHING OUTSIDE THEMSELVES is
        `test_the_watched_set_still_covers_what_the_modules_read`, which
        RECOVERS the path literals each of the seven modules reads and
        requires an arm to select it. Naming the right one matters here: an
        earlier draft of this paragraph credited
        `test_every_watched_source_path_reaches_its_module`, whose table is a
        hand-written literal — and that module's own docstring says of hand
        lists that one "only compensates for what somebody remembers to add to
        it, and nobody did". Selling a hand table as the derived anchor is the
        defect this file exists to catch, one level up.

        AND IT IS PARTIAL, because the derived anchor discovers a dependency
        by finding a PATH LITERAL and several real ones are globs. Two
        examples, both checked rather than assumed: `test_secure_fs.py`
        rglobs every Python file under the agents directory and
        `test_portability.py` globs the ops docs for the AGENTS.md §9 index,
        so `agents/*.py` and
        `docs/ops/*.md` are arms protecting real reads that the locator cannot
        see. A draft of this paragraph called those arms "read by no module"
        and their loss "coverage rather than correctness" — both wrong, and
        wrong in the flattering direction.

        `_ARM_SNAPSHOT` is what covers them, and covers every other arm the
        locator misses: the arms pinned as data, so deleting or narrowing one
        takes a deliberate second edit. That is the property the differential
        was really earning its keep on, and it survives the bash it was
        written against.

        Anything genuinely unreachable is named in `UNREACHABLE` with its
        reason, so the exemption is a statement rather than a silence.
        """
        py = _selection_port()
        corpus = [p for p in self._corpus() if p]
        patterns = [q for qs, _ in py._ARMS for q in qs]
        assert len(patterns) > 40, patterns

        def decides(pattern):
            """Is this pattern the FIRST match for some corpus path?

            Merely matching is not enough: `agents/*.py` matches plenty of
            engine files that an earlier arm claims, so a pattern shadowed
            everywhere counted as covered while nothing exercised it.
            """
            for path in corpus:
                for qs, _ in py._ARMS:
                    hit = next((q for q in qs
                                if fnmatch.fnmatchcase(path, q)), None)
                    if hit is not None:
                        if hit == pattern:
                            return True
                        break
            return False

        unreached = [q for q in patterns if not decides(q)]
        assert set(unreached) <= set(self.UNREACHABLE), (
            f"{sorted(set(unreached) - set(self.UNREACHABLE))} are matched by "
            "no path in the corpus, so nothing verifies them — add a path "
            "that reaches each, or name it in UNREACHABLE with the reason it "
            "cannot be reached")
        assert set(unreached) == set(self.UNREACHABLE), (
            f"{sorted(set(self.UNREACHABLE) - set(unreached))} are exempted as "
            "unreachable but the corpus now reaches them — drop the exemption")

    def test_the_arm_table_is_the_one_that_was_pinned(self):
        """The arms, against their pin — in both directions and in ORDER.

        `_ARM_SNAPSHOT` exists because the differential could not survive the
        flip: with the bash gone there is no second answer to compare against,
        and an arm deleted from `_ARMS` deletes its own obligation. This
        restores the one property that mattered — changing an arm takes a
        deliberate second edit — for the price of a table rather than a second
        implementation.

        Compared as a SEQUENCE, so a reordering is a finding. First match
        wins, `docs/ops` and `tests` each have two arms, and a set comparison
        would call swapping them no change at all.
        """
        live = tuple((tuple(patterns), tuple(sorted(modules)))
                     for patterns, modules in _selection_port()._ARMS)
        assert live == _ARM_SNAPSHOT, (
            "the selection arms no longer match their pin. If you MEANT to "
            "change them, update `_ARM_SNAPSHOT` in the same commit and say "
            "why in the message — that second edit is the whole point of it. "
            "If you did not, an arm has been deleted or narrowed, and the "
            "paths it covered now select nothing:\n  pinned: "
            f"{[a for a in _ARM_SNAPSHOT if a not in live]}\n  live:   "
            f"{[a for a in live if a not in _ARM_SNAPSHOT]}")

    def test_the_package_rewrite_changes_no_arm(self):
        """Why no differential could have caught dropping the rewrite.

        The prologue rewrites a package DIRECTORY under `agents/lib/` to its
        own `__init__.py` before matching. Deleting that leaves every
        path-by-path comparison green, because no path reaches a different ARM
        either way — the rewrite changes the string and not the answer.

        Asserted over the ARM DATA, never by calling the port. The first
        version called `select_modules_for` twice with `isdir` flipped, which
        compares the port against ITSELF: delete the rewrite and the sentinel
        stops being a sentinel, silently, exactly when it is needed. Reading
        the patterns keeps the claim true whether or not the rewrite exists.
        """
        py = _selection_port()
        # STRICTLY under `agents/lib/`. The rewrite cannot fire for
        # `agents/lib` itself — the prologue's `/agents/lib/*/` needs a
        # component after it — and that directory legitimately has its own arm
        # that its `__init__.py` does not share, so including it asserted a
        # difference the rewrite never causes.
        packages = sorted({str(Path(p).parent) for p in self._tracked()
                           if p.startswith("agents/lib/")}
                          | {"agents/lib/state", "agents/lib/nosuchpackage"})
        packages = [p for p in packages if p != "agents/lib"]
        assert len(packages) > 3, packages

        def decided_modules(path):
            """The FIRST arm that matches, which is the only one that decides.

            Comparing every arm was too strong: `agents/*.py` matches
            `agents/lib/state/__init__.py` and not `agents/lib/state`, because
            `*` crosses `/` — but an earlier arm claims both, so that
            difference never reaches an answer.
            """
            for qs, modules in py._ARMS:
                if any(fnmatch.fnmatchcase(path, q) for q in qs):
                    return modules
            return frozenset()

        for pkg in packages:
            # The MODULE SET, not the arm's index. Two arms can answer
            # identically, and a new exact arm returning the same set is not a
            # change to any answer — comparing indices called that a defect.
            assert decided_modules(pkg) == decided_modules(f"{pkg}/__init__.py"), (
                f"{pkg!r} selects {sorted(decided_modules(pkg))} and "
                f"{pkg}/__init__.py selects "
                f"{sorted(decided_modules(f'{pkg}/__init__.py'))}, so the "
                "package rewrite now changes an answer — a path that reaches "
                "it must be added to the corpus and this test replaced")

    def test_the_union_is_across_every_changed_path(self):
        """The scan hands over MANY paths, and the answer is their union.

        The shell accumulated into globals across a loop; the port is one
        process reading them all, and `union_for` is where that accumulation
        went. A port returning the LAST path's answer passes every per-path
        test in this class and narrows a whole scan to whatever `find` printed
        last — a green hook over an unchecked edit, which is the failure this
        file exists to prevent. There is no per-path loop left in the shell,
        so `union_for` is the only place that defect can now live, and the CLI
        is the right altitude to ask at.

        IT DOES NOT COVER THE SUBSHELL DEFECT, which an earlier draft of this
        docstring claimed it did — it never invokes the hook. That defect is
        covered, and heavily: rewriting either call site as `x |
        select_modules_from` turns THIRTY tests in this module red, measured.
        A test that claims coverage it does not have is worse than one that
        claims none.
        """
        py = _selection_port()
        # DERIVED, and for two reasons. A hand-picked trio goes stale the day
        # an arm's module set changes, and — the one that actually bit — a
        # repository path written as a literal in THIS module reads to
        # `test_the_watched_set_still_covers_what_the_modules_read` as a file
        # the hooks module opens, which then demands that path's arm select
        # the hooks module. It is an input here, not a read, and the locator
        # cannot tell those apart; not writing the literal is the honest fix.
        many: list = []
        for cand in [q for qs, _ in py._ARMS for q in qs
                     if "*" not in q and (REPO / q).is_file()]:
            if all(not (py.select_modules_for(cand) <= py.select_modules_for(q)
                        or py.select_modules_for(q)
                        <= py.select_modules_for(cand)) for q in many):
                many.append(cand)
            if len(many) == 3:
                break
        assert len(many) == 3, (
            f"no three arms with pairwise unlike answers: {many}")

        out = subprocess.run(
            [sys.executable, str(SELECTION_PORT)],
            input="\0".join(many).encode() + b"\0",
            capture_output=True)
        assert out.returncode == 0, out.stderr[-400:]
        lines = out.stdout.decode().split()
        assert lines and lines[0] == py.PROTOCOL, (
            f"the port's first line is {lines[:1]}, not its protocol line")
        got = set(lines[1:])
        expected = set().union(*(py.select_modules_for(p) for p in many))
        assert got == expected, (got, expected)
        # ...and it is a genuine union, not one path's answer. Each of the
        # three contributes a module the other two do not, so any "last one
        # wins" implementation loses at least two.
        for one in many:
            assert py.select_modules_for(one) < expected, (
                f"{one} alone already selects everything the three do "
                "together, so this test cannot tell a union from a last-write")

    def test_the_selector_is_launched_with_no_paths_on_argv(self, tmp_path):
        """A taxpayer's document name must not reach `ps`.

        This hook runs with its CWD inside a directory that may hold
        `documents/`, `summaries/` and a taxpayer profile, and the path it
        classifies can name one of them. argv is world-readable through `ps`
        on this platform, so `python "$SELECTOR" "$relative"` would publish
        that filename to every user on the machine — the same exposure the
        hook's generic `display_target` label exists to prevent, and the same
        one `test_an_unknown_red_token_never_reaches_the_pytest_argv` pins one
        layer up.

        Nothing in the code says so: a reviewer simplifying the redirect into
        an argument would be making the obvious change. This is the assertion
        that objects. It RECORDS the argv rather than reasoning about the
        source, so a second path reaching argv by any route is caught.
        """
        log = tmp_path / "argv.log"
        port = ("import sys, pathlib\n"
                f"pathlib.Path({str(log)!r}).write_text(repr(sys.argv))\n"
                f"print({_selection_port().PROTOCOL!r})\n"
                "sys.stdin.buffer.read()\n")
        secret = tmp_path / "summaries" / "a-1099.md"
        secret.parent.mkdir()
        secret.write_text("x\n")
        _run_skill_hook(tmp_path, str(secret),
                        script=self._orphan_hook(tmp_path, port))

        argv = ast.literal_eval(log.read_text())
        assert len(argv) == 1, (
            f"the selector was launched with {argv[1:]} on its argv — paths "
            "go in on STDIN, because argv is world-readable through `ps` and "
            "the hook classifies paths that can name a taxpayer's documents")
        assert not any(secret.name in str(a) for a in argv), argv

    def test_a_missing_selector_runs_everything(self, tmp_path):
        """FAIL CLOSED. A selector that cannot answer must not answer nothing.

        The hook resolves the port beside its own file, so this drives a COPY
        of the script with no `lib/` next to it. Every other way of not
        knowing — no interpreter, an import error, a raised exception — leaves
        the same non-zero status on the same command substitution, so this is
        the shape of all of them.
        """
        orphan = self._orphan_hook(tmp_path, None)
        assert not (orphan.parent / "lib").exists()
        r = self._drive(orphan, tmp_path)
        assert r.returncode == 2, (
            "a hook that cannot select exited quietly — the one direction a "
            f"missing decision must never take (rc={r.returncode})")
        assert set(_selected_modules(r)) == set(ALL_MODULES), sorted(
            _selected_modules(r))

    def test_a_selector_that_raises_after_answering_runs_everything(
            self, tmp_path):
        """The EXIT STATUS path, which nothing else here reaches.

        `test_a_missing_selector_runs_everything` claims to stand for every
        way of not knowing — no interpreter, an import error, a raised
        exception — because all of them leave the same non-zero status on the
        same command substitution. It does not: removing `|| return 1` from
        that substitution leaves it GREEN, because a missing selector prints
        nothing and the PROTOCOL guard refuses that first. Two guards, one
        test, and the weaker one was answering.

        A selector that prints its protocol line, names a real module, and
        THEN dies is what separates them: the protocol guard is satisfied, the
        output is well formed, and only the status says anything is wrong.

        QUIESCENT FIRST, and that is not setup noise — it is what the first
        draft of this test got wrong. Without a trusted stamp every pass is a
        FULL pass, so a selector that answered wrongly and one that could not
        answer both end in all seven modules, and the assertion below holds
        either way. Settling the stamp is what makes the answer readable: with
        the status guard removed this then runs `tests/test_portability.py`
        alone, which is a different set and a red test.
        """
        port = (f"import sys\nprint({_selection_port().PROTOCOL!r})\n"
                "print('PORTABILITY')\nsys.stdout.flush()\n"
                "raise SystemExit(3)\n")
        script = self._orphan_hook(tmp_path, port)
        edited = tmp_path / "CLAUDE.md"
        edited.write_text("# watched\n")

        # A trusted stamp, written through the REAL hook on a Bash payload —
        # the one path that settles the workspace without going through the
        # selector this test is about.
        _fake_venv(tmp_path, exit_code=0, lines=["1 passed"])
        time.sleep(STAMP_OVERLAP_SECONDS)
        assert _run_skill_hook_bash(tmp_path).returncode == 0
        assert (tmp_path / SKILL_STAMP).is_file(), (
            "no stamp was written, so the pass below is a COLD one and this "
            "test cannot tell a wrong answer from no answer")

        _fake_venv(tmp_path, exit_code=1, lines=["$*"])
        r = _run_skill_hook(tmp_path, str(edited), script=script)
        assert r.returncode == 2, (
            "a selector that answered and then died was read as an answer — "
            f"the status is the only thing that says otherwise (rc={r.returncode})")
        assert set(_selected_modules(r)) == set(ALL_MODULES), (
            "the hook took the dead selector's answer instead of failing "
            f"closed: {sorted(_selected_modules(r))}")


    def _orphan_hook(self, tmp_path, port: str | None):
        """A copy of the script, with `port` as the source of its `lib/` — or
        no `lib/` at all when it is None.

        A COPY, because the hook resolves the selector beside its own file:
        that is what lets these two tests replace the decision without
        touching the one the rest of the suite runs.
        """
        (tmp_path / ".claude").mkdir()
        (tmp_path / "AGENTS.md").write_text("# a watched root\n")
        _fake_venv(tmp_path, exit_code=1, lines=["$*"])
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        (hooks / "skill-invariants.sh").write_text(
            SKILL_HOOK.read_text(encoding="utf-8"), encoding="utf-8")
        if port is not None:
            (hooks / "lib").mkdir()
            (hooks / "lib" / "hook_selection.py").write_text(port)
        return hooks / "skill-invariants.sh"

    def _drive(self, script, tmp_path):
        # `docs/changelog/` has no arm, so a WORKING selector answers nothing
        # here and the hook exits 0 in silence. Every module instead is the
        # only reading that says the failure was noticed.
        return _run_skill_hook(
            tmp_path, str(tmp_path / "docs" / "changelog" / "x.md"),
            script=script)

    def test_the_shell_and_the_port_agree_on_the_protocol_line(self):
        """One protocol, read from BOTH files.

        The port writes it and the script refuses an answer without it, so it
        is inherently stated twice — and a constant in two files is the shape
        this repository has watched drift more than once. Both sides are read
        here rather than compared against a literal written a third time.
        """
        # ANCHORED, and not because anchoring is tidier. `re.search` for the
        # guard's text found it inside a comment: commenting the guard OUT
        # left this green, which is this repository's recorded "gate that
        # reads its own comments" in its smallest form. A line whose first
        # non-blank character is `#` is prose, whatever it says.
        shell = re.search(r'^[ \t]*\[ "\$module" = (\w+) \] \|\| return 1',
                          SKILL_HOOK.read_text(encoding="utf-8"), re.M)
        assert shell, (
            "`select_modules_from` no longer checks a protocol line — without "
            "it an EMPTY selector file exits 0 saying nothing and the hook "
            "reads that as `no module can break this path`. A commented-out "
            "guard reaches this message too, which is the point of the anchor")
        assert shell.group(1) == _selection_port().PROTOCOL, (
            f"the script accepts {shell.group(1)!r} and the port writes "
            f"{_selection_port().PROTOCOL!r} — one of them has moved")

    def test_a_selector_that_says_nothing_at_all_runs_everything(self, tmp_path):
        """An EMPTY selector is valid Python: it runs clean and prints nothing.

        Exit status alone cannot tell that apart from a path no module can
        break, which is why the protocol line exists. Reachable rather than
        exotic: this hook fires on every Write, the selector included, so a
        truncated save while someone edits it lands here.
        """
        r = self._drive(self._orphan_hook(tmp_path, ""), tmp_path)
        assert r.returncode == 2, (
            "an empty selector left the hook silent — the one direction a "
            f"selector that cannot answer must never take (rc={r.returncode})")
        assert set(_selected_modules(r)) == set(ALL_MODULES), sorted(
            _selected_modules(r))

    def test_an_inherited_run_flag_is_not_a_declaration(self, tmp_path):
        """A GATE INHERITS ITS ENVIRONMENT — the Y6 lesson, one layer over.

        `select_modules_from` accepts a module name by asking whether this
        script declares a flag for it, and `${!name+set}` answers "is that
        variable set". An EXPORTED `RUN_NEWMODULE=0` answers yes: the port
        names `NEWMODULE`, the check passes, the assignment lands on a
        variable nothing reads, and the function returns 0 having set no real
        flag — `nothing_selected` exits 0 and the pass goes quiet over an edit
        that should have run five modules.

        The script unsets every inherited `RUN_*` before its own declarations,
        which is what makes "is it set" mean "did this script declare it"
        without a second copy of the roster to keep in step.
        """
        port = (f"import sys\nprint({_selection_port().PROTOCOL!r})\n"
                "print('NEWMODULE')\nsys.stdin.buffer.read()\n")
        script = self._orphan_hook(tmp_path, port)
        r = _run_skill_hook(
            tmp_path, str(tmp_path / "docs" / "changelog" / "x.md"),
            script=script, env_extra={"RUN_NEWMODULE": "0"})
        assert r.returncode == 2, (
            "an inherited variable made an undeclared module name look "
            f"declared, and the pass went quiet (rc={r.returncode})")
        assert set(_selected_modules(r)) == set(ALL_MODULES), sorted(
            _selected_modules(r))

    def test_an_inherited_cdpath_does_not_break_the_selector(self, tmp_path):
        """`cd` PRINTS where it landed when CDPATH found it for it.

        So `$(cd -- "$(dirname -- "$0")" && pwd)` comes back as TWO lines and
        `$SELECTOR` names nothing — reproduced, and the hook then ran every
        module on every watched edit. Only reachable through a RELATIVE
        invocation, because bash consults CDPATH for nothing else, which is
        why this test invokes the script by a relative path where every other
        test passes an absolute one.

        IT PINS THE OUTCOME, NOT EITHER GUARD. Two things produce it — the
        `>/dev/null` on that `cd`, and `CDPATH=''` above — so removing one
        leaves this green and only removing both turns it red. Checked by
        mutation, both ways. The test above is what pins the clearing on its
        own; nothing pins the redirection alone, and nothing needs to, because
        the property is that the answer does not change.
        """
        # QUIESCENT FIRST, and that is not setup noise. Without a trusted
        # stamp every pass is a full pass, so a broken selector and a working
        # one both end in all seven modules and the test cannot tell them
        # apart. Settling the stamp is what makes the answer readable.
        (tmp_path / ".claude").mkdir()
        edited = tmp_path / "CLAUDE.md"
        edited.write_text("# watched\n")
        _fake_venv(tmp_path, exit_code=0, lines=["1 passed"])
        time.sleep(STAMP_OVERLAP_SECONDS)
        assert _run_skill_hook_bash(tmp_path).returncode == 0
        assert (tmp_path / SKILL_STAMP).is_file()

        _fake_venv(tmp_path, exit_code=1, lines=["$*"])
        r = subprocess.run(
            ["bash", str(SKILL_HOOK.relative_to(REPO))],
            input=json.dumps({"tool_input": {"file_path": str(edited)}}),
            text=True, capture_output=True, cwd=REPO,
            env=dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path), CDPATH="."))
        # CLAUDE.md's arm, exactly — not every module, which is what a
        # selector the script could not find would have produced.
        assert set(_selected_modules(r)) == _module_paths(
            _selection_port().select_modules_for("CLAUDE.md")), (
            "an inherited CDPATH changed which modules ran: "
            f"{sorted(_selected_modules(r))}")

    def test_a_selector_naming_an_undeclared_flag_runs_everything(self, tmp_path):
        """The other half: the port and the script disagree about the roster.

        `select_modules_from` checks each name against the script's own
        declarations instead of against a list of its own, so this is the
        seventh copy of the roster that does not exist. A name nothing
        declares means the two files have drifted, and the hook treats that
        as not knowing rather than as a flag to invent — `eval "RUN_$m=1"` on
        an undeclared name would otherwise create it, and nothing downstream
        reads it, so the pass would go quiet over a real disagreement.

        THE UNKNOWN NAME IS ALONE IN THE ANSWER, and it has to be. The first
        version of this printed `PORTABILITY` before it, and with the guard
        DELETED it still passed: a real module got past the empty-selection
        exit and the fixture reached every module by another road, so the
        assertion held either way. A test that cannot detect its own fix is
        the shape this repository keeps finding; mutation-checked both ways
        now.
        """
        port = (f"import sys\nprint({_selection_port().PROTOCOL!r})\n"
                "print('NEWMODULE')\n"
                "sys.stdin.buffer.read()\n")
        r = self._drive(self._orphan_hook(tmp_path, port), tmp_path)
        assert r.returncode == 2
        assert set(_selected_modules(r)) == set(ALL_MODULES), (
            "a flag the script does not declare was accepted, or the refusal "
            f"went quiet: {sorted(_selected_modules(r))}")


class TestSkillInvariantsHook:
    def test_a_bash_call_runs_the_focused_invariants(self, tmp_path):
        """Opaque shell helpers can rewrite covered prose without file_path.

        This still exercises the FULL run, and only because `tmp_path` has no
        `.claude/`: with nowhere to put a stamp the hook cannot know what
        changed, so it forces every module. Adding `.claude/` to this fixture
        would quietly convert this into a fast-path test that no longer proves a
        Bash call runs anything at all — which is the failure the whole module
        is written to make visible.
        """
        _fake_venv(tmp_path, exit_code=1, lines=["1 failed"])
        command = "python rewrite-helper.py --secret do-not-echo"
        result = subprocess.run(
            ["bash", str(SKILL_HOOK)],
            input=json.dumps({"tool_name": "Bash",
                              "tool_input": {"command": command}}),
            text=True, capture_output=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 2
        assert "after a Bash tool call" in result.stderr
        assert command not in result.stderr

    def test_a_non_skill_edit_is_ignored(self, tmp_path):
        """The path filter must run BEFORE the suite, not after it.

        The shim here would fail if it were ever reached, so a green result
        proves the filter short-circuited rather than that the checks passed.
        Without that ordering every Python edit in the repo would pay 10
        seconds, and a hook that slow on unrelated work gets removed.
        """
        _fake_venv(tmp_path, exit_code=1, lines=["should never run"])
        # NOT `agents/lib/x.py` any more: the engine directory became watched
        # when preflight.py started importing every module in it, so that path
        # would now prove the filter runs rather than that it short-circuits.
        # NOR `agents/x.py`, its successor here, for the same reason one step
        # later: test_secure_fs sweeps every `*.py` under `agents/`, so that
        # path is watched too and this test was asserting the gap
        # `test_a_python_file_deeper_under_agents_is_covered` now closes. A
        # changelog entry is outside every watched root and stays that way —
        # only `docs/QUICKSTART.md` and `docs/contributing/` are watched.
        r = _run_skill_hook(tmp_path, str(tmp_path / "docs/changelog/x.md"))
        assert r.returncode == 0, r.stderr
        assert r.stderr.strip() == ""

    def test_an_edit_through_the_claude_symlink_is_covered(self, tmp_path):
        """`.claude/skills/<name>` is a symlink to `.agents/skills/<name>`, so
        the same SKILL.md has two reachable paths. An agent that opened it by
        the second one must not escape the check."""
        _fake_venv(tmp_path, exit_code=1, lines=["1 failed"])
        r = _run_skill_hook(tmp_path,
                            str(tmp_path / ".claude/skills/tax-setup/SKILL.md"))
        assert r.returncode == 2

    def test_an_agent_definition_edit_is_covered(self, tmp_path):
        """`.claude/agents/*.md` names schema paths that `test_claude_agents.py`
        resolves against the real fetcher. Leaving it out of the matcher meant
        renaming a path there had no local signal at all — the exact failure
        that module exists to prevent."""
        _fake_venv(tmp_path, exit_code=1, lines=["1 failed"])
        r = _run_skill_hook(
            tmp_path, str(tmp_path / ".claude/agents/tax-law-freshness-auditor.md"))
        assert r.returncode == 2

    def test_a_file_outside_the_project_is_ignored(self, tmp_path):
        """The path pattern matches on substring, so a skill in another clone or
        in `~/.claude/skills` would run THIS repository's suite and report its
        failures against a file they say nothing about."""
        _fake_venv(tmp_path, exit_code=1, lines=["should never run"])
        outside = tmp_path.parent / "other-clone" / "skills" / "x" / "SKILL.md"
        r = _run_skill_hook(tmp_path, str(outside))
        assert r.returncode == 0
        assert r.stderr.strip() == ""

    def test_a_failing_check_exits_2_and_reports_the_tail(self, tmp_path):
        """The pipeline-status regression, pinned.

        `pytest … | tail -20 || exit 2` yields tail's status, so the hook exits
        0 on a red suite. Reintroducing that form turns this assertion red on
        the exit code alone, which is the only signal that distinguishes a hook
        that passes from one that is not wired up.
        """
        _fake_venv(tmp_path, exit_code=1,
                   lines=[f"noise {i}" for i in range(60)] + ["1 failed, 176 passed"])
        r = _run_skill_hook(tmp_path,
                            str(tmp_path / ".agents/skills/tax-setup/SKILL.md"))
        assert r.returncode == 2, "a failing check must surface, not pass silently"
        assert "1 failed, 176 passed" in r.stderr, (
            "the tail of the pytest output is what the agent acts on")
        assert "repository skill or agent definition" in r.stderr
        assert str(tmp_path) not in r.stderr, (
            "hook failures must not print a potentially sensitive local path")

    def test_runtime_path_containing_skills_is_ignored_without_disclosure(
            self, tmp_path):
        """Substring matches must not run checks or print taxpayer basenames."""
        _fake_venv(tmp_path, exit_code=1, lines=["1 failed"])
        runtime = tmp_path / "documents" / "skills" / "Taxpayer-Name.md"
        r = _run_skill_hook(tmp_path, str(runtime))

        assert r.returncode == 0
        assert r.stderr == ""
        assert "Taxpayer-Name.md" not in r.stderr

    def test_a_passing_check_is_silent(self, tmp_path):
        """Exit 0 with no output. A hook that narrates every successful edit is
        noise the user learns to ignore, and then misses the one that matters."""
        _fake_venv(tmp_path, exit_code=0, lines=["177 passed"])
        r = _run_skill_hook(tmp_path,
                            str(tmp_path / ".agents/skills/tax-setup/SKILL.md"))
        assert r.returncode == 0
        assert r.stderr.strip() == ""

    def test_a_missing_virtualenv_degrades_to_silence(self, tmp_path):
        """AGENTS.md §7 makes the venv the documented way to run the suite, so
        its absence means the checks are UNAVAILABLE, not that they failed. A
        contributor who has not run `python3 -m venv .venv` must not get a
        session that errors on every skill edit."""
        r = _run_skill_hook(tmp_path,
                            str(tmp_path / ".agents/skills/tax-setup/SKILL.md"))
        assert r.returncode == 0
        assert r.stderr.strip() == ""

    @pytest.mark.parametrize("name,value", [
        # pytest APPENDS this to every invocation it makes. An exported
        # `--collect-only` makes the suite exit 0 having executed no assertion
        # at all — reproduced against the real interpreter — and `-k` narrows
        # it to whatever the pattern keeps.
        ("PYTEST_ADDOPTS", "--collect-only"),
        # ...and this one deletes `assert` outright. pytest rewrites the
        # assertions in the tests it collects, so the suite is covered — but
        # several of these modules launch engine modules as SUBPROCESSES and
        # read an exit status, and nothing rewrites those. Reproduced: a
        # module whose self-test asserts something false exits 1 normally and
        # 0 under this.
        ("PYTHONOPTIMIZE", "1"),
        # ...and this one loads arbitrary code into the collection hooks. A
        # plugin implementing `pytest_collection_modifyitems` drops whatever
        # it likes before anything runs: reproduced with a four-line module,
        # `1 failed, 1 passed` became `1 passed` and the status went to 0.
        # Found in review of the slice that flipped selection to Python, and
        # present in the hook before that slice too.
        ("PYTEST_PLUGINS", "dropred"),
    ])
    def test_an_inherited_pytest_variable_cannot_reach_the_suite(
            self, tmp_path, name, value):
        """Two variables that turn this gate into a rubber stamp.

        Either way the hook reads exit 0, ADVANCES the change boundary and
        CLEARS the red carry-forward: a pass that measured nothing, recorded
        as a pass that measured everything. That is the worst outcome this
        file has.

        Asserted through the shim's own view of its environment rather than
        through a real pytest run, because the property is that the variable
        is gone by the time any interpreter starts.
        """
        (tmp_path / ".claude").mkdir()
        _fake_venv(tmp_path, exit_code=1,
                   lines=["$*", f"SEEN=[${{{name}-unset}}]"])
        r = _run_skill_hook(
            tmp_path, str(tmp_path / ".agents/skills/tax-setup/SKILL.md"),
            env_extra={name: value})
        assert "SEEN=[unset]" in r.stderr, (
            f"{name} reached the suite this gate runs, so an exported "
            f"{value!r} would make a pass report success having measured "
            f"nothing: {r.stderr[-300:]}")

    def test_the_project_inifile_stops_an_ancestor_pytest_ini(self, tmp_path):
        """The one rubber-stamp this hook cannot unset, and why it misses.

        Every other way of turning this gate green comes through the
        environment, and the script clears those. A `pytest.ini` is a FILE:
        `addopts = --collect-only` in one makes a failing suite report `N
        tests collected` and exit 0, and no `unset` reaches it.

        It misses because pytest's rootdir search stops at the FIRST inifile
        it finds, and this repository has one — `[tool.pytest.ini_options]` in
        pyproject.toml. That section is there to hold a `markers` list. Nobody
        moving the markers elsewhere would know they were deleting a guard,
        which is what this test is for: the protection was accidental until
        something asserted it.

        BOTH DIRECTIONS, because a pin that cannot fail proves nothing about
        the mechanism it claims. The ancestor file is read when the project
        has no inifile of its own, and not read when it has one.
        """
        assert "[tool.pytest.ini_options]" in (REPO / "pyproject.toml").read_text(), (
            "pyproject.toml no longer declares [tool.pytest.ini_options]. "
            "That section stops pytest's rootdir search before it reaches an "
            "ancestor directory's pytest.ini, and an ancestor holding "
            "`addopts = --collect-only` makes this gate report a green suite "
            "that ran nothing. Keep the section even if its contents move")

        (tmp_path / "pytest.ini").write_text(
            "[pytest]\naddopts = --collect-only\n")
        project = tmp_path / "project"
        (project / "tests").mkdir(parents=True)
        (project / "tests" / "test_red.py").write_text(
            "def test_fails(): assert False\n")

        def run():
            return subprocess.run(
                [sys.executable, "-m", "pytest", "tests/test_red.py", "-q",
                 "-p", "no:cacheprovider"],
                cwd=project, capture_output=True, text=True)

        exposed = run()
        assert "collected" in exposed.stdout and exposed.returncode == 0, (
            "an ancestor pytest.ini did NOT reach a project without an "
            "inifile of its own, so this test is not exercising the mechanism "
            f"it pins: {exposed.stdout[-300:]}")

        (project / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\nmarkers = []\n")
        defended = run()
        assert defended.returncode != 0, (
            "a project declaring [tool.pytest.ini_options] still read the "
            "ancestor's pytest.ini — the guard this repository relies on has "
            f"changed shape: {defended.stdout[-300:]}")

    def test_an_inherited_errexit_does_not_swallow_the_verdict(self, tmp_path):
        """`set -u` does not turn errexit OFF, and bash imports it.

        An exported `SHELLOPTS` containing `errexit` reaches this script, and
        the whole verdict rests on `out=$(run_focused_modules)` RETURNING so
        its `$?` can be read. Under errexit a red suite kills the script at
        that line instead: exit 1 with an empty stderr, which this script's
        own interface defines as "a signal killed the pass" — so a genuine
        failure is reported as no verdict and the tool call is not blocked.
        """
        (tmp_path / ".claude").mkdir()
        _fake_venv(tmp_path, exit_code=1, lines=["1 failed"])
        r = _run_skill_hook(
            tmp_path, str(tmp_path / ".agents/skills/tax-setup/SKILL.md"),
            env_extra={"SHELLOPTS": "errexit"})
        assert r.returncode == 2, (
            "a red suite under an inherited errexit did not block the tool "
            f"call (rc={r.returncode}, stderr={r.stderr[-200:]!r})")
        assert "FAILED" in r.stderr, r.stderr[-300:]

    def test_an_inherited_xtrace_does_not_bury_the_failure(self, tmp_path):
        """Noise rather than a wrong verdict, and it still matters here.

        `xtrace` arrives from the same exported SHELLOPTS. It changes no
        answer, but exit 2 feeds stderr back to the agent and a trace of the
        whole script buries the failing tail underneath it — 535 traced lines
        against the thirty the report itself is. A gate whose output cannot be
        read is one that gets reasoned past, which this file ranks above
        almost everything.
        """
        (tmp_path / ".claude").mkdir()
        _fake_venv(tmp_path, exit_code=1, lines=["1 failed"])
        r = _run_skill_hook(
            tmp_path, str(tmp_path / ".agents/skills/tax-setup/SKILL.md"),
            env_extra={"SHELLOPTS": "xtrace"})
        traced = [ln for ln in r.stderr.splitlines() if ln.startswith("+")]
        # ONE, not none: bash traces the very command that turns tracing off,
        # and nothing inside the script can get ahead of that. Asserting zero
        # would be asking for something unobtainable, which is how a correct
        # script comes to look broken. What the assertion is really saying is
        # that the trace stops at the clearing statement — so it names it.
        assert traced == ["+ set +exP -uo pipefail"], (
            f"{len(traced)} traced lines reached the agent's stderr, where "
            f"the failure report is: {traced[:3]}")

    def test_an_inherited_physical_does_not_drop_a_symlinked_edit(self, tmp_path):
        """`set -P` makes `cd` resolve symlinks, and the payload does not.

        The hook decides whether an edit is inside the project by comparing
        `file_path` against `"$PWD"/*`. Under `physical` the `cd` rewrites
        `$PWD` to the REAL path while the payload still carries the logical
        one, the prefix stops matching, `relative` comes back empty and the
        edit is dropped as "outside the project" — silently, exit 0, over a
        watched file. Reproduced before fixing: `/tmp` resolving to
        `/private/tmp` was enough.

        A symlinked checkout is ordinary (`/tmp` is one on macOS), and `set -P`
        in a shell profile is a thing people do, which is what puts this inside
        the threat model the script states rather than outside it with
        `noexec`.
        """
        real = tmp_path / "realproj"
        (real / ".claude").mkdir(parents=True)
        (real / "AGENTS.md").write_text("# watched\n")
        _fake_venv(real, exit_code=1, lines=["$*"])
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)

        r = subprocess.run(
            ["bash", str(SKILL_HOOK)],
            input=json.dumps({"tool_input": {
                "file_path": str(link / "AGENTS.md")}}),
            text=True, capture_output=True,
            env=dict(os.environ, CLAUDE_PROJECT_DIR=str(link),
                     SHELLOPTS="physical"))
        assert r.returncode == 2, (
            "an edit reached through a symlinked project path was dropped as "
            f"outside the project (rc={r.returncode})")
        assert _selects(PORTABILITY, _selected_modules(r)), sorted(
            _selected_modules(r))

    def test_an_inherited_cdpath_cannot_redirect_the_hook_to_another_clone(
            self, tmp_path):
        """CDPATH is searched BEFORE the relative path.

        So `cd "$CLAUDE_PROJECT_DIR"` with a RELATIVE value can land in a
        different directory entirely — after which this script reads another
        repository's files, writes its stamp there, and reports a verdict
        about a tree nobody edited.

        The project directory has to be RELATIVE for this to reach anything:
        bash never consults CDPATH for an absolute path, and every other test
        here passes one. That is why this case needs its own workspace rather
        than an extra assertion on an existing test.
        """
        decoy, real = tmp_path / "decoy", tmp_path / "real"
        for root in (decoy, real):
            (root / "proj" / ".claude").mkdir(parents=True)
            (root / "proj" / "AGENTS.md").write_text("# watched\n")
            _fake_venv(root / "proj", exit_code=0, lines=["1 passed"])

        r = subprocess.run(
            ["bash", str(SKILL_HOOK)],
            input=json.dumps({"tool_name": "Bash",
                              "tool_input": {"command": "ls"}}),
            text=True, capture_output=True, cwd=str(real),
            env=dict(os.environ, CLAUDE_PROJECT_DIR="proj",
                     CDPATH=str(decoy)))
        assert r.returncode == 0, r.stderr[-300:]
        # The STAMP says which tree the hook actually inspected. It is the
        # only artefact the pass leaves behind, and it lands wherever `cd`
        # went.
        assert (real / "proj" / SKILL_STAMP).is_file(), (
            "the hook wrote no stamp under the project it was pointed at")
        assert not (decoy / "proj" / SKILL_STAMP).exists(), (
            "an inherited CDPATH sent the hook into a different clone: it "
            "read that tree, wrote its stamp there, and would report a "
            "verdict about files nobody edited")

    def test_a_missing_jq_degrades_to_silence(self, tmp_path):
        """With no jq the hook cannot read which file was edited. Guessing
        would either run the suite on every write or skip skill edits; the
        script states the no-op explicitly, and this pins it.

        The stripped PATH keeps `bash` and nothing else — emptying it entirely
        would fail to launch the interpreter, which passes this assertion for
        the wrong reason.
        """
        _fake_venv(tmp_path, exit_code=1, lines=["should never run"])
        only_bash = tmp_path / "bin"
        only_bash.mkdir()
        (only_bash / "bash").symlink_to(shutil.which("bash"))
        r = _run_skill_hook(tmp_path,
                            str(tmp_path / ".agents/skills/tax-setup/SKILL.md"),
                            path=str(only_bash))
        assert r.returncode == 0
        assert r.stderr.strip() == ""

    def test_an_unchanged_watched_tree_does_not_rerun_the_suite(self, tmp_path):
        """The cheap-skip path, and the only assertion that can see it.

        A Bash payload names no file, so the previous hook ran the whole suite
        after EVERY shell call — `ls`, `git status`, the pytest run itself. On a
        loaded machine that exceeded the registered timeout, Claude Code
        cancelled the hook, and a cancelled check reads to the agent exactly
        like a check that passed. The replacement asks the filesystem instead:
        is any watched path newer than the stamp of the last completed pass?

        The red verdict here is the second half of the fix, not decoration. The
        stamp advances when the checks RAN, not when they PASSED, so a suite the
        agent is midway through repairing reports once per CHANGE instead of
        once per tool call — including on the `git diff` and `pytest` calls
        needed to repair it.
        """
        (tmp_path / ".claude").mkdir()
        (tmp_path / "AGENTS.md").write_text("# a watched root\n")
        _fake_venv(tmp_path, exit_code=1, lines=["1 failed"])
        time.sleep(STAMP_OVERLAP_SECONDS)

        first = _run_skill_hook_bash(tmp_path)
        assert first.returncode == 2, first.stderr
        assert (tmp_path / SKILL_STAMP).is_file(), (
            "no stamp was written, so every later call repeats the full run")

        second = _run_skill_hook_bash(tmp_path)
        assert second.returncode == 0, second.stderr
        assert second.stderr == "", (
            "a still-red suite re-fired on an unchanged tree — the finding was "
            "already delivered, and the repeat lands on the very commands the "
            "agent needs to diagnose it")

    def test_a_watched_change_after_a_reported_failure_fires_again(self, tmp_path):
        """The counterweight to the test above, and it is not optional.

        Suppression could be total — a hook that exits 0 forever passes the
        quiescence assertion perfectly. What makes the stamp a boundary rather
        than an off switch is that the next write to a watched path re-runs and
        re-reports, red verdict and all.
        """
        (tmp_path / ".claude").mkdir()
        watched = tmp_path / "AGENTS.md"
        watched.write_text("# a watched root\n")
        _fake_venv(tmp_path, exit_code=1, lines=["1 failed"])
        time.sleep(STAMP_OVERLAP_SECONDS)

        assert _run_skill_hook_bash(tmp_path).returncode == 2
        assert _run_skill_hook_bash(tmp_path).returncode == 0, (
            "the tree was not quiescent, so the assertion below proves nothing")

        watched.write_text("# a watched root, edited\n")
        again = _run_skill_hook_bash(tmp_path)
        assert again.returncode == 2, (
            "a watched file changed after the stamp advanced and the hook "
            "stayed silent — the gate is off, not fast")

    def test_a_skill_edit_does_not_pay_for_the_hook_module(self,
                                                           quiescent_workspace):
        """Per-module selection is where the measured saving actually comes from.

        One module dominates the cold pass — `tests/test_claude_hooks.py`, this
        one, because a hook can only be tested by running it and every assertion
        below spawns bash — and a skill edit cannot break a single one of its
        assertions. Selecting the union of what the changed paths can turn red
        puts a skill edit at roughly a tenth of a cold pass and an
        agent-definition edit at roughly a five-hundredth. Those RATIOS are the
        durable half; the seconds behind them live in exactly one place, the
        MEASURED COSTS block in `.claude/hooks/skill-invariants.sh`. Read them
        there rather than from a copy here — a copy is how one quantity comes to
        have three values, which is the state this comment used to be in.

        Each edit is preceded by a clear of the stamp's red set, because the
        subject here is what ONE PATH selects and the hook deliberately adds to
        that: the skill edit below exits 1, which records portability and
        capabilities as red, so the agent edit that follows correctly selects
        three modules. Asserting that union instead would go green against a
        hook that had stopped mapping paths to modules at all — every path
        would select everything, and every union assertion would hold.
        """
        _fake_venv(quiescent_workspace, exit_code=1, lines=["$*"])

        _clear_red_carry_forward(quiescent_workspace)
        skill_edit = _run_skill_hook(
            quiescent_workspace,
            str(quiescent_workspace / ".agents/skills/tax-setup/SKILL.md"))
        assert skill_edit.returncode == 2
        assert _selected_modules(skill_edit) == [PORTABILITY, CAPABILITIES,
                                                  AGENTS, PREFLIGHT, SECUREFS], (
            "a skill edit must run the modules that read skill prose — the "
            "agents module among them since V6 put a boundary claim in three "
            "skills — and pay for the hooks module and the host guard, neither "
            "of which a skill edit can turn red, in neither case")

        _clear_red_carry_forward(quiescent_workspace)
        agent_edit = _run_skill_hook(
            quiescent_workspace,
            str(quiescent_workspace
                / ".claude/agents/tax-law-freshness-auditor.md"))
        assert agent_edit.returncode == 2
        assert _selected_modules(agent_edit) == [AGENTS]

    # Every entry here can turn its module red, and not one of them is a `*.md`
    # under a skill directory — the only shape the previous filter accepted. The
    # fetcher is the case that names the defect outright: renaming a key there
    # is precisely what `test_claude_agents.py` exists to catch, since it
    # resolves every schema path the auditor cites against `create_empty_tax_law`.
    #
    # Two entries are here for a dependency no assertion mentions.
    # `capabilities.py` imports `rollover.py`, so a half-finished edit to
    # rollover is a COLLECTION error, not a failed assertion; and `skill_text.py`
    # is how both prose modules reach a skill at all, so its glob decides
    # whether reference files are read.
    WATCHED_SOURCE_PATHS = [
        ("agents/lib/tax_law_fetcher.py", AGENTS),
        # The SECOND engine module test_claude_agents.py reads. Its OBBBA window
        # decides whether the acceptance fixture is corrupt at all, so editing
        # that window is the change those guards exist to catch — and it fell
        # through to the generic `agents/lib/*` arm, which selects every
        # module except this one.
        ("agents/lib/ptet.py", AGENTS),
        # The THIRD, and the one whose edge is not a path literal anywhere: the
        # Replay A guard IMPORTS this module and asserts what the shipped
        # `compute_qbi_deduction` returns on the record's own case. The locator
        # in the test below recovers `REPO / "..."` reads and finds nothing to
        # recover here, so this row is the whole of the local signal — without
        # it a change to the Form 8995 cap left the hook green and turned
        # test_claude_agents red at CI. W2 made it a PACKAGE, and the path
        # here is the FEDERAL half specifically — `compute_qbi_deduction` is
        # in `__init__.py`, and pointing this row at the directory would claim
        # `state/ny.py` can turn the agents module red, which it cannot.
        ("agents/lib/tax_computations/__init__.py", AGENTS),
        ("agents/lib/capabilities.py", CAPABILITIES),
        ("agents/lib/rollover.py", CAPABILITIES),
        ("agents/templates/taxpayer_profile_template.json", CAPABILITIES),
        # The row the negative list's docstring below already claims exists.
        # `result_template.json` left that list when test_portability began
        # building a prior-year return out of it, and joined this one only in
        # prose: the `case` arm was added to the hook, both lists were called
        # "one claim seen from both directions", and nothing here was touched.
        # Deleting that arm left the whole suite green while an edit to the
        # template selected no module at all.
        ("agents/templates/result_template.json", PORTABILITY),
        ("AGENTS.md", PORTABILITY),
        ("CLAUDE.md", PORTABILITY),
        ("README.md", PORTABILITY),
        ("LICENSE", PORTABILITY),
        (".gitignore", PORTABILITY),
        (".pii-allowlist", PORTABILITY),
        (".vscode/settings.json", PORTABILITY),
        ("agents/install.sh", PORTABILITY),
        ("docs/contributing/agents-compat.md", PORTABILITY),
        ("tests/skill_text.py", PORTABILITY),
        ("tests/engine_tree.py", PORTABILITY),
        ("tests/engine_tree.py", HOSTGUARD),
        # The W2 tail moved preflight's roster comparison and secure_fs's
        # unredacted-path roster onto the helper too. Both ask it what the
        # engine CONTAINS, so narrowing its walk narrows a roster — and a
        # narrowed roster is the shape that passes green over the files it
        # stopped seeing.
        ("tests/engine_tree.py", PREFLIGHT),
        ("tests/engine_tree.py", SECUREFS),
        ("scripts/pre-commit-pii-scan.sh", HOOKS),
        (".claude/settings.json", HOOKS),
        (".claude/hooks/harden-runtime-perms.sh", HOOKS),
        ("tests/test_claude_hooks.py", HOOKS),
        # A path appears once PER MODULE it has to reach. One row per path
        # pinned only the first module of a multi-module arm, and the arms
        # below were the ones nothing else covered: the `REPO / "literal"`
        # locator in the test after this cannot recover a read written over a
        # loop, and both documents here are read that way. Deleting
        # `RUN_PREFLIGHT=1` from the QUICKSTART/agents-compat arm used to leave
        # the whole suite green while an edit dropping the launcher string from
        # either turned test_preflight red at CI only.
        ("docs/contributing/agents-compat.md", PREFLIGHT),
        ("docs/QUICKSTART.md", PREFLIGHT),
        ("docs/QUICKSTART.md", PORTABILITY),
        ("requirements.in", PREFLIGHT),
        ("agents/lib/run.sh", PREFLIGHT),
        ("agents/lib/run.sh", PORTABILITY),
        ("agents/lib/run.sh", SECUREFS),
        ("tests/skill_text.py", PREFLIGHT),
        ("tests/skill_text.py", CAPABILITIES),
        # conftest.py runs before EVERY module below and can end the run before
        # one test of any of them executes, so its arm selects all seven and
        # every one of them is pinned here. It was watched one by one before
        # `tests` became a root, and until it was listed the file that can stop
        # collection reached `nothing_selected` and an edit to it got no local
        # signal at all. Reported against PR #82, which is the commit that added
        # the file.
        ("tests/conftest.py", PORTABILITY),
        ("tests/conftest.py", CAPABILITIES),
        ("tests/conftest.py", AGENTS),
        ("tests/conftest.py", HOOKS),
        ("tests/conftest.py", PREFLIGHT),
        ("tests/conftest.py", SECUREFS),
        ("tests/conftest.py", HOSTGUARD),
        ("scripts/pre-commit-pii-scan.sh", PORTABILITY),
        ("scripts/pre-commit-pii-scan.sh", SECUREFS),
        # EVERY module in tests/ is source behind
        # `TestNoTestSpawnsABareInterpreter`, which globs the directory whole.
        # The seven with arms of their own are the easy half; the ~forty
        # without were the gap, and the locator in the test below cannot see it
        # — it recovers `REPO / "literal"` reads and a glob is not one. Three
        # shapes are pinned: a module with a rich arm, the hooks module (whose
        # arm was split off `.claude/hooks` for this), and one with no arm at
        # all, which reaches the catch-all or nothing.
        ("tests/test_preflight.py", HOSTGUARD),
        ("tests/test_claude_hooks.py", HOSTGUARD),
        ("tests/skill_text.py", HOSTGUARD),
        ("tests/test_scenario.py", HOSTGUARD),
        ("tests/test_tax_computations.py", HOSTGUARD),
    ]

    def test_every_watched_source_path_reaches_its_module(
            self, quiescent_workspace):
        """A path left out of the mapping produces no local signal at all, which
        is indistinguishable from a path that has no invariants to begin with.

        One loop rather than one parametrized case each: the fixture's settling
        sleep is real wall time, and this module is already the one that
        dominates the hook's own pass (see MEASURED COSTS in
        `.claude/hooks/skill-invariants.sh` for what that costs), spawning a
        bash process per assertion inside a hook that has to stay fast.
        """
        _fake_venv(quiescent_workspace, exit_code=1, lines=["$*"])
        wrong = []
        for relative, module in self.WATCHED_SOURCE_PATHS:
            # The shim exits 1, so each iteration leaves its modules recorded as
            # red and the next one unions them in — by the end of the table a
            # path that selects NOTHING would still be handed most of the suite
            # and pass. Clear between iterations so each row measures its own
            # arm.
            _clear_red_carry_forward(quiescent_workspace)
            r = _run_skill_hook(quiescent_workspace,
                                str(quiescent_workspace / relative))
            if r.returncode != 2 or not _selects(module, _selected_modules(r)):
                wrong.append(f"{relative} -> {_selected_modules(r) or 'nothing'}"
                             f" (needs {module})")
        assert not wrong, (
            "these watched paths do not reach the module that reads them: "
            f"{wrong}")

    def test_every_watched_module_that_reads_a_shared_helper_is_in_its_arm(
            self):
        """The table above runs one direction: a watched PATH reaches a module.
        This is the other, and it is the one that rots — a test module grows an
        import, and the helper's arm is somewhere else entirely.

        ALL THREE SIDES derived, none listed. The helpers come from the `tests/`
        directory, the importers from the test files' own source, and the
        selections from the script — so a module that starts reading
        `tests/engine_tree.py` tomorrow, or a THIRD helper added beside it, is
        covered without anybody remembering this test exists. A hardcoded pair
        of helper names was the first draft, and a list that cannot see an
        addition is the same silent-green this whole mechanism is about.

        It matters more for these helpers than for most: the modules reading
        `engine_tree` do not merely import it, they ask it what `agents/lib/`
        CONTAINS, and a narrowed walk narrows a roster. A narrowed roster
        passes green over the files it stopped seeing — the exact failure the
        helper was written to end, reintroduced by the helper itself.

        Scoped to the seven modules the hook can run. `test_workspace.py`,
        `test_irs_dates.py`, `test_tax_computations.py` and `test_projection.py`
        read a helper too and are not among them, so CI is their only signal;
        that is a cost of the hook's scope, not a gap here.
        """
        helpers = _shared_test_helpers()
        assert helpers, "no shared helpers under tests/ — the derivation broke"

        read_by_a_watched_module, missing_arm, selects_too_little = [], [], []
        for helper in helpers:
            importers = {m for m in ALL_MODULES if _imports(REPO / m, helper)}
            if not importers:
                # `ny_fixtures.py` today: real, shared, and read by nothing the
                # hook runs. Nothing to assert, and demanding an arm for it
                # would be demanding cost for no coverage.
                continue
            read_by_a_watched_module.append(helper)
            arm = _arm_selecting(helper)
            if arm is None:
                missing_arm.append(f"{helper} (read by {sorted(importers)})")
                continue
            short = sorted(importers - arm)
            if short:
                selects_too_little.append(f"{helper} -> {short}")

        assert read_by_a_watched_module, (
            "no shared helper is imported by any of the seven — either the "
            "spellings changed or this assertion has stopped measuring "
            "anything")
        assert not missing_arm, (
            f"{missing_arm} have no `case` arm at all, so an edit to one "
            "falls through to the generic `tests` arm and reaches almost "
            "nothing that reads it")
        assert not selects_too_little, (
            f"{selects_too_little} read a helper whose arm does not select "
            "them. Editing the helper would leave them green locally, and the "
            "ones asking it what the engine CONTAINS would have a roster "
            "narrowed silently — the defect the helper exists to end.")

    def test_the_arm_reader_survives_a_multi_pattern_arm(self):
        """Cry-wolf validation for `_arm_selecting`.

        The question an arm answers is membership in its pattern LIST, and the
        dominant shape here groups several patterns under one module set.
        Merging two arms — correct, behaviour-preserving, and the obvious next
        move whenever their selections converge — must not make a correct
        table read as a missing arm. The bash version of this reader failed
        exactly that way over `a|b)`.
        """
        # DERIVED from the table rather than named, and NOT FIRST in its arm,
        # which is the whole question. The version that stood here asserted
        # about `tests/engine_tree.py`, which sits in a ONE-pattern arm — so
        # `patterns == (pattern,)` passed it, and the multi-pattern shape the
        # docstring is about was never exercised. The bash reader's `a|b)`
        # failure would have gone unnoticed by its replacement.
        multi = [(pats, mods) for pats, mods in _selection_port()._ARMS
                 if len(pats) > 1]
        assert multi, (
            "no arm lists more than one pattern any more, so the membership "
            "question this reader answers is not one the table asks — delete "
            "this test WITH the claim in `_arm_selecting`, or neither")
        for patterns, modules in multi:
            for pattern in patterns[1:]:
                assert _arm_selecting(pattern) is not None, (
                    f"{pattern!r} is listed in an arm and reads as having no "
                    "arm at all — the reader is matching the arm's FIRST "
                    "pattern, or the whole tuple, rather than membership in "
                    "it, which makes every 'no arm' finding it reports suspect")
        assert _arm_selecting("tests/absent.py") is None, (
            "a path with no arm must read as ABSENT, not as an arm that "
            "failed to parse: the two are different findings")

    def test_the_arm_reader_names_the_same_modules_the_port_selects(self):
        """...and the reader agrees with the DECISION, not just the table.

        `_arm_selecting` reads `_ARMS` directly, which is the same data
        `select_modules_for` walks — but only for an arm no EARLIER arm
        shadows. Every caller below hands it a path it expects to decide, so
        the two must agree there, and the day one stops deciding its own path
        the callers are asserting about an arm nothing reaches.
        """
        port = _selection_port()
        for path in ("tests/engine_tree.py", "tests/skill_text.py",
                     "agents/lib/secure_fs.py", "agents/lib/ptet.py"):
            decided = _module_paths(port.select_modules_for(path))
            assert _arm_selecting(path) == decided, (
                f"{path} has an arm selecting {sorted(_arm_selecting(path))} "
                f"and the port decides {sorted(decided)} — an earlier arm now "
                "shadows it, so every assertion made through this reader is "
                "about an arm that never runs")

    def test_the_path_locator_does_not_read_a_comment_as_a_read(self):
        """Cry-wolf validation for `_code_only`, which the locator now uses.

        The locator is a regex over source text and cannot tell prose from
        code. It read a comment warning AGAINST a spelling as a use of it, and
        asked for a hook arm covering the whole suite directory — cost with no
        coverage behind it, on a module that was right.
        """
        # A directory that does NOT exist, because the locator reading this
        # module recovers string literals from its source and would report the
        # probe itself as a genuine read — which is the same cry-wolf one
        # level up.
        commented = 'x = 1  # REPO / "elsewhere" is what NOT to write here\n'
        assert '"elsewhere"' not in _code_only(commented), (
            "a path named in a comment is prose; the module does not read it")
        real = 'p = REPO / "elsewhere"\n'
        assert '"elsewhere"' in _code_only(real), (
            "stripping comments must not touch code — a real read that "
            "vanished here would be a watched path silently unwatched")
        assert '#' in _code_only('s = "a#b"\n'), (
            "a `#` inside a string is not a comment, and cutting at the first "
            "one would corrupt the line it sits on")

    def test_the_import_reader_knows_every_spelling_in_this_suite(self):
        """`tests/` is on `sys.path` for part of this suite, so the bare form
        is live — `test_projection.py` writes `from engine_tree import ...`.
        A reader that knew only the dotted form would answer "does not read
        it" for a module that does, silently narrowing the set it checks."""
        import tempfile
        forms = ["from tests.engine_tree import engine_files",
                 "from tests import engine_tree",
                 "from engine_tree import engine_files",
                 "import tests.engine_tree",
                 "import tests.engine_tree as ET",
                 "import engine_tree",
                 "from .engine_tree import engine_files",
                 "from . import engine_tree"]
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            for form in forms:
                probe.write_text(form + "\n", encoding="utf-8")
                assert _imports(probe, "tests/engine_tree.py"), form
            probe.write_text('# tests/engine_tree.py is named here only\n'
                             'X = ["tests/engine_tree.py"]\n', encoding="utf-8")
            assert not _imports(probe, "tests/engine_tree.py"), (
                "naming the helper in a table or a comment is not reading it "
                "— this module does exactly that, and a substring rule called "
                "it an importer")

    def test_a_per_file_arm_never_selects_less_than_the_generic_one(
            self, quiescent_workspace):
        """`case` is first-match-wins, so an engine module with its OWN arm gets
        exactly what that arm lists — never the generic `agents/lib/*` arm
        below it.

        Every such arm therefore has to REPEAT the generic selections on top of
        its extra ones, and twice now it has not: RUN_PORTABILITY was missing
        first, and after that fix RUN_SECUREFS was still missing from the three
        files where it matters most. `test_secure_fs` sweeps `agents/**/*.py`
        for a network import and for a raw path reaching a message, so a fetcher
        that grew an outbound call turned it red while this hook ran five
        modules, passed them all, and exited 0 silent.

        The table above cannot see this: it asserts one module per path, so a
        path already reaching `test_claude_agents.py` satisfies it while missing
        two others. Derive the arms FROM THE SCRIPT rather than listing them —
        a fifth per-file arm added later is covered the day it is written.

        BOTH SIDES from the script, not one. The per-file arms were derived and
        the generic selection was a hardcoded set of three, so when the
        generic engine arm gained the host-guard flag the set did not, and
        this test stayed green over five arms that never selected it: editing
        `secure_fs.py` to drop its package import turned
        `test_host_guard.py::TestEveryModuleIsCovered` red at CI while the hook
        exited 0. Reported by review against PR #82. A half-derived comparison
        is the same failure this test's first paragraph records, one level up.
        """
        # From `_ARMS`, which is where the arms live since W3 slice 2. The
        # shape being looked for is unchanged: a pattern naming ONE engine
        # module, which first-match-wins hands its own arm instead of the
        # generic one below it.
        paths = sorted({q for patterns, _ in _selection_port()._ARMS
                        for q in patterns
                        if q.startswith("agents/lib/") and q.endswith(".py")
                        and "*" not in q})
        assert len(paths) >= 3, (
            f"no per-file engine arms found — the shape stopped matching the "
            f"table, so this test now asserts nothing (found {paths})")

        generic = _arm_selecting("agents/lib/*")
        assert generic >= {PREFLIGHT, SECUREFS, PORTABILITY, HOSTGUARD}, (
            f"the generic engine arm selects only {sorted(generic)} — the "
            "derivation lost a flag, or the arm did")
        _fake_venv(quiescent_workspace, exit_code=1, lines=["$*"])
        short = []
        for relative in paths:
            # Per ITERATION. The shim exits 1 to make the argv readable, so each
            # run records its own modules as red and the next one legitimately
            # unions them in — which means the second path onwards inherits
            # `securefs` from the first and this test passes with the fix
            # reverted. Verified: without this line, deleting RUN_SECUREFS from
            # the tax_law_fetcher arm left it green.
            _clear_red_carry_forward(quiescent_workspace)
            selected = set(_selected_modules(
                _run_skill_hook(quiescent_workspace,
                                str(quiescent_workspace / relative))))
            if not generic <= selected:
                short.append(f"{relative} misses {sorted(generic - selected)}")
        assert not short, (
            "these engine modules have their own case arm and it selects less "
            f"than `agents/lib/*` would: {short}")

    # The paths whose edits must NOT reach this module. Every other assertion
    # about the mapping is a lower bound — "does this path reach the module that
    # reads it" — and a lower bound is satisfied by an arm that selects
    # everything. Cost is the only thing that makes an over-broad arm wrong, so
    # it is the only thing that can catch one, and this module is where the cost
    # lives: it dominates every total in MEASURED COSTS.
    #
    # Both entries are TEMPLATES sitting in a `case` list beside a test module.
    # `taxpayer_profile_template.json` shared an arm with `test_capabilities.py`,
    # and when the six test-module arms grew RUN_HOOKS — because this module
    # PARSES their source — the template silently came along, so editing a JSON
    # template began costing a full pass of the slowest module in the suite. The
    # list is short deliberately: adding a row is a claim that a path has no
    # invariant here, which is exactly the claim that goes stale silently, so it
    # is made one path at a time and never by pattern.
    #
    # PAIRED with the module that DOES read each path, and not written as a bare
    # tuple of paths, for a reason this file supplies itself: `_path_constants`
    # below recovers exactly that shape — a constant tuple whose every element
    # names an existing file — and reads it as a set of paths THIS module reads.
    # Spelled that way, a list declaring two paths uncoupled from the hooks
    # module made the coverage test demand they select it, and the two tests
    # asserted opposite things about the same two paths. A pair is also the
    # honester row: "not hooks" alone is half a claim, and the half that rots.
    UNCOUPLED_FROM_HOOKS = (
        ("agents/templates/taxpayer_profile_template.json", CAPABILITIES),
        ("agents/templates/result_template.json", PORTABILITY),
    )

    def test_a_template_edit_does_not_pay_for_the_hooks_module(
            self, quiescent_workspace):
        """An arm that selects too much is silent, and pays in seconds forever.

        Nothing else in this class can see it. `WATCHED_SOURCE_PATHS` asserts
        one module per path and is satisfied by an arm selecting all six; the
        per-file-arm test asserts a per-file arm selects no LESS than the
        generic one. Both are lower bounds, and the failure here is an upper one
        — which is why it shipped in the round that added RUN_HOOKS to the six
        test-module arms and was found by review rather than by this suite.

        Asserted against the hooks module alone rather than against an exact
        expected set: the exact set is what the two lower-bound tests already
        own, and duplicating it here would make every legitimate arm change fail
        in two places. What this adds is the one module whose selection dominates
        every total in MEASURED COSTS — on the decomposition there it is larger
        than the other five rows put together.
        """
        _fake_venv(quiescent_workspace, exit_code=1, lines=["$*"])
        coupled = []
        for relative, reader in self.UNCOUPLED_FROM_HOOKS:
            assert (REPO / relative).is_file(), (
                f"{relative} no longer exists — this row asserts nothing, and "
                "a row asserting nothing about cost is how the arm it watches "
                "grows back")
            # Per iteration, for the reason every other loop in this class
            # carries the same line: the shim exits 1, so each run records its
            # modules as red and the next unions them in — and `hooks` recorded
            # once would then be handed to every row after it.
            _clear_red_carry_forward(quiescent_workspace)
            selected = _selected_modules(
                _run_skill_hook(quiescent_workspace,
                                str(quiescent_workspace / relative)))
            if _selects(HOOKS, selected):
                coupled.append(f"{relative} -> {sorted(selected)}")
            # The other half of the pair. An arm rewritten to select NOTHING
            # would satisfy the check above trivially, and a cost test that
            # passes hardest when the mapping is emptiest is the wrong test.
            assert _selects(reader, selected), (
                f"{relative} no longer reaches {reader}, which reads it — this "
                "row is now asserting only that a dead arm is cheap")
        assert not coupled, (
            "editing these templates now runs the hooks module, which reads "
            f"none of them: {coupled}. It is the slowest module in the suite "
            "(MEASURED COSTS), so this is a cost regression with no signal "
            "other than this test — check whether a `case` arm was merged with "
            "one that names a test module")

    def test_a_test_module_edit_does_not_pay_for_the_whole_hooks_module(
            self, quiescent_workspace):
        """The second UPPER bound on the mapping, and the one the cost of this
        module actually turns on.

        Five of the six test-module arms carried `RUN_HOOKS=1`, so every save of
        `tests/test_preflight.py` — the file this branch grows fastest — ran the
        module that dominates every total in MEASURED COSTS. The dependency it
        was paying for is three assertions: the watched-set locator, the paired
        lower bound beside it, and the deselect pin, which resolves both
        `--deselect` ids against tests/test_portability.py's own source.
        Everything else in this module reads the SCRIPT and the two hooks, not
        the test modules.

        This is the UPPER bound and not a claim that the three are cheap — what
        they cost is in MEASURED COSTS under THE TRIGGER WITH NO ROW, and it is
        about three preflight modules for a preflight save. The bound that
        matters is the one an arm can regress through: back to `RUN_HOOKS` and
        the trigger pays the dominant row again.

        Nothing else here can see this. Both coverage tests are lower bounds and
        `_selects` satisfies them from a node id, which is the whole point —
        so an arm quietly widened back to the module would keep them green and
        cost the dominant row forever.
        """
        _fake_venv(quiescent_workspace, exit_code=1, lines=["$*"])
        widened = []
        for module in (PORTABILITY, CAPABILITIES, AGENTS, PREFLIGHT, SECUREFS,
                       HOSTGUARD):
            _clear_red_carry_forward(quiescent_workspace)
            selected = _selected_modules(
                _run_skill_hook(quiescent_workspace,
                                str(quiescent_workspace / module)))
            if HOOKS in selected:
                widened.append(f"{module} -> {sorted(selected)}")
            # The other half of the pair, for the reason the template test
            # carries one: an arm selecting NOTHING satisfies the check above
            # perfectly, and a cost test that passes hardest when the mapping is
            # emptiest is the wrong test.
            assert _selects(HOOKS, selected), (
                f"{module} no longer reaches the assertions in {HOOKS} that "
                "parse it — the dependency is real and this row now asserts "
                "only that a dead arm is cheap")
            assert _selects(module, selected), (
                f"{module} no longer selects itself")
        assert not widened, (
            f"editing these test modules runs the whole hooks module: {widened}. "
            "It is the slowest in the suite (MEASURED COSTS) and the dependency "
            "is three assertions — check whether an arm went back to RUN_HOOKS")

    def test_an_unarmed_test_module_pays_the_host_guard_and_no_more(
            self, quiescent_workspace):
        """The catch-all is scoped, and a catch-all is where scope goes to die.

        `tests/test_scenario.py` has no arm of its own and no module here reads
        it, so the only thing its edit can turn red is the bare-interpreter
        sweep — the cheapest row in MEASURED COSTS. Widening the arm to
        `select_every_module`, or to the hooks module, would satisfy the
        coverage row above just as well and put a cold pass behind every save
        in a directory of forty-odd files. The lower bound cannot see that;
        this is the upper one.
        """
        _fake_venv(quiescent_workspace, exit_code=1, lines=["$*"])
        _clear_red_carry_forward(quiescent_workspace)
        selected = _selected_modules(
            _run_skill_hook(quiescent_workspace,
                            str(quiescent_workspace / "tests/test_scenario.py")))
        assert selected == [HOSTGUARD], (
            f"an unarmed test module selects {selected}; the sweep that makes "
            f"it watched at all lives in {HOSTGUARD} and nothing else here "
            f"reads it")

    def test_compiled_test_bytecode_selects_nothing(self, quiescent_workspace):
        """`tests` is a find ROOT now, so a pytest run's own `__pycache__`
        writes are printed by the scan on the next Bash call. The arm is scoped
        to `*.py` for exactly that reason — a bare `tests/*` would put a pytest
        module behind every `ls` that followed a test run, which is the fast
        path this hook exists to protect."""
        _fake_venv(quiescent_workspace, exit_code=1, lines=["$*"])
        _clear_red_carry_forward(quiescent_workspace)
        r = _run_skill_hook(
            quiescent_workspace,
            str(quiescent_workspace
                / "tests/__pycache__/test_scenario.cpython-312.pyc"))
        assert r.returncode == 0, (
            "compiled bytecode is not source the sweep parses, and every test "
            "run rewrites it")

    def test_the_hooks_dependents_are_real_and_complete(self):
        """A node id that names no test selects nothing, and pytest says so only
        by refusing the whole pass with exit 4 — which the hook turns into a
        block with no verdict behind it, on every call until someone notices.

        So the list is pinned from both ends. Every id resolves EXACTLY to a
        test that exists, and the membership is a SET rather than two required
        names: a lower bound cannot see an addition, and what shipped was a list
        describing itself as "the whole of what an edit to one of those modules
        can turn red" with the deselect pin — the one assertion here that names
        a test module by path — left out of it.
        """
        script = (HOOKS_DIR / "skill-invariants.sh").read_text()
        listed = re.findall(r'^\s*"(tests/test_claude_hooks\.py::[^"]+)"$',
                            script, re.M)
        assert len(listed) >= 3, (
            f"the HOOKS_DEPENDENTS list stopped parsing: {listed}")

        out = subprocess.run(
            [sys.executable, "-m", "pytest", HOOKS, "--collect-only", "-q",
             "-p", "no:cacheprovider"],
            cwd=REPO, text=True, capture_output=True).stdout
        collected = [ln.strip() for ln in out.splitlines()
                     if ln.strip().startswith(HOOKS + "::")]
        assert len(collected) > 100, (
            f"collecting {HOOKS} produced {len(collected)} node ids, so the "
            f"resolution check below would pass against an empty listing: "
            f"{out[-400:]}")
        missing = _unresolvable(listed, collected)
        assert not missing, (
            f"these node ids name no test — the hook hands pytest a selection "
            f"it answers with exit 4, which the hook reports as a block with no "
            f"verdict behind it: {missing}")

        # ...and the control on the exactness, because the check it replaced
        # was a substring search that a strict PREFIX satisfied. Driven through
        # the same helper the assertion above uses: a control carrying its own
        # copy of the rule passes against the copy.
        real = f"{HOOKS}::TestX::test_y_or_nothing"
        prefix = real[:-len("_or_nothing")]
        assert _unresolvable([prefix], [real]) == [prefix], (
            "a node id that is a strict prefix of a real one resolves — which "
            "is the substring search this replaced, and it collects nothing")
        assert _unresolvable([prefix], [f"{prefix}[case]"]) == [], (
            "a dependent that grew a `parametrize` reads as naming no test; "
            "pytest selects every case from the bracket-free id")

        found = {node.split("::", 1)[1] for node in listed}
        assert found == HOOKS_DEPENDENTS, (
            f"the hook selects {sorted(found)} for an edit to one of the six "
            f"other test modules; this suite pins {sorted(HOOKS_DEPENDENTS)}. "
            "Adding one is a cost decision (MEASURED COSTS, THE TRIGGER WITH "
            "NO ROW) and removing one drops a check to CI — either way, say so "
            "here as well as there")
        assert HOOKS_DEPENDENTS_EXCLUDED not in found, (
            f"{HOOKS_DEPENDENTS_EXCLUDED} is selected by the six test-module "
            "arms again. It goes red the moment a test is ADDED to one of them "
            "and stays red until MEASURED COSTS is refreshed in six places, so "
            "it blocks every tool call of the round that makes the refresh "
            "possible — the gates-that-cry-wolf pattern")

    def test_a_python_file_deeper_under_agents_is_covered(
            self, quiescent_workspace):
        """`test_secure_fs` sweeps `agents/**` and `scripts/**` WHOLE — every
        `*.py` and `*.sh` at any depth — for a network import, a bare `chmod`
        and a raw path reaching a message. The hook's catch-all for that sweep
        was `agents|scripts`, which matches those two words exactly, so a file
        one level further down (`agents/tools/check.py`) matched no arm at all.

        Neither branch has a fallback: the Write/Edit branch exits 0 the moment
        nothing is selected, and the Bash branch hands each path `find` printed
        to this same function. So a new file outside `agents/lib/` could add an
        outbound call or drop a redaction and turn `test_secure_fs` red with no
        local signal whatsoever — CI only.

        The negative half is not padding. The arm is scoped to the two
        EXTENSIONS the sweep reads rather than a bare `agents/*`, because an
        engine run rewrites `agents/lib/__pycache__/*.pyc`; a bare pattern would
        select a pytest module on the next Bash call after every single one.
        """
        _fake_venv(quiescent_workspace, exit_code=1, lines=["$*"])
        missed = []
        for relative in ("agents/tools/check.py", "scripts/helpers/check.sh",
                         "agents/tools/nested/deeper/check.sh"):
            _clear_red_carry_forward(quiescent_workspace)
            r = _run_skill_hook(quiescent_workspace,
                                str(quiescent_workspace / relative))
            if r.returncode != 2 or SECUREFS not in _selected_modules(r):
                missed.append(f"{relative} -> {_selected_modules(r) or 'nothing'}")
        assert not missed, (
            "these files are inside a tree test_secure_fs sweeps whole and the "
            f"hook selects nothing for them: {missed}")

        _clear_red_carry_forward(quiescent_workspace)
        bytecode = _run_skill_hook(
            quiescent_workspace,
            str(quiescent_workspace
                / "agents/lib/__pycache__/preflight.cpython-312.pyc"))
        assert bytecode.returncode == 0, (
            "compiled bytecode is not source the sweep reads, and it is "
            "rewritten by every engine run — selecting on it would put a "
            "pytest module behind every `ls` that followed one")

    @pytest.mark.parametrize("relative", [
        "agents/templates/answers_template.md",
        "docs/changelog/epic-V.md",
    ])
    def test_an_unwatched_source_edit_stays_silent(self, tmp_path, relative):
        """The counterweight to the parametrization above: the watched set is a
        list, not a mood.

        The generic engine arm USED to be the sharpest case here, on the reasoning
        that widening the filter to the engine would make every module edit pay
        for a pytest run that said nothing about it. That reasoning expired when
        `preflight.py` arrived: `check()` imports all twenty-one modules and
        executes one, so `test_preflight.py` says something about every single
        one of them, and a SyntaxError in `projection.py` really does turn it
        red. Engine modules are watched now, and the cost is the preflight
        module alone — between capabilities and portability (MEASURED COSTS).
        `requirements.txt` expired the same way one step further out: the
        preflight gate now compares INSTALLED distributions against the lock,
        and `test_preflight.py` reads the file to derive what a pin says, so a
        Dependabot bump that drops or renames a pin turns that module red.

        The paths kept here are ones no watched module reads: a template no
        invariant test loads, and a per-epic changelog. `result_template.json`
        was the template here until `test_portability.py` began building a
        prior-year return out of it, and it moved to the watched side in the
        same edit; `docs/roadmap/` was the second path here until
        `TestTheBacklogSaysWhatIsOpen` began reading the 09-05 backlog, and it
        moved for the same reason. The two lists are one claim seen from both
        directions, so a path leaves this one by JOINING that one, never by
        being quietly dropped — and this docstring records each departure
        because the alternative is a shrinking control nobody notices.

        The positive control at the end is what makes the silence mean
        anything: a hook that exits 0 for every payload — deleted, renamed,
        broken at its first line — satisfies the assertion above perfectly.
        `AGENTS.md` is the control rather than a skill file for a second
        reason. The filter this replaced accepted `*.md` under the skill and
        agent directories and nothing else, so a skill file fires under both
        and pins only that SOMETHING fires; AGENTS.md is watched by exactly one
        of them, which is the same claim the parametrized paths make from the
        other side.
        """
        _fake_venv(tmp_path, exit_code=1, lines=["should never run"])
        r = _run_skill_hook(tmp_path, str(tmp_path / relative))
        assert r.returncode == 0, r.stderr
        assert r.stderr.strip() == ""

        assert _run_skill_hook(tmp_path, str(tmp_path / "AGENTS.md")) \
            .returncode == 2, (
            "the same workspace and the same shim stayed silent for a WATCHED "
            "path too, so the silence above measures nothing about the filter")

    def test_the_watched_set_still_covers_what_the_modules_read(
            self, quiescent_workspace):
        """The staleness pin the hook's own comment says it needs.

        `WATCHED_ROOTS` and `select_modules_for` are a hand transcription of
        what four Python modules happen to read today, and nothing recomputes
        it. The drift mode is silent in the direction that matters: the day
        `test_claude_hooks.py` starts asserting against AGENTS.md, an AGENTS.md
        edit keeps selecting `test_portability.py` alone and the miss shows up
        nowhere. So re-derive the read set from the modules themselves and drive
        the real hook once per path.

        Two shapes are recoverable: a `REPO / "…"` literal, and a module-level
        CONSTANT holding a tuple of paths that a loop then joins to REPO. The
        second was added because the first cannot see the shape that actually
        grew — `test_preflight.DOCUMENTS`, seven documents swept for a
        bare-interpreter engine call, read as `REPO / rel` over a variable. Two
        of its entries reached CI with no local signal at all: AGENTS.md
        selected the portability module alone, and `.github/ISSUE_TEMPLATE` was
        neither watched nor mapped. The hand list above was the compensating
        mechanism and its own comment says so; a hand list only compensates for
        what somebody remembers to add to it, and nobody did.

        An import edge (`test_claude_agents.py` reaching `tax_law_fetcher.py`)
        and a glob inside `skill_text()` stay invisible here, which is why that
        list still exists alongside this.
        """
        # TWO spellings, because the modules use two. `REPO / "a" / "b"` is the
        # chain; `Path(REPO_ROOT, "a", "b")` is what test_secure_fs writes
        # throughout, and matching only the first recovered ZERO paths from that
        # module — so it sat in this loop looking guarded while every path it
        # reads was invisible here, covered only by the hand-written table above
        # and by nothing at all for a read it grows later.
        chain = re.compile(r'REPO((?:\s*/\s*"[^"]+")+)'
                           r'|Path\(\s*REPO_ROOT\s*,((?:\s*"[^"]+"\s*,?)+)\)')
        reads: dict[str, set[str]] = {}
        for module in ALL_MODULES:
            for rel in _path_constants((REPO / module).read_text()):
                reads.setdefault(rel, set()).add(module)
            for match in chain.finditer(
                    _code_only((REPO / module).read_text())):
                rel = "/".join(re.findall(r'"([^"]+)"',
                                          match.group(1) or match.group(2)))
                # A literal naming a path that does NOT exist is an assertion
                # about its absence — `agents/skills`, the pre-PR#66 flat
                # location. Nothing can change under it, so nothing watches it.
                if (REPO / rel).exists():
                    reads.setdefault(rel, set()).add(module)
        # Per MODULE, not just in total: a spelling this locator cannot read
        # makes a module look covered while contributing nothing, which is
        # exactly how test_secure_fs sat here recovering zero paths.
        for module in (PORTABILITY, PREFLIGHT, SECUREFS):
            assert any(module in readers for readers in reads.values()), (
                f"the locator recovers no path literals from {module} — it is "
                f"in this loop contributing nothing, and a read it grows later "
                f"reaches CI with no local signal")
        assert len(reads) >= 15, (
            f"the locator stopped finding path literals: {sorted(reads)}")

        _fake_venv(quiescent_workspace, exit_code=1, lines=["$*"])
        unwatched = []
        for rel, readers in sorted(reads.items()):
            # Per iteration, for the reason the two loops above carry the same
            # line: the shim exits 1, so each run records its modules as red and
            # the next unions them in. `.agents/skills` sorts first and records
            # four of the six, after which a path whose arm was deleted outright
            # still shows them and passes. This loop was the third instance of
            # that vacuity and the one left standing when the other two were
            # fixed — which is itself the argument for fixing all three at once.
            _clear_red_carry_forward(quiescent_workspace)
            selected = _selected_modules(
                _run_skill_hook(quiescent_workspace,
                                str(quiescent_workspace / rel)))
            unwatched += [f"{rel} -> {m}" for m in sorted(readers)
                          if not _selects(m, selected)]
        assert not unwatched, (
            f"these modules read a path that does not select them: {unwatched}. "
            "Add the path to WATCHED_ROOTS and to select_modules_for — both, "
            "since one without the other is half-covered")

    def test_an_unrunnable_check_is_named_as_such(self, tmp_path):
        """pytest exits 1 for "ran and failed" and 2-5 for "no verdict at all":
        a collection error, an internal error, a renamed module (usage error), or
        nothing collected. Reporting a usage error as "invariants FAILED" sends
        the agent hunting a skill bug that does not exist, which is how a gate
        loses its credibility while firing correctly."""
        skill = str(tmp_path / ".agents/skills/tax-setup/SKILL.md")

        _fake_venv(tmp_path, exit_code=4, lines=["file or directory not found"])
        unrunnable = _run_skill_hook(tmp_path, skill)
        assert unrunnable.returncode == 2
        assert "could not RUN" in unrunnable.stderr
        assert "invariants FAILED" not in unrunnable.stderr
        assert "pytest exit 4" in unrunnable.stderr

        _fake_venv(tmp_path, exit_code=1, lines=["1 failed"])
        failed = _run_skill_hook(tmp_path, skill)
        assert failed.returncode == 2
        assert "Skill invariants FAILED" in failed.stderr

    def test_a_symlinked_dot_claude_is_refused_and_nothing_is_written_through_it(
            self, tmp_path):
        """The branch above the one below it, and the only one that can write
        OUTSIDE THE CLONE.

        The cascade opens `[ -L .claude ] || [ ! -d .claude ]`, and the two
        halves are not the same test: a symlink POINTING AT A DIRECTORY
        satisfies `-d`, so the `-L` is the whole of what catches it. The
        sibling test above covers a symlinked STAMP inside a real `.claude`;
        this covers the directory itself, which is the half with somewhere to
        write to. It had no test at all — the two symlinked-`.claude` tests in
        this file belong to the PERMISSIONS hook, and reading a name rather
        than a class makes this look covered.

        THIS PINS THE PROPERTY, NOT EITHER BRANCH, and the distinction is
        measured rather than assumed. The refusal is made twice: once here, and
        again inside `advance_stamp`, which re-validates immediately before its
        write because the pass between them is tens of seconds long and
        `.claude` can be replaced during it. Deleting the `-L` from EITHER site
        alone leaves this test green — the other still refuses — and deleting
        both turns it red. So what is asserted is the property both exist to
        hold, which is the right thing for a test to own: either check may be
        restructured, and neither may be dropped without the other noticing.
        """
        outside = tmp_path.parent / f"outside-{tmp_path.name}-dot-claude"
        outside.mkdir()
        (tmp_path / ".claude").symlink_to(outside)
        (tmp_path / "AGENTS.md").write_text("# a watched root\n")
        _fake_venv(tmp_path, exit_code=1, lines=["$*"])

        r = _run_skill_hook_bash(tmp_path)

        assert r.returncode == 2, r.stderr
        assert _selected_modules(r) == [PORTABILITY, CAPABILITIES, AGENTS,
                                        HOOKS, PREFLIGHT, SECUREFS,
                                        HOSTGUARD], (
            "a `.claude` the hook will not write to must force the full pass")
        assert list(outside.iterdir()) == [], (
            "the hook wrote through a symlinked .claude, outside the clone")
        assert (tmp_path / ".claude").is_symlink(), (
            "the link was replaced rather than refused — the hook must never "
            "CREATE .claude either, which in a taxpayer workspace is "
            "infrastructure it has no business inventing")

        # The control, on the sibling test's pattern: the refusal has to COST
        # something, or every assertion above holds against a hook that never
        # writes a stamp at all and runs everything every time.
        (tmp_path / ".claude").unlink()
        (tmp_path / ".claude").mkdir()
        _fake_venv(tmp_path, exit_code=0, lines=["288 passed"])
        time.sleep(STAMP_OVERLAP_SECONDS)
        assert _run_skill_hook_bash(tmp_path).returncode == 0
        assert (tmp_path / SKILL_STAMP).is_file(), (
            "no stamp is ever written, so the refusal above costs nothing and "
            "this test is measuring a hook that has no stamp")
        assert _run_skill_hook_bash(tmp_path).returncode == 0

    def test_a_run_marker_that_cannot_be_created_forces_the_full_pass(
            self, tmp_path):
        """The third branch, and the one with no visible symptom.

        The marker is what the stamp's mtime is compared against, and it is
        made under `$TMPDIR`. If `mktemp -d` fails the hook holds no marker,
        and it must then treat the stamp as unusable — because without the
        marker it cannot tell a stamp written by this pass from one written
        during it, which is the whole reason the marker is backdated.

        UNTESTED UNTIL NOW, and invisible in the direction that matters: with
        this branch removed the hook would go on passing every other test in
        this file, because `$TMPDIR` is writable on every machine that runs
        them. The failure only appears on a host where it is not.
        """
        (tmp_path / ".claude").mkdir()
        (tmp_path / "AGENTS.md").write_text("# a watched root\n")
        _fake_venv(tmp_path, exit_code=1, lines=["$*"])

        r = _run_skill_hook_bash(
            tmp_path, env={"TMPDIR": str(tmp_path / "no-such-tmpdir")})

        assert r.returncode == 2, r.stderr
        assert _selected_modules(r) == [PORTABILITY, CAPABILITIES, AGENTS,
                                        HOOKS, PREFLIGHT, SECUREFS,
                                        HOSTGUARD], (
            "without a run marker the stamp cannot be trusted, so every "
            "module has to run")
        assert not (tmp_path / SKILL_STAMP).exists(), (
            "a pass that could not make its marker must not advance the "
            "change boundary — an edit landing during it would then be older "
            "than the boundary and never checked again")

        # The control: the same workspace with a usable TMPDIR writes a stamp.
        _fake_venv(tmp_path, exit_code=0, lines=["288 passed"])
        time.sleep(STAMP_OVERLAP_SECONDS)
        assert _run_skill_hook_bash(tmp_path).returncode == 0
        assert (tmp_path / SKILL_STAMP).is_file(), (
            "the stamp is never written here regardless, so the refusal above "
            "costs nothing and this test measures nothing")

    def test_a_symlinked_skill_invariant_stamp_is_rejected_without_external_write(
            self, tmp_path):
        """Stamp maintenance obeys the same workspace boundary as every other
        write here: the hook must never follow a link out of the clone. Refusing
        costs one thing only — the next call repeats the pass.

        Two things make this discriminating rather than decorative, and
        neither is the sentinel. The pass still RUNS — refusing to WRITE the
        boundary is not the same as refusing to check — and the control at the
        end shows what the refusal actually costs. Without that control every
        assertion here holds against a hook with no stamp at all: it would run
        the full pass every time, leave the link alone because it never writes,
        and look identical from outside.
        """
        (tmp_path / ".claude").mkdir()
        (tmp_path / "AGENTS.md").write_text("# a watched root\n")
        outside = tmp_path.parent / f"outside-{tmp_path.name}-skill-stamp"
        outside.write_text("external sentinel")
        stamp = tmp_path / SKILL_STAMP
        stamp.symlink_to(outside)
        _fake_venv(tmp_path, exit_code=1, lines=["$*"])

        r = _run_skill_hook_bash(tmp_path)

        assert r.returncode == 2, r.stderr
        assert _selected_modules(r) == [PORTABILITY, CAPABILITIES, AGENTS,
                                        HOOKS, PREFLIGHT, SECUREFS,
                                        HOSTGUARD], (
            "an unusable stamp must force the full pass, never skip it")
        assert outside.read_text() == "external sentinel", (
            "the hook wrote its stamp through a symlink outside the workspace")
        assert stamp.is_symlink(), "the link was replaced rather than refused"

        # The control. Same workspace, same payload, link removed: now a real
        # stamp is installed and the call after it is quiet. That contrast is
        # what makes the full pass above the PRICE of the refusal rather than
        # simply how every pass here behaves.
        stamp.unlink()
        _fake_venv(tmp_path, exit_code=0, lines=["288 passed"])
        time.sleep(STAMP_OVERLAP_SECONDS)
        assert _run_skill_hook_bash(tmp_path).returncode == 0
        assert stamp.is_file() and not stamp.is_symlink(), (
            "no boundary is ever written, so the refusal above costs nothing "
            "and this test is measuring a hook that has no stamp")
        quiet = _run_skill_hook_bash(tmp_path)
        assert quiet.returncode == 0 and _selected_modules(quiet) == []

    def test_the_stamp_boundary_precedes_writes_concurrent_with_the_pass(
            self, tmp_path):
        """The pass is a pytest run lasting tens of seconds, so a file written
        while it is going is routine, not exotic. The stamp therefore carries
        the pass START; an end-time stamp would make that write look older than
        the boundary.

        The exit code alone is not a dependable discriminator here, which is
        worth stating because it is the obvious assertion to reach for: an
        end-time stamp can also land ahead of the NEXT call's backdated marker,
        trip the future-stamp branch, and force a full pass — the right exit
        code for entirely the wrong reason. The module list separates the two
        cases outright. Seeing the concurrent write as a change selects the two
        modules that read AGENTS.md — portability for its length, preflight for
        the bare-interpreter sweep over DOCUMENTS — while distrusting the stamp
        selects all four. Verified by mutation, replacing the stamp's `touch -r`
        with `true`.
        """
        (tmp_path / ".claude").mkdir()
        late = tmp_path / "AGENTS.md"
        python = _fake_venv(tmp_path, exit_code=0)
        python.write_text(
            "#!/bin/sh\n"
            "/bin/sleep 2\n"
            f'printf "written mid-pass\\n" > "{late}"\n'
            "exit 0\n")
        python.chmod(0o755)

        during = _run_skill_hook_bash(tmp_path)
        assert during.returncode == 0, during.stderr
        assert late.is_file()

        _fake_venv(tmp_path, exit_code=1, lines=["$*"])
        after = _run_skill_hook_bash(tmp_path)
        assert after.returncode == 2, (
            "a file written during the pass was swallowed by an end-time stamp")
        assert _selected_modules(after) == [PORTABILITY, PREFLIGHT], (
            "the concurrent write was not recognised as a change; the full "
            "selection here means the stamp was distrusted, not that the write "
            "was seen")

    @pytest.mark.parametrize("corruption", ["wrong-version", "legacy-v1",
                                            "future"])
    def test_a_future_or_wrong_version_stamp_forces_a_full_pass(self, tmp_path,
                                                                corruption):
        """Both branches exist so a stamp that cannot be trusted fails toward
        checking too much rather than skipping everything.

        A stamp from an older hook cannot be compared against a watched set that
        has since grown, and a stamp in the future — a clock correction, a
        restored backup — makes every subsequent write look old forever.

        `legacy-v1` is the case worth naming separately from an arbitrary bad
        version. Version 1 was the version line ALONE, so a reader that had
        merely extended the format in place would parse a v1 stamp as a
        well-formed v2 stamp whose red set is empty — a plausible answer, and
        wrong for precisely the check the v1 pass failed on. Bumping the
        version is what turns that into distrust, and this is the assertion
        that the bump happened rather than the format being widened quietly.
        """
        (tmp_path / ".claude").mkdir()
        (tmp_path / "AGENTS.md").write_text("# a watched root\n")
        _fake_venv(tmp_path, exit_code=1, lines=["$*"])
        time.sleep(STAMP_OVERLAP_SECONDS)
        assert _run_skill_hook_bash(tmp_path).returncode == 2
        stamp = tmp_path / SKILL_STAMP
        original = stamp.stat().st_mtime
        assert _selected_modules(_run_skill_hook_bash(tmp_path)) == [], (
            "the tree was not quiescent, so a full pass below proves nothing")

        if corruption in ("wrong-version", "legacy-v1"):
            stamp.write_text("1\n" if corruption == "legacy-v1" else "9\n")
            # Restore the timestamp the pass gave it, or the rewrite itself
            # would trip the future-stamp branch and the version check would
            # never be reached.
            os.utime(stamp, (original, original))
        else:
            ahead = original + 86_400
            os.utime(stamp, (ahead, ahead))

        r = _run_skill_hook_bash(tmp_path)
        assert r.returncode == 2
        assert _selected_modules(r) == [PORTABILITY, CAPABILITIES, AGENTS,
                                        HOOKS, PREFLIGHT, SECUREFS,
                                        HOSTGUARD], (
            "an untrustworthy stamp must force every module, not silently skip "
            "every change")

    @pytest.mark.parametrize("exit_code", [2, 3, 4, 5])
    def test_a_no_verdict_pass_does_not_advance_the_stamp(self, tmp_path,
                                                          exit_code):
        """pytest exits 1 for "ran and failed" and 2-5 for "no verdict at all",
        and only the first of those measured anything.

        Advancing the boundary on a no-verdict pass is the worst combination
        available: the hook reports that the checks themselves are broken, the
        agent repairs them, and the edit that sat on disk throughout the outage
        is now OLDER than the boundary and is never examined again. The loss is
        silent, permanent, and invisible to every other assertion here, because
        every one of them either changes a file after the pass or never has a
        pass fail to produce a verdict at all.

        Parametrized across the whole no-verdict range rather than one case: 2
        (a collection error) is the common one and 4 (a renamed module) is what
        the usage-error test already fires, and a guard written for one status
        is one `elif` away from covering only that status.

        The mirror at the end is the half that states the cost. Holding the
        boundary still is only worth anything if the edit it protects is
        actually re-examined once the checks run again.
        """
        (tmp_path / ".claude").mkdir()
        watched = tmp_path / "AGENTS.md"
        watched.write_text("# a watched root\n")
        _fake_venv(tmp_path, exit_code=0, lines=["288 passed"])
        time.sleep(STAMP_OVERLAP_SECONDS)

        assert _run_skill_hook_bash(tmp_path).returncode == 0
        stamp = tmp_path / SKILL_STAMP
        assert stamp.is_file(), "no boundary was established to hold still"
        boundary = stamp.stat().st_mtime

        # Aged on both sides of the write, so the repaired pass at the end
        # cannot see this edit merely because it shares a coarse filesystem
        # tick with the stamp the outage pass would have written.
        time.sleep(STAMP_OVERLAP_SECONDS)
        watched.write_text("# edited while the checks were unrunnable\n")
        time.sleep(STAMP_OVERLAP_SECONDS)

        _fake_venv(tmp_path, exit_code=exit_code, lines=["no tests ran"])
        outage = _run_skill_hook_bash(tmp_path)
        assert outage.returncode == 2
        assert "could not RUN" in outage.stderr
        assert stamp.stat().st_mtime == boundary, (
            "a pass that measured nothing moved the change boundary past an "
            "edit it never examined")

        _fake_venv(tmp_path, exit_code=1, lines=["$*"])
        repaired = _run_skill_hook_bash(tmp_path)
        assert repaired.returncode == 2, (
            "the edit made during the outage is invisible now that the checks "
            "run again — which is the entire cost of advancing the stamp there")
        assert PORTABILITY in _selected_modules(repaired)

    def test_the_check_name_lists_agree(self):
        """The check names are enumerated in SIX places, and the two that were
        left out of the first version of this test fail silently forever.

        `apply_red_checks` turns a stamp token back into a flag,
        `remember_red_checks` turns a flag back into a token, `read_red_checks`
        decides which tokens are not junk, `run_focused_modules` maps a flag to
        the module pytest actually runs, `select_every_module` forces them all,
        and `nothing_selected` decides whether anything runs at all.

        Comparing only the first three — which is how this shipped — misses the
        two that cannot announce themselves. Omit a check from
        `run_focused_modules` and its flag is set by every trigger, written to
        the stamp on every red verdict, restored on every later pass, and never
        handed to pytest: it can never go green, so it is carried red forever
        while its assertions never run. Omit it from `nothing_selected` and the
        flag is set, the fast path still exits 0, and a Bash call never runs it.
        """
        import re as _re
        script = (HOOKS_DIR / "skill-invariants.sh").read_text()

        def flags(pattern):
            return {m.upper() for m in _re.findall(pattern, script, _re.M)}

        initialised = flags(r"^RUN_(\w+)=0$")
        applied = flags(r"^\s*\w+\) RUN_(\w+)=1 ;;$")
        remembered = flags(r'\[ "\$RUN_(\w+)" -eq 1 \] && NEW_RED=')
        forced = flags(r"^    RUN_(\w+)=1$")
        nothing = flags(r'\[ "\$RUN_(\w+)" -eq 0 \]')
        runs = flags(r'\[ "\$RUN_(\w+)" -eq 1 \] \\')
        reader = _re.search(r'case "\$line" in\n\s*([\w|]+)\)', script)
        assert reader, "read_red_checks' token list not found — has it moved?"
        recognised = {t.upper() for t in reader.group(1).split("|")}

        lists = {"initialised": initialised, "apply": applied,
                 "remember": remembered, "read": recognised,
                 "select_every": forced, "nothing_selected": nothing,
                 "run_focused": runs}
        assert len(set(map(frozenset, lists.values()))) == 1, (
            "the check-name lists have drifted: "
            + "; ".join(f"{k}={sorted(v)}" for k, v in sorted(lists.items())))
        assert "PREFLIGHT" in applied, "the new module is not wired at all"

        # SETS ARE NOT ENOUGH: the two directions have to agree PAIRWISE. The
        # `applied` regex above discards the stamp token and keeps only the flag,
        # so swapping two pairings — `preflight) RUN_SECUREFS=1` next to
        # `securefs) RUN_PREFLIGHT=1` — leaves all seven sets identical and this
        # test green, while a red `test_preflight.py` is carried forward under
        # the other module's name and re-selects the wrong one on the next pass.
        # The still-red check then never runs again and reports clean, which is
        # the exact failure the docstring above says this test exists to catch.
        pairs_applied = {token.upper(): flag.upper() for token, flag in
                         _re.findall(r"^\s*(\w+)\) RUN_(\w+)=1 ;;$", script,
                                     _re.M)}
        pairs_remembered = {token.upper(): flag.upper() for flag, token in
                            _re.findall(
                                r'\[ "\$RUN_(\w+)" -eq 1 \] && '
                                r'NEW_RED="\$\{NEW_RED\}(\w+)"', script)}
        assert pairs_applied, "apply_red_checks' pairings no longer parse"
        assert pairs_remembered, "remember_red_checks' pairings no longer parse"
        assert pairs_applied == pairs_remembered, (
            f"a stamp token and its flag are paired differently in the two "
            f"directions, so a red check is carried forward under another "
            f"module's name: apply={pairs_applied} remember={pairs_remembered}")
        for token, flag in pairs_applied.items():
            assert token == flag, (
                f"the stamp token {token!r} sets RUN_{flag} — they are the same "
                f"check and must share a name, or the mapping needs a third "
                f"place to be kept in step")

    def test_a_red_check_is_recarried_until_it_goes_green(self, tmp_path):
        """The two stamp rules that only make sense together.

        The boundary advances on a RED verdict on purpose: holding it back
        re-reports the same failure on every following tool call, landing on
        the `git diff` and `pytest` calls needed to diagnose it, and a gate that
        cries wolf gets reasoned past. What that costs — unpaid until the red
        set was carried forward — is coverage the run-everything-every-time
        behaviour had: an agent-definition edit a minute later selects the
        agents module alone, passes, and exits 0 in silence while the
        portability invariant is still failing on disk.

        The quiescent call in the middle is not a spare assertion. It is the
        one that shows the union did not buy the coverage back by reinstating
        the cry-wolf half, and it is the reason the union is applied only after
        the nothing-changed fast path has already exited.
        """
        (tmp_path / ".claude").mkdir()
        skill = tmp_path / ".agents" / "skills" / "tax-setup" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: tax-setup\n---\n")
        agent = tmp_path / ".claude" / "agents" / "bias-direction-reviewer.md"
        agent.parent.mkdir(parents=True)
        agent.write_text("# reviewer\n")
        _fake_venv(tmp_path, exit_code=0, lines=["288 passed"])
        time.sleep(STAMP_OVERLAP_SECONDS)

        # A workspace with no stamp yet has no boundary to compare against and
        # every pass is a full pass, so the selections below would all be all
        # four modules and would say nothing. Bootstrap a green boundary first.
        assert _run_skill_hook_bash(tmp_path).returncode == 0
        assert _stamp_body(tmp_path) == [SKILL_STAMP_VERSION]

        _fake_venv(tmp_path, exit_code=1, lines=["$*"])
        red = _run_skill_hook(tmp_path, str(skill))
        assert red.returncode == 2
        assert _selected_modules(red) == [PORTABILITY, CAPABILITIES, AGENTS,
                                          PREFLIGHT, SECUREFS]
        assert _stamp_body(tmp_path) == [SKILL_STAMP_VERSION, "portability",
                                         "capabilities", "agents", "preflight",
                                         "securefs"], (
            "the failing pass recorded nothing, so nothing downstream can know "
            "a check is still red")

        quiet = _run_skill_hook_bash(tmp_path)
        assert quiet.returncode == 0 and _selected_modules(quiet) == [], (
            "a red check re-ran on a tool call that changed nothing — the "
            "union must come AFTER the nothing-changed fast path, or the "
            "carry-forward restores the gate that fires on every call")

        time.sleep(STAMP_OVERLAP_SECONDS)
        agent.write_text("# reviewer, edited\n")
        union = _run_skill_hook(tmp_path, str(agent))
        assert union.returncode == 2
        assert _selected_modules(union) == [PORTABILITY, CAPABILITIES, AGENTS,
                                            PREFLIGHT, SECUREFS], (
            "an unrelated watched change ran the agents module alone and "
            "reported success, with two checks still red on disk")

        _fake_venv(tmp_path, exit_code=0, lines=["288 passed"])
        time.sleep(STAMP_OVERLAP_SECONDS)
        agent.write_text("# reviewer, repaired\n")
        green = _run_skill_hook(tmp_path, str(agent))
        assert green.returncode == 0
        assert _stamp_body(tmp_path) == [SKILL_STAMP_VERSION], (
            "the green pass covered every carried check and must clear them; a "
            "carry-forward that only grows is a permanent tax on every edit")

    def test_an_unknown_red_token_never_reaches_the_pytest_argv(self, tmp_path):
        """The stamp records a CHECK NAME, never a module path, and this is the
        assertion that pins the difference.

        Storing the path is the shorter design and it wedges. The hook would
        hand pytest a name it wrote days ago; a since-renamed module answers
        with exit 4, a usage error, which is a pass with NO VERDICT; and a
        no-verdict pass no longer advances the stamp, so nothing ever clears the
        entry that caused it. Recognising four literal tokens and dropping
        everything else caps the damage a mangled stamp can do at one extra
        module on one pass.
        """
        (tmp_path / ".claude").mkdir()
        (tmp_path / "AGENTS.md").write_text("# a watched root\n")
        agent = tmp_path / ".claude" / "agents" / "bias-direction-reviewer.md"
        agent.parent.mkdir(parents=True)
        agent.write_text("# reviewer\n")
        _fake_venv(tmp_path, exit_code=1, lines=["$*"])
        time.sleep(STAMP_OVERLAP_SECONDS)

        assert _run_skill_hook_bash(tmp_path).returncode == 2
        stamp = tmp_path / SKILL_STAMP
        original = stamp.stat().st_mtime
        stamp.write_text(f"{SKILL_STAMP_VERSION}\nportability\n"
                         "tests/test_renamed_away.py\nnot-a-check\n")
        # Restore the timestamp the pass gave it: the rewrite would otherwise
        # read as a stamp from the future and force all four modules, which
        # satisfies the assertion below for a reason unrelated to the filter.
        os.utime(stamp, (original, original))

        r = _run_skill_hook(tmp_path, str(agent))

        assert r.returncode == 2
        assert _selected_modules(r) == [PORTABILITY, AGENTS], (
            "the recognised token must still be carried and the rest dropped")
        for junk in ("tests/test_renamed_away.py", "not-a-check"):
            assert junk not in r.stderr, (
                f"{junk} reached pytest's argv straight out of a stamp on disk")

    def test_the_skill_hook_cleans_up_after_a_signal(self, tmp_path):
        """The stamp candidate is `mktemp`'d INSIDE `.claude/`, because it has
        to land on the same filesystem as the file it atomically replaces —
        which is exactly what puts it beyond the scratch directory's removal.

        The registered hook timeout TERMINATES a slow pass, and for a pytest
        run lasting tens of seconds that is the routine outcome rather than the
        exotic one. The `touch` shim holds the pass open at the one moment a
        candidate exists and has not yet been renamed; the real window is
        microseconds wide, so without the shim this would go green against a
        hook that cleaned up nothing.

        Exit 1 is asserted alongside, and the two wrong answers are the
        instructive part: 0 would claim a clean check the pass never made, and
        2 would block a tool call over a pass that produced no verdict at all.
        """
        (tmp_path / ".claude").mkdir()
        (tmp_path / "AGENTS.md").write_text("# a watched root\n")
        _fake_venv(tmp_path, exit_code=0, lines=["288 passed"])
        scratch_root = tmp_path / "scratch"
        scratch_root.mkdir()

        shim = tmp_path / "bin"
        shim.mkdir()
        (shim / "touch").write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *.claude/.skill-invariants-stamp.*) /bin/sleep 3 ;;\n"
            "esac\n"
            f"exec {shutil.which('touch')} \"$@\"\n")
        (shim / "touch").chmod(0o755)

        process = subprocess.Popen(
            ["bash", str(SKILL_HOOK)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "TMPDIR": str(scratch_root),
                 "PATH": f"{shim}:{os.environ['PATH']}"})
        process.stdin.write(json.dumps({"tool_name": "Bash",
                                        "tool_input": {"command": "ls"}}))
        process.stdin.close()
        # communicate() below re-flushes self.stdin, and flushing a stream this
        # test has already closed raises ValueError up to and including 3.13 —
        # 3.14 is where `except ValueError` was added beside the BrokenPipeError
        # arm. Checked against CPython's own source at v3.13.9, which catches
        # BrokenPipeError alone, and against the 3.14 running here, which
        # catches both.
        #
        # 3.13 IS THE FLOOR, so the raising half is the floor leg of CI, not
        # history. An earlier version of this comment put the split at 3.12/3.13
        # and called this "a defence with no current attacker" — it has one, on
        # the interpreter this project's lower leg runs. Dropping the reference
        # is what keeps the test from failing there for a reason unrelated to
        # what it asserts.
        process.stdin = None

        candidates = []
        deadline = time.time() + 20
        while time.time() < deadline and not candidates:
            candidates = list((tmp_path / ".claude")
                              .glob(".skill-invariants-stamp.??????"))
            time.sleep(0.05)
        assert candidates, "no in-repo candidate ever existed to be cleaned up"
        assert list(scratch_root.glob("skill-invariants.*")), (
            "no scratch directory either, so neither half of the trap is "
            "being measured"
        )
        # A trapped signal is deferred until the foreground command returns, so
        # the assertions wait for the process to be REAPED rather than firing
        # straight after the kill.
        process.terminate()
        process.communicate(timeout=30)

        assert process.returncode == 1, (
            "a killed pass must report neither 0, a clean check it never made, "
            "nor 2, a block over a verdict it never reached")
        assert not list((tmp_path / ".claude")
                        .glob(".skill-invariants-stamp.??????")), (
            "a killed pass left a stamp candidate in .claude/ — untracked, "
            "unignorable by exact name, one `git add .` from a commit")
        assert not list(scratch_root.glob("skill-invariants.*"))
        assert not (tmp_path / SKILL_STAMP).exists(), (
            "the boundary advanced on a pass that was killed before it could "
            "write one")

        # The complement, and the one that fails if the cleanup is ever widened
        # into a sweep of `.claude/`: a pass allowed to finish installs its
        # stamp and leaves no sibling behind. `mv` consumes the candidate name,
        # so the trap can only ever reach a pass that died before its rename.
        normal = _run_skill_hook_bash(tmp_path)
        assert normal.returncode == 0, normal.stderr
        assert (tmp_path / SKILL_STAMP).is_file(), "the trap ate the new stamp"
        assert not list((tmp_path / ".claude")
                        .glob(".skill-invariants-stamp.??????"))

    def test_the_hook_creates_nothing_in_a_workspace_without_dot_claude(
            self, tmp_path):
        """`.claude/` is infrastructure this hook has no business inventing in a
        taxpayer workspace. Without it there is no stamp and every pass is a
        full pass — correct, merely slower, which is the safe direction."""
        (tmp_path / "AGENTS.md").write_text("# a watched root\n")
        _fake_venv(tmp_path, exit_code=0, lines=["288 passed"])
        before = {p.name for p in tmp_path.iterdir()}

        assert _run_skill_hook_bash(tmp_path).returncode == 0
        assert {p.name for p in tmp_path.iterdir()} == before
        assert not list(tmp_path.rglob(".skill-invariants-stamp*"))


class TestRuntimePermissionsHook:
    """AGENTS.md §1 requires chmod 600 on every file holding PII or financial
    data. Today that rule is carried by skill prose, and a step that forgets it
    leaves a return world-readable with no signal anywhere."""

    # An opaque command: the payload names no path, so nothing in it bounds
    # what the call may have written beneath archive/. This is the dominant
    # branch — nearly every command a session runs lands here.
    OPAQUE_BASH = {"tool_name": "Bash", "tool_input": {"command": "bash helper.sh"}}

    def _run(self, workspace: Path, payload: dict | None = None, indent=None,
             env_extra: dict | None = None):
        """`indent` writes the payload pretty-printed — i.e. across several
        lines, which is what a `read`-based payload parser truncates."""
        return subprocess.run(
            ["bash", str(PERMS_HOOK)],
            input=(json.dumps(payload, indent=indent)
                   if payload is not None else None),
            text=True, capture_output=True,
            env=dict(os.environ, CLAUDE_PROJECT_DIR=str(workspace),
                     **(env_extra or {})))

    def test_runtime_files_are_forced_to_owner_only(self, tmp_path):
        names = ("taxpayer_profile.json", "result.json", "answers.md",
                 "parse_ledger.json", "holdings.json", "projection_inputs.json",
                 "crypto_transactions.json", "FBAR_2025.md",
                 "2025-review.md", "tax_calendar_2025.ics",
                 "tax_summary_2025.pdf")
        for name in names:
            f = tmp_path / name
            f.write_text("x")
            f.chmod(0o644)
        assert self._run(tmp_path).returncode == 0
        # Scoped to the files this test created, not the whole directory: the
        # hook legitimately touches other paths, and a whole-directory sweep
        # fails on those for reasons that have nothing to do with the rule.
        leaked = [n for n in names if _mode(tmp_path / n) != 0o600]
        assert not leaked, f"still group/world readable: {leaked}"

    def test_dot_prefixed_pdfs_are_forced_to_owner_only(self, tmp_path):
        """Shell `*.pdf` does not include dot-prefixed artifacts by default.

        Draft generators and imports can use hidden files before an atomic
        rename, and the permissions contract applies during that interval too.
        """
        hidden = tmp_path / ".draft-return.pdf"
        hidden.write_text("financial data")
        hidden.chmod(0o644)

        assert self._run(tmp_path).returncode == 0
        assert _mode(hidden) == 0o600

    def test_nested_generated_pdfs_are_forced_to_owner_only(self, tmp_path):
        """Opaque generators may place a return outside named runtime trees."""
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        generated = tmp_path / "exports" / "Tax_Return_2026.PDF"
        generated.parent.mkdir()
        generated.write_text("financial data")
        generated.chmod(0o644)

        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "python generate_report.py"},
        }
        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(generated) == 0o600

    def test_all_root_pdf_basename_shapes_are_forced_to_owner_only(self,
                                                                   tmp_path):
        """Dot-only stems and mixed-case extensions are valid PDF basenames."""
        names = ("..pdf", "...pdf", "return.PDF", ".draft-return.PdF")
        for name in names:
            artifact = tmp_path / name
            artifact.write_text("financial data")
            artifact.chmod(0o644)

        assert self._run(tmp_path).returncode == 0
        assert all(_mode(tmp_path / name) == 0o600 for name in names)

    def test_root_backup_and_swap_artifacts_are_forced_to_owner_only(self,
                                                                      tmp_path):
        """Gitignored partial-copy shapes can contain complete financial data."""
        names = ("result.json.bak", ".result.json.tmp", "result.json.swp",
                 "result.json.swo", "result.json~")
        for name in names:
            artifact = tmp_path / name
            artifact.write_text("financial data")
            artifact.chmod(0o644)

        assert self._run(tmp_path).returncode == 0
        assert all(_mode(tmp_path / name) == 0o600 for name in names)

    def test_nested_backup_and_swap_artifacts_are_forced_to_owner_only(
            self, tmp_path):
        """A nested PDF backup can contain the same complete financial data."""
        exports = tmp_path / "exports"
        exports.mkdir()
        names = ("return.pdf.bak", ".return.pdf.tmp", "return.pdf.swp",
                 "return.pdf.swo", "return.pdf~")
        for name in names:
            artifact = exports / name
            artifact.write_text("financial data")
            artifact.chmod(0o644)

        assert self._run(tmp_path).returncode == 0
        assert all(_mode(exports / name) == 0o600 for name in names)

    def test_option_like_pdf_name_is_not_parsed_as_a_chmod_flag(self, tmp_path):
        """Prefix root basenames so chmod never receives a leading option."""
        artifact = tmp_path / "-return.pdf"
        artifact.write_text("financial data")
        artifact.chmod(0o644)
        shim = tmp_path / "bin"
        shim.mkdir()
        (shim / "chmod").write_text(
            "#!/bin/sh\n"
            "case \"$2\" in -*) exit 64 ;; esac\n"
            "exec /bin/chmod \"$@\"\n"
        )
        (shim / "chmod").chmod(0o755)

        r = subprocess.run(
            ["bash", str(PERMS_HOOK)], text=True, capture_output=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "PATH": f"{shim}:{os.environ['PATH']}"})

        assert r.returncode == 0, r.stderr
        assert _mode(artifact) == 0o600

    @pytest.mark.parametrize("kind", ["root", "nested"])
    def test_runtime_symlinks_are_rejected_without_chmodding_external_targets(
            self, tmp_path, kind):
        """`chmod` follows symlinks, including symlinked parent directories.

        A runtime tree must never use the hook as a confused deputy to chmod a
        path outside the workspace. Rejection is loud and leaves the external
        target untouched rather than pretending the permission pass succeeded.
        """
        outside = tmp_path.parent / f"outside-{tmp_path.name}-{kind}"
        outside.mkdir()
        external = outside / "financial.pdf"
        external.write_text("financial data")
        external.chmod(0o644)

        if kind == "root":
            (tmp_path / "documents").symlink_to(outside, target_is_directory=True)
        else:
            documents = tmp_path / "documents"
            documents.mkdir()
            (documents / "linked.pdf").symlink_to(external)

        r = self._run(tmp_path)

        assert r.returncode == 1
        assert "refusing to follow a symlink" in r.stderr
        assert str(outside) not in r.stderr
        assert _mode(external) == 0o644, (
            "the hook followed a runtime symlink and chmodded an external file")

    def test_an_old_stamp_cannot_hide_a_preexisting_runtime_symlink(self, tmp_path):
        """The stamp format changes when a new full-pass invariant ships.

        Otherwise a symlink left by the older hook can predate its stamp and
        remain invisible to the incremental predicate forever.
        """
        outside = tmp_path.parent / f"outside-{tmp_path.name}-old-stamp"
        outside.mkdir()
        external = outside / "financial.pdf"
        external.write_text("financial data")
        external.chmod(0o644)
        documents = tmp_path / "documents"
        documents.mkdir()
        (documents / "linked.pdf").symlink_to(external)
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        stamp = dot_claude / ".perms-stamp"
        stamp.write_text("")  # format written by the previous hook version

        r = self._run(tmp_path)

        assert r.returncode == 1
        assert "refusing to follow a symlink" in r.stderr
        assert stamp.read_text() == "", (
            "a rejected full pass must not upgrade/advance the old stamp")
        assert _mode(external) == 0o644

    @needs_jq
    def test_write_payload_rejects_a_file_reached_through_a_symlinked_parent(
            self, tmp_path):
        """Payload hardening bypasses `find`, so it needs the same path guard."""
        outside = tmp_path.parent / f"outside-{tmp_path.name}-payload"
        outside.mkdir()
        external = outside / "review.md"
        external.write_text("financial data")
        external.chmod(0o644)
        archive = tmp_path / "archive" / "2024"
        archive.mkdir(parents=True)
        (archive / "reviews").symlink_to(outside, target_is_directory=True)

        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(archive / "reviews" / "review.md"),
            },
        }
        r = self._run(tmp_path, payload)

        assert r.returncode == 1
        assert "refusing to follow a symlink" in r.stderr
        assert _mode(external) == 0o644

    def test_the_protected_list_covers_the_pii_scans_sensitive_filenames(self):
        """The list in this hook is the FOURTH copy of "which runtime files hold
        taxpayer data" — the PII scan, `.gitignore` and the installer's ignore
        patterns are the others — and AGENTS.md §1 is explicit that hand-mirrored
        rules are how four bypasses once shipped to two places at once.

        It arrived already drifted: `crypto_transactions.json` (a full
        transaction history) and `projection_inputs.json` (YTD income and
        withholding) were missing. Both are gitignored, so the commit-time scan
        never sees them — which is the precise gap this hook exists to close.

        Parsing the scan's own `case` is what turns a mirror into a checked
        mirror. Merging the lists outright is not available: one is a shell
        `case` inside a security gate this repository has reviewed six times.
        """
        scan = (REPO / "scripts" / "pre-commit-pii-scan.sh").read_text()
        # Anchored on the line the case actually GUARDS, and forbidden from
        # spanning lines. The unanchored form took the first `*.json|*` pattern
        # list in the file with a `[^)]*` tail that ran across newlines, so any
        # `case` added earlier in the scan whose pattern happened to contain
        # `.json|` silently hijacked the parse — after which this test reported
        # the permissions hook as failing to protect patterns that are not
        # filenames at all. A near miss, not a hypothetical: the `.claude/`
        # runtime-state rule was added directly above this one.
        m = re.search(
            r"^\s*([^\s()|]+\.json\|[^)\n]*)\)\s*\n\s*BLOCKED\+=\(\"\s*"
            r"BLOCKED FILENAME", scan, re.M)
        assert m, "the sensitive-filename case in the PII scan has moved"
        canonical = {n.strip() for n in m.group(1).split("|")}
        assert len(canonical) >= 5, f"parsed too few names: {canonical}"

        hook_exact, hook_globs = _perms_hook_protected_names()

        # BOTH directions. The subset form that stood here could only ever see
        # half of this defect class, and the half it could not see is the half
        # that was live: `generate_tax_pdf.py` was in the hook and NOT in the
        # scan, so the hook chmodded it 600 while `git add -f
        # generate_tax_pdf.py` committed a file carrying the taxpayer's NAME in
        # its section headers — the one runtime file the CONTENT scan
        # structurally cannot vet, since it matches SSN and credential shapes
        # and a name has neither. That is precisely why the filename rule has
        # to carry it.
        #
        # The two failures are not the same failure and the message says which.
        assert canonical == hook_exact, (
            "the PII scan's sensitive-filename case and the permissions hook's "
            "protected-file list have drifted.\n"
            f"  only in the scan (the hook leaves it world-readable): "
            f"{sorted(canonical - hook_exact)}\n"
            f"  only in the hook (the scan lets it COMMIT): "
            f"{sorted(hook_exact - canonical)}\n"
            "Both lists answer one question — which runtime files hold taxpayer "
            "data. Add the name to both, or, if it is genuinely public the way "
            "tax_law_*.json is, classify it in regex_mirrors below.")

        regex_case = re.search(r"grep -qE '\^\(([^']+)\)\$'", scan)
        assert regex_case, "the regex sensitive-filename case has moved"
        regex_mirrors = {
            r"tax_law_.*\.json": None,  # public rates, deliberately excluded
            r"FBAR_.*\.md": "FBAR_*.md",
            r"[0-9]{4}-review\.md": "[0-9][0-9][0-9][0-9]-review.md",
            r"tax_calendar_.*\.ics": "tax_calendar_*.ics",
        }
        regex_parts = set(regex_case.group(1).split("|"))
        assert regex_parts == set(regex_mirrors), (
            "the PII scan's regex filename classes changed; classify each new "
            "class as public or mirror it into the permissions hook")
        hook_text = PERMS_HOOK.read_text()
        missing_regex = [pattern for pattern, shell_glob in regex_mirrors.items()
                         if shell_glob and shell_glob not in hook_text]
        assert not missing_regex, (
            f"regex-sensitive filename classes lack permission globs: "
            f"{missing_regex}")

        # The same equality one level up, over filename CLASSES. `if g` filters
        # out the single documented one-sided entry, which is deliberate and is
        # the reason this compares values rather than keys: `tax_law_*.json` is
        # in the scan's regex case and NOT in the hook, because published rates
        # and brackets are public figures rather than taxpayer data. Every
        # other class must appear on both sides.
        expected_globs = {g for g in regex_mirrors.values() if g}
        assert hook_globs == expected_globs, (
            "the permissions hook and the PII scan disagree on which filename "
            "CLASSES are sensitive.\n"
            f"  only in the hook: {sorted(hook_globs - expected_globs)}\n"
            f"  only in the scan: {sorted(expected_globs - hook_globs)}")

    def test_the_generated_artifact_extension_set_is_declared_once(self):
        """Three consumers must read GENERATED_ARTIFACT_EXTENSIONS, not repeat it.

        The root-level glob loop, is_generated_artifact_name (the payload fast
        path), and harden_workspace_artifacts' find predicate each need their
        own loop over the set — the fast path runs before any find, and the
        root loop runs before .claude/ or a walk exist at all — but all three
        must be reading the SAME array rather than three independently spelled
        copies. A hardcoded literal reintroduced at any one of them is exactly
        the drift this array exists to make structurally impossible: the
        earlier design stated the set twice, one contributor spelled it a
        third time in the root-level loop, and nothing noticed.
        """
        text = PERMS_HOOK.read_text()

        decl = re.search(r"^GENERATED_ARTIFACT_EXTENSIONS=\(([^)]*)\)$",
                          text, re.M)
        assert decl, "GENERATED_ARTIFACT_EXTENSIONS has moved or been renamed"
        extensions = decl.group(1).split()
        assert len(extensions) >= 4, f"parsed too few extensions: {extensions}"
        assert len(set(extensions)) == len(extensions), (
            f"duplicate entries in GENERATED_ARTIFACT_EXTENSIONS: {extensions}")

        def function_body(name):
            body = re.search(rf"\n{name}\(\) \{{\n(.*?)\n\}}\n", text, re.S)
            assert body, f"{name} has moved"
            return body.group(1)

        fast_path_body = function_body("is_generated_artifact_name")
        assert "GENERATED_ARTIFACT_EXTENSIONS[@]" in fast_path_body, (
            "is_generated_artifact_name no longer loops over "
            "GENERATED_ARTIFACT_EXTENSIONS — it may have grown its own "
            "hardcoded extension list")
        assert "*~) return 0" in fast_path_body, (
            "is_generated_artifact_name dropped the *~ literal (it has no "
            "leading dot and can't live in the extensions array)")

        walk_body = function_body("harden_workspace_artifacts")
        assert "GENERATED_ARTIFACT_EXTENSIONS[@]" in walk_body, (
            "harden_workspace_artifacts no longer loops over "
            "GENERATED_ARTIFACT_EXTENSIONS — it may have grown its own "
            "hardcoded find predicate")
        assert "-o -name '*~'" in walk_body, (
            "harden_workspace_artifacts' find predicate dropped the *~ literal")

        root_loop = re.search(
            r"shopt -s nullglob dotglob nocaseglob\n(.*?)"
            r"\nshopt -u nocaseglob dotglob nullglob\n", text, re.S)
        assert root_loop, "the root-level artifact glob loop has moved"
        assert "GENERATED_ARTIFACT_EXTENSIONS[@]" in root_loop.group(1), (
            "the root-level glob loop no longer loops over "
            "GENERATED_ARTIFACT_EXTENSIONS — it may have grown its own "
            "hardcoded glob list")
        assert "'./*~'" in root_loop.group(1), (
            "the root-level glob loop dropped the *~ literal")

    def test_the_runtime_and_pruned_tree_names_are_declared_once(self):
        """RUNTIME_ARTIFACT_TREES/PRUNED_HOME_TREES back both the fast path
        and the find walk.

        Before this array existed, `.git`/`.venv` were named three separate
        times — the find walk's prune clause, a literal `case` arm in the
        fast path, and a second `case` arm doing the case-alias check — with
        no shared list and nothing to notice if one of the three drifted
        from the others. classify_edited_top_tree and
        harden_workspace_artifacts must both read these two arrays rather
        than growing their own copies of either list.
        """
        text = PERMS_HOOK.read_text()

        runtime_decl = re.search(
            r"^RUNTIME_ARTIFACT_TREES=\(([^)]*)\)$", text, re.M)
        assert runtime_decl, "RUNTIME_ARTIFACT_TREES has moved or been renamed"
        runtime_trees = runtime_decl.group(1).split()
        assert set(runtime_trees) == {
            "documents", "summaries", "reviews", "scenarios", "projections",
            "withholding", "archive", ".planning",
        }, f"RUNTIME_ARTIFACT_TREES no longer matches AGENTS.md's runtime " \
           f"tree list: {sorted(runtime_trees)}"

        pruned_decl = re.search(r"^PRUNED_HOME_TREES=\(([^)]*)\)$", text, re.M)
        assert pruned_decl, "PRUNED_HOME_TREES has moved or been renamed"
        assert set(pruned_decl.group(1).split()) == {".git", ".venv"}, (
            f"PRUNED_HOME_TREES no longer matches: {pruned_decl.group(1)}")

        def function_body(name):
            body = re.search(rf"\n{name}\(\) \{{\n(.*?)\n\}}\n", text, re.S)
            assert body, f"{name} has moved"
            return body.group(1)

        classify_body = function_body("classify_edited_top_tree")
        assert "RUNTIME_ARTIFACT_TREES[@]" in classify_body, (
            "classify_edited_top_tree no longer loops over "
            "RUNTIME_ARTIFACT_TREES — the payload fast path may have grown "
            "its own hardcoded tree list")
        assert "PRUNED_HOME_TREES[@]" in classify_body, (
            "classify_edited_top_tree no longer loops over "
            "PRUNED_HOME_TREES — the payload fast path may have grown its "
            "own hardcoded .git/.venv check")

        walk_body = function_body("harden_workspace_artifacts")
        assert "PRUNED_HOME_TREES[@]" in walk_body \
            and "RUNTIME_ARTIFACT_TREES[@]" in walk_body, (
            "harden_workspace_artifacts' prune predicate no longer loops "
            "over PRUNED_HOME_TREES/RUNTIME_ARTIFACT_TREES — it may have "
            "grown its own hardcoded -path list")

    def test_a_chmod_that_cannot_run_is_reported_rather_than_swallowed(self, tmp_path):
        """Never-block was right; total silence was not the only way to get it.

        A chmod that fails — another uid, a read-only mount, a path that cannot
        be stat'd — leaves a return world-readable, and swallowing the error
        reported that on no channel at all while CLAUDE.md told the reader the
        rule was being re-asserted. Exit 1 surfaces the message to the user and
        lets the tool call proceed; exit 2, the only code that would derail the
        session, is still never used.

        The message must not echo the filename: generated PDFs and FBAR files
        can carry taxpayer identity in their basenames. The shim is the only
        deterministic way to reproduce the failure, because a test process can
        always chmod a file it just created.
        """
        sensitive_name = "Example-Taxpayer-return.pdf"
        (tmp_path / sensitive_name).write_text("{}")
        shim = tmp_path / "bin"
        shim.mkdir()
        (shim / "chmod").write_text("#!/bin/sh\nexit 1\n")
        (shim / "chmod").chmod(0o755)

        r = subprocess.run(
            ["bash", str(PERMS_HOOK)], text=True, capture_output=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "PATH": f"{shim}:{os.environ['PATH']}"})
        assert r.returncode == 1, (
            "a failed chmod must surface; 0 hides it and 2 would derail the "
            "session over a permissions pass")
        assert "sensitive runtime file" in r.stderr
        assert sensitive_name not in r.stderr, (
            "the permissions hook must not disclose identity-bearing filenames")

    def test_the_incremental_walk_still_hardens_a_new_file(self, tmp_path):
        """The recursive walk is incremental because this hook runs after every
        tool call and `archive/` grows by a year of documents at each cutover.
        The optimisation is only safe if a file written AFTER the stamp is still
        caught — `-newercm` compares ctime, so a mode change counts too.
        """
        docs = tmp_path / "documents"
        docs.mkdir()
        (tmp_path / ".claude").mkdir()
        old = docs / "first.pdf"
        old.write_text("x")
        old.chmod(0o644)
        assert self._run(tmp_path).returncode == 0      # writes the stamp
        assert _mode(old) == 0o600
        assert (tmp_path / ".claude" / ".perms-stamp").is_file()

        later = docs / "second.pdf"
        later.write_text("x")
        later.chmod(0o644)
        self._run(tmp_path)
        assert _mode(later) == 0o600, (
            "a file written after the stamp was skipped — the incremental walk "
            "is silently protecting nothing new")

    def test_archive_incremental_pass_repairs_chmod_only_directory_change(
            self, tmp_path):
        """An indexed directory mode can change without changing its mtime."""
        archive = tmp_path / "archive"
        year = archive / "2025"
        reviews = year / "reviews"
        reviews.mkdir(parents=True)
        (reviews / "existing.md").write_text("financial data")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0

        # Put every directory outside the timestamp-overlap window, then make
        # only a chmod change. chmod advances ctime, not mtime, so the old
        # mtime-only shallow check skipped `reviews/` indefinitely.
        for directory in (archive, year, reviews):
            os.utime(directory, (1, 1))
        reviews.chmod(0o755)
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "ordinary-notes.md")},
        }

        result = self._run(tmp_path, payload)
        assert result.returncode == 0, result.stderr
        assert _mode(reviews) == 0o700, (
            "a chmod-only archive directory exposure escaped the shallow pass")

    def test_archive_incremental_pass_visits_only_changed_years(self, tmp_path):
        """A timestamp predicate does not make a recursive find bounded.

        `find archive -newercm STAMP` still stats every PDF from every prior
        year before filtering. The hook must inspect only the shallow year
        entries, then recurse into a year that actually changed.
        """
        old_year = tmp_path / "archive" / "2024"
        old_year.mkdir(parents=True)
        old_file = old_year / "old.pdf"
        old_file.write_text("x")
        old_file.chmod(0o644)
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0

        # Make the unchanged historical year unambiguously older even on a
        # filesystem whose timestamps have only one-second resolution. The
        # stamp keeps a one-second overlap so a new year in its original tick
        # is still strictly newer and cannot be skipped.
        os.utime(old_year, (1, 1))

        new_year = tmp_path / "archive" / "2025"
        new_year.mkdir()
        new_file = new_year / "new.pdf"
        new_file.write_text("x")
        new_file.chmod(0o644)

        shim = tmp_path / "bin"
        shim.mkdir()
        log = tmp_path / "chmod.log"
        (shim / "chmod").write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$PERMS_CHMOD_LOG\"\n"
            "exec /bin/chmod \"$@\"\n"
        )
        (shim / "chmod").chmod(0o755)
        r = subprocess.run(
            ["bash", str(PERMS_HOOK)], text=True, capture_output=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "PERMS_CHMOD_LOG": str(log),
                 "PATH": f"{shim}:{os.environ['PATH']}"})

        assert r.returncode == 0, r.stderr
        assert _mode(new_file) == 0o600
        calls = log.read_text()
        assert "archive/2025" in calls
        assert "archive/2024" not in calls, (
            "an unchanged historical year was recursively revisited")

    def test_full_archive_pass_batches_file_chmods(self, tmp_path):
        """A full pass must not launch one chmod process per archived file."""
        year = tmp_path / "archive" / "2025"
        year.mkdir(parents=True)
        for i in range(100):
            artifact = year / f"artifact-{i}.pdf"
            artifact.write_text("financial data")
            artifact.chmod(0o644)

        shim = tmp_path / "bin"
        shim.mkdir()
        log = tmp_path / "chmod.log"
        (shim / "chmod").write_text(
            "#!/bin/sh\n"
            "printf 'call\\n' >> \"$PERMS_CHMOD_LOG\"\n"
            "exec /bin/chmod \"$@\"\n"
        )
        (shim / "chmod").chmod(0o755)

        r = subprocess.run(
            ["bash", str(PERMS_HOOK)], text=True, capture_output=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "PERMS_CHMOD_LOG": str(log),
                 "PATH": f"{shim}:{os.environ['PATH']}"})

        assert r.returncode == 0, r.stderr
        assert len(log.read_text().splitlines()) < 20, (
            "the full walk launched a separate chmod for each archive file")
        assert all(_mode(p) == 0o600 for p in year.glob("*.pdf"))

    @needs_jq
    def test_write_payload_hardens_a_file_inside_an_existing_archive(self, tmp_path):
        """Shallow archive polling must not miss a direct edit in an old year.

        Editing a file changes the file ctime, not its tax-year directory's
        ctime, so the Write/Edit payload is the bounded way to identify it.
        """
        archived = tmp_path / "archive" / "2024" / "reviews" / "review.md"
        archived.parent.mkdir(parents=True)
        archived.write_text("old")
        archived.chmod(0o644)
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0

        archived.write_text("changed")
        archived.chmod(0o644)
        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": str(archived)}}
        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(archived) == 0o600

    @staticmethod
    def _is_case_insensitive_filesystem(tmp_path):
        """Probe tmp_path rather than trusting `uname`.

        A case-insensitive filesystem answers to either spelling of a name; a
        case-sensitive one treats them as two unrelated names. Three of this
        class's tests each need to know which kind of host they are running
        on and skip the scenario that cannot occur on the other kind — this
        is their shared probe rather than three copies of the same three
        lines, which is what a fourth such test would otherwise become.
        """
        probe = tmp_path / "case-probe"
        probe.write_text("x")
        is_case_insensitive = (tmp_path / "CASE-PROBE").exists()
        probe.unlink()
        return is_case_insensitive

    @staticmethod
    def _start_behind_a_slow_walk(workspace, payload, slow_walk,
                                  project_dir=None):
        """Start the hook with one `find` paused, once the pause is live.

        Pausing a walk is what makes ORDER observable rather than merely
        outcome: everything hardened before it is already 0600 while the hook is
        still blocked, and everything that waits for it is still 0644. Asserting
        the end state instead would pass on either ordering, since every walk
        eventually hardens its own set.

        `slow_walk` selects WHICH walk stalls, by find's first argument — a
        runtime tree by name, or `.` for harden_workspace_artifacts. It has no
        default and every caller must state it explicitly: choosing it wrongly
        is how one of these tests stops discriminating, because pausing a walk
        that runs before the behaviour under test proves nothing — the
        assertion is then satisfied by the very walk the fast path exists to
        pre-empt — and a silent default let exactly that happen once already.

        Returns the live process and the marker the shim touches; the caller
        must reap it.
        """
        shim = workspace / "bin"
        shim.mkdir()
        marker = workspace / "full-walk-started"
        (shim / "find").write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            f"  {slow_walk})\n"
            "    : > \"$PERMS_FIND_MARKER\"\n"
            "    /bin/sleep 2\n"
            "    ;;\n"
            "esac\n"
            f"exec {shutil.which('find')} \"$@\"\n"
        )
        (shim / "find").chmod(0o755)
        process = subprocess.Popen(
            ["bash", str(PERMS_HOOK)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ,
                 "CLAUDE_PROJECT_DIR": str(project_dir or workspace),
                 "PERMS_FIND_MARKER": str(marker),
                 "PATH": f"{shim}:{os.environ['PATH']}"},
        )
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload))
        process.stdin.close()
        # communicate() re-flushes self.stdin, and flushing an already-closed
        # stream raises ValueError up to and including 3.13, 3.14 being where
        # `except ValueError` was added. The floor is 3.13, so the raising half
        # is a leg CI runs rather than a version below it. Dropping the
        # reference keeps the test honest on any interpreter rather than only on
        # the newest one.
        process.stdin = None
        deadline = time.time() + 10
        while time.time() < deadline and not marker.exists():
            time.sleep(0.02)
        return process, marker

    @needs_jq
    def test_write_payload_hardens_exact_target_before_a_slow_full_walk_finishes(
            self, tmp_path):
        """The named file must be safe even if the later tree scan times out.

        `harden_full_tree` batches file chmods until find has traversed the whole
        tree. Pausing that traversal exposes the ordering directly: a target
        parsed after the walk is still 0644 while the hook is blocked here.
        """
        documents = tmp_path / "documents"
        documents.mkdir()
        target = documents / "new.pdf"
        target.write_text("financial data")
        target.chmod(0o644)
        (tmp_path / ".claude").mkdir()

        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": str(target)}}
        process, marker = self._start_behind_a_slow_walk(
            tmp_path, payload, slow_walk="documents")

        try:
            assert marker.exists(), "the deliberately slow full walk never began"
            assert _mode(target) == 0o600, (
                "the exact Write target waited behind the unbounded tree scan")
            _, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    @needs_jq
    def test_a_generated_pdf_outside_every_runtime_tree_is_hardened_early(
            self, tmp_path):
        """A return can be generated into any subdirectory, not just a named one.

        The early payload path recognised only the eight runtime prefixes, so a
        completed return in `exports/` was left to `harden_workspace_artifacts`
        — which finds it only after walking the whole contributor tree, and not
        at all if that walk hits the hook timeout.

        The paused walk is `harden_workspace_artifacts` ITSELF (`find .`), not a
        runtime tree: the fast path runs before every walk regardless of which
        of the two runs first between themselves, so pausing `documents` here
        would still leave `harden_workspace_artifacts` free to find `exports/`
        on its own — satisfying this assertion via the walk, not the fast path,
        and passing even with the fast path's artifact arm deleted.
        """
        documents = tmp_path / "documents"
        documents.mkdir()
        (documents / "w2.pdf").write_text("financial data")
        target = tmp_path / "exports" / "Tax_Return_2026.PDF"
        target.parent.mkdir()
        target.write_text("financial data")
        target.chmod(0o644)
        (tmp_path / ".claude").mkdir()

        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": str(target)}}
        process, marker = self._start_behind_a_slow_walk(
            tmp_path, payload, slow_walk=".")

        try:
            assert marker.exists(), "the deliberately slow walk never began"
            assert _mode(target) == 0o600, (
                "a generated return outside the runtime trees waited behind "
                "the tree scan")
            _, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    def test_a_bash_written_document_is_hardened_before_the_workspace_sweep(
            self, tmp_path):
        """A slow generic sweep must not starve the named runtime trees.

        `harden_workspace_artifacts` prunes only the NAMED trees below — a
        nested checkout, a build output, or any other subtree a workspace
        happens to contain is walked in full, so that sweep is not bounded
        by construction. `documents/` holds the taxpayer's actual source
        files under arbitrary names, bounded only by what got scanned in,
        and AGENTS.md singles it out as needing its own rule for exactly
        that reason. The payload is opaque Bash, so there is nothing to
        fast-path; only walk order protects this file, and the named tree
        goes first so a slow sweep elsewhere cannot strand it at 0644.

        The paused walk is `.` (harden_workspace_artifacts itself), not a
        runtime tree: pausing `documents` would prove nothing here, since
        the loop runs first and would already have hardened the target
        before the pause could even engage.
        """
        documents = tmp_path / "documents"
        documents.mkdir()
        target = documents / "new_w2.pdf"
        target.write_text("financial data")
        target.chmod(0o644)
        (tmp_path / ".claude").mkdir()

        process, marker = self._start_behind_a_slow_walk(
            tmp_path, self.OPAQUE_BASH, slow_walk=".")

        try:
            assert marker.exists(), "the deliberately slow walk never began"
            assert _mode(target) == 0o600, (
                "a Bash-written document waited behind the workspace sweep")
            _, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    @needs_jq
    def test_the_payload_fast_path_reaches_no_further_than_the_walk(self,
                                                                     tmp_path):
        """Earlier, not wider.

        `harden_workspace_artifacts` prunes `.git` and `.venv`, so a matching
        name inside either must not become hardened merely because it arrived
        by the payload route instead. More chmods is not automatically safer: a
        permissions hook that reaches into the object store is one a contributor
        switches off, and a switched-off hook protects nothing.
        """
        (tmp_path / ".claude").mkdir()
        targets = []
        for tree in (".git", ".venv"):
            (tmp_path / tree).mkdir()
            artifact = tmp_path / tree / "objects.tmp"
            artifact.write_text("not taxpayer data")
            artifact.chmod(0o644)
            targets.append(artifact)

        for artifact in targets:
            payload = {"tool_name": "Write",
                       "tool_input": {"file_path": str(artifact)}}
            assert self._run(tmp_path, payload).returncode == 0

        leaked = [str(a.relative_to(tmp_path)) for a in targets
                  if _mode(a) != 0o644]
        assert not leaked, f"the fast path chmodded pruned trees: {leaked}"

    @needs_jq
    def test_a_case_aliased_venv_path_is_still_excluded_from_hardening(
            self, tmp_path):
        """A default case-insensitive macOS filesystem answers to either spelling.

        `.VENV/tool.tmp` and `.venv/tool.tmp` name the SAME on-disk file there,
        so a payload reporting the uppercase alias still passes `[ -e "$edited" ]`.
        A case-sensitive `case "$edited" in .venv/*)` arm misses that alias, falls
        to the generic artifact-name arm, and lets the fast path chmod a file
        inside the tree `harden_workspace_artifacts` deliberately prunes — for
        example turning a venv script's 0755 into 0600 and breaking it. Skipped
        on a case-sensitive host (CI runs Linux): there `.VENV` is an ordinary,
        unrelated directory rather than an alias of `.venv`, so this scenario
        cannot occur and an end-to-end reproduction would test nothing real.
        """
        is_case_insensitive = self._is_case_insensitive_filesystem(tmp_path)
        if not is_case_insensitive:
            pytest.skip("host filesystem is case-sensitive")

        (tmp_path / ".claude").mkdir()
        (tmp_path / ".venv").mkdir()
        artifact = tmp_path / ".venv" / "tool.tmp"
        artifact.write_text("not taxpayer data")
        artifact.chmod(0o755)

        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": str(tmp_path / ".VENV" / "tool.tmp")}}
        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(artifact) == 0o755, (
            "the fast path chmodded .venv via a case-aliased payload path")

    @needs_jq
    def test_a_case_varied_sensitive_tree_path_is_still_hardened(
            self, tmp_path):
        """A case-varied spelling of an INCLUDED tree must not skip the fast path.

        The mirror image of the alias test above: the fast path's
        case-alias handling initially covered only the EXCLUDED .git/.venv
        arms, added when a case-insensitive host let `.VENV/tool.tmp` and
        `.venv/tool.tmp` collide. The INCLUDED arms (documents/, summaries/,
        ...) matched case-sensitively only, so `Documents/scan.png` on the
        same kind of host silently skipped the fast path and was left at
        whatever mode the Write left it in until the next full workspace
        walk — reproduced directly by review: the file stayed 0644 for the
        whole span of a paused walk. Skipped on a case-sensitive host (CI
        runs Linux): there `Documents` is an ordinary, distinct directory
        outside every named tree, and no guarantee is expected to cover it.

        The paused walk is `documents` — the SAME directory, since that is
        what makes this an alias at all. Running the hook to completion
        instead asserts nothing: `harden_changed_tree documents` reaches
        `Documents/scan.png` through the other spelling of its own inode, so
        the end state is 0600 whether the fast path classified the payload
        or dropped it entirely. Only the pause separates the two.
        """
        is_case_insensitive = self._is_case_insensitive_filesystem(tmp_path)
        if not is_case_insensitive:
            pytest.skip("host filesystem is case-sensitive")

        (tmp_path / ".claude").mkdir()
        (tmp_path / "documents").mkdir()
        target = tmp_path / "Documents" / "scan.png"
        target.write_text("financial data")
        target.chmod(0o644)

        payload = {"tool_name": "Write", "tool_input": {"file_path": str(target)}}
        process, marker = self._start_behind_a_slow_walk(
            tmp_path, payload, slow_walk="documents")

        try:
            assert marker.exists(), "the deliberately slow walk never began"
            assert _mode(target) == 0o600, (
                "a case-varied spelling of a sensitive tree skipped the fast "
                "path")
            _, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    @needs_jq
    def test_a_distinct_uppercase_directory_is_still_hardened(self, tmp_path):
        """.VENV is not a case alias of .venv on every host.

        On a case-sensitive filesystem (CI runs Linux) `.VENV` is an
        ordinary, distinct directory that `harden_workspace_artifacts`'s find
        walk never prunes. Excluding it from the fast path by name alone —
        the same way the case-insensitive-alias fix folds `.VENV` into
        `.venv` — would strand a file inside it at whatever mode it arrived
        with if the later workspace scan times out. Skipped on a
        case-insensitive host (this macOS dev box): there `.VENV` and
        `.venv` name the same on-disk file, so the two cannot coexist as
        distinct directories and this scenario cannot occur.

        The paused walk is `harden_workspace_artifacts` ITSELF (`find .`),
        which is the very scan the docstring above says a timeout can lose.
        Running the hook to completion instead asserts nothing: that walk
        prunes the literal `./.venv` and so descends into the distinct
        `./.VENV`, where `-iname '*.pdf'` matches — the end state is 0600
        whether the fast path classified `.VENV` correctly or wrongly folded
        it into the pruned tree. Only the pause separates the two.
        """
        is_case_insensitive = self._is_case_insensitive_filesystem(tmp_path)
        if is_case_insensitive:
            pytest.skip("host filesystem is case-insensitive")

        (tmp_path / ".claude").mkdir()
        (tmp_path / ".VENV").mkdir()
        target = tmp_path / ".VENV" / "return.pdf"
        target.write_text("financial data")
        target.chmod(0o644)

        payload = {"tool_name": "Write", "tool_input": {"file_path": str(target)}}
        process, marker = self._start_behind_a_slow_walk(
            tmp_path, payload, slow_walk=".")

        try:
            assert marker.exists(), "the deliberately slow walk never began"
            assert _mode(target) == 0o600, (
                "a distinct uppercase directory was excluded as if it were a "
                "case alias of the pruned .venv")
            _, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    @needs_jq
    @needs_gnu_chmod
    def test_a_dash_prefixed_root_artifact_reaches_the_fast_path_cleanly(
            self, tmp_path):
        """An absolute payload path can strip to a root name starting with `-`.

        `/repo/-return.pdf` strips to the root-relative `-return.pdf`. The
        root PDF/backup glob at pass start already hardens this valid name
        safely as `./-return.pdf`, so its mode is correct either way — the
        bug is in the payload fast path's OWN, later, redundant chmod call:
        unprefixed, GNU chmod reads `-return.pdf` as an unrecognized option,
        `warn`s, sets `failed=1`, and the hook exits 1 and skips stamp
        advancement despite the Write having fully succeeded. Skipped on BSD
        chmod (this macOS dev box), which does not reproduce that parse.
        """
        (tmp_path / ".claude").mkdir()
        target = tmp_path / "-return.pdf"
        target.write_text("financial data")
        target.chmod(0o644)

        payload = {"tool_name": "Write", "tool_input": {"file_path": str(target)}}
        result = self._run(tmp_path, payload)
        assert result.returncode == 0, result.stderr
        assert result.stderr == '', (
            "the fast path's own chmod call warned on a dash-prefixed name")
        assert _mode(target) == 0o600

    @needs_jq
    def test_a_physically_resolved_payload_path_is_still_in_the_workspace(
            self, tmp_path):
        """CLAUDE_PROJECT_DIR may name the workspace through a symlink.

        `cd` without -P leaves $PWD holding that logical spelling, so a tool
        reporting the RESOLVED path failed the `"$PWD"/*` test, fell into the
        absolute-path arm, and was discarded as external. The whole early-harden
        fast path was then inert on such a host — silently, since the later tree
        walk still hardens the file and the hook still exits 0.
        """
        real = tmp_path / "real"
        (real / "documents").mkdir(parents=True)
        (real / ".claude").mkdir()
        target = real / "documents" / "w2.pdf"
        target.write_text("financial data")
        target.chmod(0o644)
        link = tmp_path / "link"
        link.symlink_to(real)

        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": str(target.resolve())}}
        process, marker = self._start_behind_a_slow_walk(
            real, payload, slow_walk="documents", project_dir=link)

        try:
            assert marker.exists(), "the deliberately slow walk never began"
            assert _mode(target) == 0o600, (
                "a physically-resolved payload path was treated as external")
            _, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    @needs_jq
    def test_traversal_spelled_archive_edit_forces_a_full_pass(self, tmp_path):
        """Clearing a traversal path must not also clear its archive effect."""
        target = tmp_path / "archive" / "2025" / "reviews" / "out.md"
        target.parent.mkdir(parents=True)
        target.write_text("old")
        (tmp_path / "archive" / "2024").mkdir()
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0

        target.write_text("financial data")
        target.chmod(0o644)
        for year in ("2024", "2025"):
            os.utime(tmp_path / "archive" / year, (1, 1))
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(
                    tmp_path / "archive" / "2024" / ".." / "2025"
                    / "reviews" / "out.md"
                )
            },
        }

        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(target) == 0o600

    @needs_jq
    def test_delayed_nested_archive_creation_is_seen_on_the_next_pass(
            self, tmp_path):
        """A detached writer changes only the indexed immediate parent."""
        reviews = tmp_path / "archive" / "2025" / "reviews"
        reviews.mkdir(parents=True)
        (reviews / "existing.md").write_text("old")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        os.utime(tmp_path / "archive" / "2025", (1, 1))

        delayed = reviews / "late.md"
        delayed.write_text("financial data")
        delayed.chmod(0o644)
        payload = {"tool_name": "Bash", "tool_input": {"command": "pwd -P"}}

        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(delayed) == 0o600

    @needs_jq
    def test_archive_file_modes_are_tracked_after_deferred_work_outlives_a_call(
            self, tmp_path):
        """File-mode tracking must outlive any one-shot deferred recheck."""
        target = tmp_path / "archive" / "2025" / "reviews" / "out.md"
        target.parent.mkdir(parents=True)
        target.write_text("financial data")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0

        opaque = {"tool_name": "Bash",
                  "tool_input": {"command": "python helper.py"}}
        assert self._run(tmp_path, opaque).returncode == 0
        read_only = {"tool_name": "Bash", "tool_input": {"command": "pwd -P"}}
        assert self._run(tmp_path, read_only).returncode == 0
        target.chmod(0o644)  # producer outlives that entire next tool call

        assert self._run(tmp_path, read_only).returncode == 0
        assert _mode(target) == 0o600
        file_index = tmp_path / ".claude" / ".archive-files"
        assert file_index.is_file()
        assert _mode(file_index) == 0o600

    @needs_jq
    def test_archive_index_modes_are_reasserted_before_incremental_use(self,
                                                                       tmp_path):
        """Archive path manifests are sensitive even on an otherwise quiet pass.

        @needs_jq is load-bearing, not decoration. Without jq the payload cannot
        be parsed at all, the no-jq branch runs the exposure scan, and that
        rebuilds both manifests at 600 through mktemp — so this assertion stayed
        green on such a host with reassert_archive_index_modes deleted outright,
        testing a code path it does not name.
        """
        year = tmp_path / "archive" / "2025"
        year.mkdir(parents=True)
        (year / "return.pdf").write_text("financial data")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        assert self._run(tmp_path).returncode == 0
        indexes = [dot_claude / ".archive-dirs",
                   dot_claude / ".archive-files"]
        for index in indexes:
            index.chmod(0o644)
        for directory in (tmp_path / "archive", year):
            os.utime(directory, (1, 1))

        quiet_write = {"tool_name": "Write",
                       "tool_input": {"file_path": str(tmp_path / "notes.md")}}
        result = self._run(tmp_path, quiet_write)

        assert result.returncode == 0, result.stderr
        assert all(_mode(index) == 0o600 for index in indexes), (
            "an incremental pass trusted a group/world-readable archive index")

    @needs_jq
    def test_archive_index_chmod_failure_holds_stamp_and_attempts_both_modes(
            self, tmp_path):
        """One failed manifest chmod must reject the branch without hiding it.

        The directory manifest fails first; the file-manifest assertion proves
        the hook still attempts the second re-hardening before falling back.
        """
        year = tmp_path / "archive" / "2025"
        year.mkdir(parents=True)
        (year / "return.pdf").write_text("financial data")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        assert self._run(tmp_path).returncode == 0
        directory_index = dot_claude / ".archive-dirs"
        file_index = dot_claude / ".archive-files"
        stamp = dot_claude / ".perms-stamp"
        directory_index.chmod(0o644)
        file_index.chmod(0o644)
        before = (stamp.read_bytes(), stamp.stat().st_mtime_ns)
        for directory in (tmp_path / "archive", year):
            os.utime(directory, (1, 1))

        shim = tmp_path / "bin"
        shim.mkdir()
        (shim / "chmod").write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'.claude/.archive-dirs'*) exit 1 ;;\n"
            "esac\n"
            "exec /bin/chmod \"$@\"\n"
        )
        (shim / "chmod").chmod(0o755)
        result = self._run(
            tmp_path,
            {"tool_name": "Write",
             "tool_input": {"file_path": str(tmp_path / "notes.md")}},
            env_extra={"PATH": f"{shim}:{os.environ['PATH']}"},
        )

        assert result.returncode == 1
        assert _mode(directory_index) == 0o644
        assert _mode(file_index) == 0o600, (
            "failure on the first index skipped re-hardening the second")
        assert (stamp.read_bytes(), stamp.stat().st_mtime_ns) == before, (
            "an index chmod failure advanced the permission stamp")

    @needs_jq
    def test_a_wrong_index_mode_rejects_the_manifest_contents_too(self,
                                                                  tmp_path):
        """Repairing the mode cannot make the earlier contents trustworthy.

        A manifest that ARRIVED at anything but 600 was written or touched by
        something that is not this hook, so the incremental branch must not read
        it as the COMPLETE list of archived files. Here it is also truncated —
        the shape a restore, a foreign umask or a killed writer leaves — and the
        omitted file is exposed. archive_changed_directories succeeds on an
        unchanged tree (it reports only that the index is usable, not that
        anything moved), so the incremental branch really is the one taken and
        harden_indexed_archive_files really does consult this manifest: trusting
        it retires archive/2025/omitted.pdf from checking permanently.
        """
        year = tmp_path / "archive" / "2025"
        year.mkdir(parents=True)
        listed = year / "listed.pdf"
        omitted = year / "omitted.pdf"
        listed.write_text("financial data")
        omitted.write_text("financial data")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        assert self._run(tmp_path).returncode == 0

        file_index = dot_claude / ".archive-files"
        file_index.write_bytes(b"archive/2025/listed.pdf\0")
        file_index.chmod(0o644)
        omitted.chmod(0o644)
        for directory in (tmp_path / "archive", year):
            os.utime(directory, (1, 1))

        result = self._run(
            tmp_path,
            {"tool_name": "Write",
             "tool_input": {"file_path": str(tmp_path / "notes.md")}})

        assert result.returncode == 0, result.stderr
        assert _mode(omitted) == 0o600, (
            "a group/world-readable manifest was trusted as a complete list")
        assert _mode(file_index) == 0o600
        assert b"archive/2025/omitted.pdf" in file_index.read_bytes().split(b"\0"), (
            "the distrusted manifest was not rebuilt from the filesystem")

    @needs_jq
    def test_an_existing_manifest_is_rehardened_when_its_sibling_is_missing(
            self, tmp_path):
        """A missing .archive-files is no reason to leave .archive-dirs open.

        .archive-dirs is a NUL-separated list of every archived directory path,
        taxpayer data whatever its sibling is doing. The existence precheck used
        to `return 1` before ANY chmod, directly contradicting the comment above
        it. The symlinked result.json is what makes that observable: it sets the
        failed flag, and the index-refresh block — which would otherwise replace
        .archive-dirs with a fresh 600 mktemp candidate and mask the omission —
        requires failed=0.
        """
        year = tmp_path / "archive" / "2025"
        year.mkdir(parents=True)
        (year / "return.pdf").write_text("financial data")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        assert self._run(tmp_path).returncode == 0

        directory_index = dot_claude / ".archive-dirs"
        (dot_claude / ".archive-files").unlink()
        directory_index.chmod(0o644)
        (tmp_path / "result.json").symlink_to(tmp_path / "nowhere.json")

        result = self._run(
            tmp_path,
            {"tool_name": "Write",
             "tool_input": {"file_path": str(tmp_path / "notes.md")}})

        assert result.returncode == 1
        assert _mode(directory_index) == 0o600, (
            "a missing sibling left the archive directory manifest readable")

    @needs_jq
    def test_partial_archive_index_publish_forces_both_to_rebuild_next_call(
            self, tmp_path):
        """A timeout between manifest renames must leave durable dirty state.

        The directory manifest is allowed to advance and the file-manifest move
        then fails. Without a durable marker the next incremental call trusts
        that mismatched pair and misses the preserved-mtime file.

        The marker is a file of its own rather than the stamp's absence. The
        stamp is the incremental boundary for every OTHER runtime tree as well,
        none of which is in doubt here, so the assertions below pin both halves:
        the archive pair is marked dirty, and the stamp is left exactly as a
        failed pass always leaves it — held, not destroyed.
        """
        year = tmp_path / "archive" / "2025"
        year.mkdir(parents=True)
        (year / "seed.pdf").write_text("financial data")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        assert self._run(tmp_path).returncode == 0
        stamp = dot_claude / ".perms-stamp"

        restored = year / "documents"
        restored.mkdir(mode=0o700)
        late = restored / "late.pdf"
        late.write_text("financial data")
        late.chmod(0o600)
        for directory in (tmp_path / "archive", year):
            os.utime(directory, (1, 1))

        shim = tmp_path / "bin"
        shim.mkdir()
        (shim / "mv").write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'.claude/.archive-files') exit 1 ;;\n"
            "esac\n"
            "exec /bin/mv \"$@\"\n"
        )
        (shim / "mv").chmod(0o755)
        partial = self._run(
            tmp_path, self.OPAQUE_BASH,
            env_extra={"PATH": f"{shim}:{os.environ['PATH']}"},
        )

        marker = dot_claude / ".archive-index-dirty"

        assert partial.returncode == 1
        assert marker.exists(), (
            "a partially published archive-index pair left no dirty marker")
        assert stamp.exists(), (
            "an archive-manifest failure destroyed the incremental boundary "
            "of every unrelated runtime tree")
        assert b"archive/2025/documents" in (
            dot_claude / ".archive-dirs").read_bytes().split(b"\0")
        assert b"archive/2025/documents/late.pdf" not in (
            dot_claude / ".archive-files").read_bytes().split(b"\0")

        late.chmod(0o644)
        for directory in (tmp_path / "archive", year, restored):
            os.utime(directory, (1, 1))
        next_call = self._run(
            tmp_path,
            {"tool_name": "Write",
             "tool_input": {"file_path": str(tmp_path / "notes.md")}},
        )

        assert next_call.returncode == 0, next_call.stderr
        assert _mode(late) == 0o600, (
            "the mismatched manifests survived into an incremental pass")

    @needs_jq
    def test_a_quiet_pass_never_leaves_the_stamp_absent_mid_refresh(self,
                                                                    tmp_path):
        """Publishing the manifests must not open a stamp-absent window.

        ARCHIVE_INDEX_DIRTY is set by track_archive_refresh_root, which the
        exposure scan calls UNCONDITIONALLY and before its repair loop, so this
        window opened on every opaque Bash call — including this one, over a
        completely unchanged archive. Anything that read the stamp inside it saw
        none and went full, and a kill inside it destroyed a consistent stamp
        for a reason that had nothing to do with the trees the stamp bounds.

        The witness has to be sampled DURING the pass. Afterwards the stamp is
        present either way, because the pass rewrites it on the way out.
        """
        year = tmp_path / "archive" / "2025"
        year.mkdir(parents=True)
        (year / "return.pdf").write_text("financial data")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        assert self._run(tmp_path).returncode == 0

        witness = tmp_path / "stamp-seen"
        shim = tmp_path / "bin"
        shim.mkdir()
        (shim / "mv").write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'.claude/.archive-dirs')\n"
            "    if [ -f .claude/.perms-stamp ]; then\n"
            f"      echo present > '{witness}'\n"
            "    else\n"
            f"      echo absent > '{witness}'\n"
            "    fi ;;\n"
            "esac\n"
            "exec /bin/mv \"$@\"\n"
        )
        (shim / "mv").chmod(0o755)

        result = self._run(
            tmp_path, self.OPAQUE_BASH,
            env_extra={"PATH": f"{shim}:{os.environ['PATH']}"},
        )

        assert result.returncode == 0, result.stderr
        assert witness.read_text().strip() == "present", (
            "publishing the archive manifests unlinked the shared stamp")
        assert not (dot_claude / ".archive-index-dirty").exists(), (
            "a successful publish left the dirty marker behind")

    @needs_jq
    def test_exported_echo_function_forces_a_full_archive_pass(self, tmp_path):
        """An exported function named like a builtin can write another year."""
        visible = tmp_path / "archive" / "2024" / "out.md"
        hidden = tmp_path / "archive" / "2025" / "reviews" / "out.md"
        visible.parent.mkdir(parents=True)
        hidden.parent.mkdir(parents=True)
        visible.write_text("old")
        hidden.write_text("old")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        for year in ("2024", "2025"):
            os.utime(tmp_path / "archive" / year, (1, 1))

        hidden.write_text("financial data")
        hidden.chmod(0o644)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo x > archive/2024/out.md"},
        }
        wrapper = (
            'echo() { :; }; export -f echo; '
            'exec bash "$PERMISSIONS_HOOK"'
        )
        result = subprocess.run(
            ["bash", "-c", wrapper], input=json.dumps(payload), text=True,
            capture_output=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "PERMISSIONS_HOOK": str(PERMS_HOOK)},
        )

        assert result.returncode == 0, result.stderr
        assert _mode(hidden) == 0o600

    @needs_jq
    def test_notebook_edit_payload_hardens_existing_archive_notebook(self,
                                                                     tmp_path):
        """NotebookEdit needs the same old-year payload fallback as Write/Edit."""
        notebook = tmp_path / "archive" / "2024" / "reviews" / "work.ipynb"
        notebook.parent.mkdir(parents=True)
        notebook.write_text("{}")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        os.utime(tmp_path / "archive" / "2024", (1, 1))

        notebook.write_text('{"financial": true}')
        notebook.chmod(0o644)
        payload = {
            "tool_name": "NotebookEdit",
            "tool_input": {"notebook_path": str(notebook)},
        }

        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(notebook) == 0o600

    @needs_jq
    def test_bash_payload_hardens_nested_write_in_existing_archive_year(self,
                                                                        tmp_path):
        """A nested file write does not update the tax-year directory ctime.

        The Bash payload must identify and re-harden the concrete year without
        recursively revisiting sibling years, or a 0644 review written by a
        shell command survives the shallow archive poll.
        """
        old_year = tmp_path / "archive" / "2023" / "reviews"
        target_dir = tmp_path / "archive" / "2024" / "reviews"
        old_year.mkdir(parents=True)
        target_dir.mkdir(parents=True)
        (old_year / "old.md").write_text("old")
        (target_dir / "existing.md").write_text("old")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        # Age the complete pre-existing tree outside the deliberate one-second
        # boundary overlap. Leaving `reviews/` fresh would correctly classify
        # it as recent even when only its tax-year parent is historical.
        for directory in (tmp_path / "archive" / "2023", old_year,
                          tmp_path / "archive" / "2024", target_dir):
            os.utime(directory, (1, 1))

        generated = target_dir / "generated.md"
        generated.write_text("financial data")
        generated.chmod(0o644)

        shim = tmp_path / "bin"
        shim.mkdir()
        log = tmp_path / "chmod.log"
        (shim / "chmod").write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$PERMS_CHMOD_LOG\"\n"
            "exec /bin/chmod \"$@\"\n"
        )
        (shim / "chmod").chmod(0o755)
        payload = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "printf x > archive/2024/reviews/generated.md"
            },
        }
        r = subprocess.run(
            ["bash", str(PERMS_HOOK)], input=json.dumps(payload),
            text=True, capture_output=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "PERMS_CHMOD_LOG": str(log),
                 "PATH": f"{shim}:{os.environ['PATH']}"})

        assert r.returncode == 0, r.stderr
        assert _mode(generated) == 0o600
        calls = log.read_text()
        assert "archive/2024" in calls
        assert "archive/2023" not in calls, (
            "a concrete Bash archive target caused a full-history traversal")

    @needs_jq
    def test_dynamic_bash_archive_path_falls_back_to_full_hardening(self,
                                                                    tmp_path):
        """A shell-variable path cannot be mapped safely to one tax year.

        The bounded optimization must yield to a full archive pass rather than
        treat the absence of a literal `archive/YYYY` token as no write.
        """
        target = tmp_path / "archive" / "2024" / "reviews" / "generated.md"
        target.parent.mkdir(parents=True)
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0

        target.write_text("financial data")
        target.chmod(0o644)
        payload = {
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    'archive_root=archive; printf x > '
                    '"$archive_root/2024/reviews/generated.md"'
                )
            },
        }

        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(target) == 0o600

    @needs_jq
    def test_mixed_literal_and_dynamic_bash_archive_paths_fall_back_to_full(
            self, tmp_path):
        """One literal source year must not hide a dynamic destination year."""
        source = tmp_path / "archive" / "2024" / "reviews" / "source.md"
        target = tmp_path / "archive" / "2025" / "reviews" / "out.md"
        source.parent.mkdir(parents=True)
        target.parent.mkdir(parents=True)
        source.write_text("source")
        target.write_text("old")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        os.utime(tmp_path / "archive" / "2024", (1, 1))
        os.utime(tmp_path / "archive" / "2025", (1, 1))

        target.write_text("financial data")
        target.chmod(0o644)
        payload = {
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "YEAR=2025; cat archive/2024/reviews/source.md > "
                    "archive/$YEAR/reviews/out.md"
                ),
            },
        }

        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(target) == 0o600

    @needs_jq
    def test_opaque_bash_payload_hardens_an_existing_archive(self, tmp_path):
        """A helper script can write archive without naming it in the payload."""
        target = tmp_path / "archive" / "2025" / "reviews" / "out.md"
        target.parent.mkdir(parents=True)
        target.write_text("old")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        os.utime(tmp_path / "archive" / "2025", (1, 1))

        target.write_text("financial data")
        target.chmod(0o644)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "bash helper.sh"},
        }

        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(target) == 0o600

    @needs_jq
    def test_opaque_bash_with_literal_archive_argument_hardens_all_years(
            self, tmp_path):
        """A script's literal input year does not bound its output year."""
        helper = tmp_path / "archive" / "2024" / "helper.sh"
        target = tmp_path / "archive" / "2025" / "reviews" / "out.md"
        helper.parent.mkdir(parents=True)
        target.parent.mkdir(parents=True)
        helper.write_text("#!/bin/sh\n")
        target.write_text("old")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        os.utime(tmp_path / "archive" / "2024", (1, 1))
        os.utime(tmp_path / "archive" / "2025", (1, 1))

        target.write_text("financial data")
        target.chmod(0o644)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "bash archive/2024/helper.sh"},
        }

        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(target) == 0o600

    @needs_jq
    def test_literal_source_with_variable_destination_hardens_all_years(
            self, tmp_path):
        """A literal input year cannot bound a variable-expanded destination."""
        source = tmp_path / "archive" / "2024" / "source.md"
        target = tmp_path / "archive" / "2025" / "reviews" / "out.md"
        source.parent.mkdir(parents=True)
        target.parent.mkdir(parents=True)
        source.write_text("source")
        target.write_text("old")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        os.utime(tmp_path / "archive" / "2024", (1, 1))
        os.utime(tmp_path / "archive" / "2025", (1, 1))

        target.write_text("financial data")
        target.chmod(0o644)
        payload = {
            "tool_name": "Bash",
            "tool_input": {
                "command": 'cat archive/2024/source.md > "$DESTINATION"'
            },
        }

        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(target) == 0o600

    @needs_jq
    def test_simple_read_only_bash_payload_does_not_walk_archive(self, tmp_path):
        """The opaque-command fallback must not undo the bounded optimization."""
        year = tmp_path / "archive" / "2024"
        year.mkdir(parents=True)
        (year / "old.pdf").write_text("old")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        os.utime(year, (1, 1))

        shim = tmp_path / "bin"
        shim.mkdir()
        log = tmp_path / "chmod.log"
        (shim / "chmod").write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$PERMS_CHMOD_LOG\"\n"
            "exec /bin/chmod \"$@\"\n"
        )
        (shim / "chmod").chmod(0o755)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "pwd -P"},
        }
        result = subprocess.run(
            ["bash", str(PERMS_HOOK)], input=json.dumps(payload),
            text=True, capture_output=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "PERMS_CHMOD_LOG": str(log),
                 "PATH": f"{shim}:{os.environ['PATH']}"},
        )

        assert result.returncode == 0, result.stderr
        assert "archive/2024" not in log.read_text(), (
            "a simple read-only command caused a full archive traversal")

    @needs_jq
    def test_git_external_diff_is_treated_as_opaque(self, tmp_path):
        """Git diff options can execute a configured external helper."""
        target = tmp_path / "archive" / "2024" / "reviews" / "out.md"
        target.parent.mkdir(parents=True)
        target.write_text("old")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        os.utime(tmp_path / "archive" / "2024", (1, 1))

        target.write_text("financial data")
        target.chmod(0o644)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git diff --ext-diff"},
        }

        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(target) == 0o600

    @needs_jq
    def test_ripgrep_helper_options_are_treated_as_opaque(self, tmp_path):
        """Ripgrep can execute hostname and decompression helper programs."""
        target = tmp_path / "archive" / "2024" / "reviews" / "out.md"
        target.parent.mkdir(parents=True)
        target.write_text("old")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        os.utime(tmp_path / "archive" / "2024", (1, 1))

        target.write_text("financial data")
        target.chmod(0o644)
        payload = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "rg --hostname-bin=helper --hyperlink-format=default"
            },
        }

        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(target) == 0o600

    @needs_jq
    def test_read_only_utility_name_from_a_path_shim_is_opaque(self, tmp_path):
        """A helper-capable PATH shim must not inherit `ls`'s read-only trust."""
        target = tmp_path / "archive" / "2024" / "reviews" / "out.md"
        target.parent.mkdir(parents=True)
        target.write_text("old")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        os.utime(tmp_path / "archive" / "2024", (1, 1))

        target.write_text("financial data")
        target.chmod(0o644)
        shim = tmp_path / "bin"
        shim.mkdir()
        (shim / "ls").write_text("#!/bin/sh\nexit 0\n")
        (shim / "ls").chmod(0o755)
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
        result = subprocess.run(
            ["bash", str(PERMS_HOOK)], input=json.dumps(payload),
            text=True, capture_output=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "PATH": f"{shim}:{os.environ['PATH']}"},
        )

        assert result.returncode == 0, result.stderr
        assert _mode(target) == 0o600

    @needs_jq
    def test_aliased_write_destination_forces_full_archive_pass(self, tmp_path):
        """A visible source year cannot bound a symlink-aliased destination."""
        source = tmp_path / "archive" / "2024" / "source.md"
        target_dir = tmp_path / "archive" / "2025" / "reviews"
        target = target_dir / "out.md"
        source.parent.mkdir(parents=True)
        target_dir.mkdir(parents=True)
        source.write_text("source")
        target.write_text("old")
        (tmp_path / "shortcut").symlink_to(target_dir, target_is_directory=True)
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        os.utime(tmp_path / "archive" / "2024", (1, 1))
        os.utime(tmp_path / "archive" / "2025", (1, 1))

        target.write_text("financial data")
        target.chmod(0o644)
        payload = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "cat archive/2024/source.md > shortcut/out.md"
            },
        }

        assert self._run(tmp_path, payload).returncode == 0
        assert _mode(target) == 0o600

    @needs_jq
    def test_globbed_archive_destination_forces_full_archive_pass(self, tmp_path):
        """An unexpanded glob can write through a symlink into another year."""
        target = tmp_path / "archive" / "2025" / "reviews" / "new.md"
        target.parent.mkdir(parents=True)
        target.write_text("old")
        year_2024 = tmp_path / "archive" / "2024"
        year_2024.mkdir()
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0

        (year_2024 / "link").symlink_to("../2025/reviews/new.md")
        subprocess.run(
            ["bash", "-c", "printf x > archive/2024/*"],
            cwd=tmp_path, check=True,
        )
        target.chmod(0o644)
        os.utime(year_2024, (1, 1))
        os.utime(tmp_path / "archive" / "2025", (1, 1))
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "printf x > archive/2024/*"},
        }

        result = self._run(tmp_path, payload)
        assert result.returncode == 1  # the symlink is refused and reported
        assert _mode(target) == 0o600, (
            "a globbed archive destination escaped the year-scoped pass")

    def test_a_failed_incremental_pass_does_not_advance_the_stamp(self, tmp_path):
        """A transient chmod failure must be retried on the next hook call.

        Advancing the stamp after the failed pass makes the unchanged exposed
        file older than the stamp, so restoring chmod does not help: every
        later incremental walk skips it permanently.
        """
        docs = tmp_path / "documents"
        docs.mkdir()
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        assert self._run(tmp_path).returncode == 0
        stamp = dot_claude / ".perms-stamp"
        os.utime(stamp, (1, 1))
        old_stamp_mtime = stamp.stat().st_mtime_ns

        exposed = docs / "retry.pdf"
        exposed.write_text("x")
        exposed.chmod(0o644)

        shim = tmp_path / "bin"
        shim.mkdir()
        (shim / "chmod").write_text("#!/bin/sh\nexit 1\n")
        (shim / "chmod").chmod(0o755)
        failed = subprocess.run(
            ["bash", str(PERMS_HOOK)], text=True, capture_output=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "PATH": f"{shim}:{os.environ['PATH']}"})

        assert failed.returncode == 1
        assert _mode(exposed) == 0o644
        assert stamp.stat().st_mtime_ns == old_stamp_mtime, (
            "a failed pass advanced the stamp and made the exposed file look old")

        assert self._run(tmp_path).returncode == 0
        assert _mode(exposed) == 0o600, (
            "the unchanged file was not retried after chmod became available")

    def test_a_future_permission_stamp_forces_a_full_pass(self, tmp_path):
        """A backwards clock correction makes current writes older than stamp.

        The hook must distrust the future stamp, harden the full tree, and move
        the stamp back onto the corrected clock so later incremental runs work.
        """
        docs = tmp_path / "documents"
        docs.mkdir()
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        assert self._run(tmp_path).returncode == 0
        stamp = dot_claude / ".perms-stamp"

        future = stamp.stat().st_mtime + 86_400
        os.utime(stamp, (future, future))
        exposed = docs / "clock-correction.pdf"
        exposed.write_text("financial data")
        exposed.chmod(0o644)

        assert self._run(tmp_path).returncode == 0
        assert _mode(exposed) == 0o600
        assert stamp.stat().st_mtime < future, (
            "the future stamp was not rewritten onto the corrected timeline")

    def test_marker_creation_failure_forces_a_full_pass(self, tmp_path):
        """A missing clock marker must not leave future-stamp filtering live."""
        docs = tmp_path / "documents"
        docs.mkdir()
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        assert self._run(tmp_path).returncode == 0
        stamp = dot_claude / ".perms-stamp"
        future = stamp.stat().st_mtime + 86_400
        os.utime(stamp, (future, future))

        exposed = docs / "clock-correction.pdf"
        exposed.write_text("financial data")
        exposed.chmod(0o644)

        shim = tmp_path / "bin"
        shim.mkdir()
        (shim / "mktemp").write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *harden-runtime-perms.XXXXXX*)\n"
            "    scratch=$(\"$REAL_MKTEMP\" \"$@\") || exit 1\n"
            "    mkdir \"$scratch/now\" || exit 1\n"
            "    printf '%s\\n' \"$scratch\"\n"
            "    ;;\n"
            "  *) exec \"$REAL_MKTEMP\" \"$@\" ;;\n"
            "esac\n"
        )
        (shim / "mktemp").chmod(0o755)
        failed = subprocess.run(
            ["bash", str(PERMS_HOOK)], text=True, capture_output=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "REAL_MKTEMP": shutil.which("mktemp"),
                 "PATH": f"{shim}:{os.environ['PATH']}"},
        )

        assert failed.returncode == 1
        assert "permissions pass boundary" in failed.stderr
        assert _mode(exposed) == 0o600, (
            "marker failure left the future-stamp incremental filter active")
        assert stamp.stat().st_mtime == future, (
            "a failed pass must not advance the permission stamp")

    def test_stamp_boundary_precedes_writes_concurrent_with_the_pass(self,
                                                                     tmp_path):
        """A late writer in an already-scanned tree must be caught next pass."""
        documents = tmp_path / "documents"
        projections = tmp_path / "projections"
        documents.mkdir()
        projections.mkdir()
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0

        shim = tmp_path / "bin"
        shim.mkdir()
        target = documents / "late.pdf"
        sentinel = tmp_path / "producer-ran"
        (shim / "chmod").write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *projections*)\n"
            "    if [ ! -e \"$CONCURRENT_SENTINEL\" ]; then\n"
            "      /usr/bin/touch \"$CONCURRENT_SENTINEL\"\n"
            "      /usr/bin/touch \"$CONCURRENT_TARGET\"\n"
            "      /bin/chmod 644 \"$CONCURRENT_TARGET\"\n"
            "      sleep 2\n"
            "    fi\n"
            "    ;;\n"
            "esac\n"
            "exec /bin/chmod \"$@\"\n"
        )
        (shim / "chmod").chmod(0o755)
        second = subprocess.run(
            ["bash", str(PERMS_HOOK)], text=True, capture_output=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "CONCURRENT_SENTINEL": str(sentinel),
                 "CONCURRENT_TARGET": str(target),
                 "PATH": f"{shim}:{os.environ['PATH']}"},
        )

        assert second.returncode == 0, second.stderr
        assert _mode(target) == 0o644
        assert self._run(tmp_path).returncode == 0
        assert _mode(target) == 0o600, (
            "the end-time stamp permanently skipped a concurrent late write")

    def test_permission_stamp_keeps_a_coarse_timestamp_overlap(self, tmp_path):
        """Strict `find -newer` needs distance from a coarse clock tick."""
        (tmp_path / "documents").mkdir()
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()

        assert self._run(tmp_path).returncode == 0
        stamp = dot_claude / ".perms-stamp"
        after = tmp_path / "after-stamp"
        after.touch()

        assert after.stat().st_mtime - stamp.stat().st_mtime >= 0.5, (
            "the stamp has no overlap window for same-tick writes")

    def test_archive_index_keeps_a_coarse_timestamp_overlap(self, tmp_path):
        """The strict archive `-newermm` boundary needs the same overlap."""
        reviews = tmp_path / "archive" / "2025" / "reviews"
        reviews.mkdir(parents=True)
        (reviews / "existing.md").write_text("financial data")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()

        assert self._run(tmp_path).returncode == 0
        archive_index = dot_claude / ".archive-dirs"
        after = tmp_path / "after-archive-index"
        after.touch()

        assert after.stat().st_mtime - archive_index.stat().st_mtime >= 0.5, (
            "the archive index has no overlap window for same-tick writes")

    def test_timestamp_adjustment_failure_preserves_the_old_stamp(self, tmp_path):
        """A failed candidate must not replace the last safe scan boundary."""
        (tmp_path / "documents").mkdir()
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        assert self._run(tmp_path).returncode == 0
        stamp = dot_claude / ".perms-stamp"
        before = (stamp.read_bytes(), stamp.stat().st_mtime_ns)

        shim = tmp_path / "bin"
        shim.mkdir()
        (shim / "touch").write_text("#!/bin/sh\nexit 1\n")
        (shim / "touch").chmod(0o755)
        r = subprocess.run(
            ["bash", str(PERMS_HOOK)], text=True, capture_output=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "PATH": f"{shim}:{os.environ['PATH']}"})

        assert r.returncode == 1
        assert (stamp.read_bytes(), stamp.stat().st_mtime_ns) == before, (
            "failed timestamp adjustment replaced/advanced the old stamp")
        assert not list(dot_claude.glob(".perms-stamp.??????"))

    def test_a_symlinked_permission_stamp_is_rejected_without_external_write(
            self, tmp_path):
        """Stamp maintenance must obey the same workspace boundary as chmod."""
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        outside = tmp_path.parent / f"outside-{tmp_path.name}-stamp"
        outside.write_text("external sentinel")
        stamp = dot_claude / ".perms-stamp"
        stamp.symlink_to(outside)

        r = self._run(tmp_path)

        assert r.returncode == 1
        assert "symlink for the permissions stamp" in r.stderr
        assert str(outside) not in r.stderr
        assert outside.read_text() == "external sentinel", (
            "the hook wrote its stamp through a symlink outside the workspace")

    def test_a_symlinked_claude_directory_forces_a_full_permission_pass(
            self, tmp_path):
        """An external valid stamp must never authorize an incremental skip."""
        documents = tmp_path / "documents"
        documents.mkdir()
        artifact = documents / "old.pdf"
        artifact.write_text("financial data")
        artifact.chmod(0o644)
        os.utime(artifact, (1, 1))
        os.utime(documents, (1, 1))

        outside = tmp_path.parent / f"outside-{tmp_path.name}-dot-claude"
        outside.mkdir()
        stamp = outside / ".perms-stamp"
        stamp.write_text("2\n")
        future = stamp.stat().st_mtime + 86_400
        os.utime(stamp, (future, future))
        (tmp_path / ".claude").symlink_to(outside, target_is_directory=True)

        result = self._run(tmp_path)

        assert result.returncode == 1
        assert "symlinked permissions metadata directory" in result.stderr
        assert _mode(artifact) == 0o600

    @needs_jq
    def test_a_multiline_payload_is_read_whole(self, tmp_path):
        """`IFS= read -r payload` stops at the first newline.

        Every other payload test in this module hands the hook a compact
        `json.dumps`, so a hook that read only `{` satisfied all of them: jq then
        returned an empty tool_name, the Write/Edit branch never ran, and with it
        the ONLY path that hardens the exact `archive/YYYY/...` file the payload
        names. The hook kept exiting 0 and saying nothing, which is what made
        losing that precision invisible.

        Two files are needed because the resulting mode cannot discriminate on
        its own — the fallback hardens the target too. The target is reachable
        only by the Write branch and the sibling only by the opaque-payload
        exposure scan, since both are created after the bootstrap (so the file
        manifest does not list them) with their parents aged back (so the
        directory poll does not see them).
        """
        archive = tmp_path / "archive"
        target_dir = archive / "2024" / "reviews"
        sibling_dir = archive / "2023" / "reviews"
        target_dir.mkdir(parents=True)
        sibling_dir.mkdir(parents=True)
        (target_dir / "seed.md").write_text("seed")
        (sibling_dir / "seed.md").write_text("seed")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0

        target = target_dir / "generated.md"
        sibling = sibling_dir / "generated.md"
        for late in (target, sibling):
            late.write_text("financial data")
            late.chmod(0o644)
        for directory in (archive, archive / "2024", target_dir,
                          archive / "2023", sibling_dir):
            os.utime(directory, (1, 1))

        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": str(target)}}
        r = self._run(tmp_path, payload, indent=2)

        assert r.returncode == 0, r.stderr
        assert _mode(target) == 0o600, (
            "the Write branch never ran — the payload was truncated at its "
            "first newline and the hook could not tell which tool called it")
        assert _mode(sibling) == 0o644, (
            "a Write payload took the unbounded fallback, which is what an "
            "unidentifiable payload gets")

    @needs_jq
    def test_a_pretty_printed_bash_payload_keeps_the_read_only_optimization(
            self, tmp_path):
        """The same truncation seen from the other side.

        A read-only command cannot create an exposed archive artifact, so it is
        the one Bash shape that skips the scan entirely. Truncated to `{`, the
        payload parses as malformed instead and every `pwd`, `ls` and `wc` in
        the session pays for a traversal of the whole archive.
        """
        year = tmp_path / "archive" / "2024"
        year.mkdir(parents=True)
        (year / "seed.pdf").write_text("seed")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0

        untouched = year / "late.pdf"
        untouched.write_text("financial data")
        untouched.chmod(0o644)
        os.utime(year, (1, 1))
        os.utime(tmp_path / "archive", (1, 1))
        env_extra, log = _chmod_logging_shim(tmp_path)

        payload = {"tool_name": "Bash", "tool_input": {"command": "pwd -P"}}
        r = self._run(tmp_path, payload, indent=2, env_extra=env_extra)

        assert r.returncode == 0, r.stderr
        calls = log.read_text() if log.exists() else ""
        assert "archive/2024" not in calls, (
            "a read-only command walked the archive — the payload was read as "
            "malformed")
        assert _mode(untouched) == 0o644

    @needs_jq
    def test_archive_refresh_roots_stay_an_antichain(self, tmp_path):
        """No tracked refresh root may contain another.

        Both index refreshes append `find <root>` for every root they hold, so
        two nesting roots put every path under the inner one into
        `.archive-dirs` and `.archive-files` TWICE. The directory poll then
        stats and re-hardens that subtree twice per call, and each later partial
        refresh copies both copies forward — the index grows instead of
        converging.

        So the covering test has to run in BOTH directions. Checking only
        whether an existing root already contains the new tree leaves
        `archive/2024/documents` recorded and `archive/2024` appended beside it.

        Child-then-parent is arranged deterministically rather than left to
        readdir order: the shallow directory poll runs before the payload
        branch, so a changed nested directory is registered first, and a Bash
        payload naming its year literally then registers the parent. Both are
        ordinary bounded paths — this is not an exotic sequence.
        """
        documents = tmp_path / "archive" / "2024" / "documents"
        documents.mkdir(parents=True)
        (documents / "seed.pdf").write_text("seed")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0

        # Only documents/ looks changed to the poll; the year around it is aged
        # past the index's one-second overlap so the poll cannot reach it first.
        for directory in (tmp_path / "archive", tmp_path / "archive" / "2024"):
            os.utime(directory, (1, 1))
        now = time.time()
        os.utime(documents, (now, now))

        bounded = {"tool_name": "Bash",
                   "tool_input": {
                       "command": "printf x > archive/2024/reviews/out.md"}}
        assert self._run(tmp_path, bounded).returncode == 0

        for index in (".archive-dirs", ".archive-files"):
            raw = (tmp_path / ".claude" / index).read_bytes()
            entries = [e for e in raw.split(b"\0") if e]
            duplicated = {e for e in entries if entries.count(e) > 1}
            assert not duplicated, (
                f"{index} lists {len(duplicated)} path(s) twice — two nesting "
                "refresh roots were both rebuilt into it")

    @needs_jq
    def test_opaque_bash_does_not_rechmod_an_already_hardened_archive(self,
                                                                      tmp_path):
        """The fallback's job is to establish that nothing under archive/ is
        readable by anyone but its owner — NOT to re-apply a mode a file already
        carries.

        As `harden_full_tree archive` it re-chmodded the entire filing history
        after every opaque call, which is nearly every command AGENTS.md §7
        documents, and no assertion here noticed. Asking find for the exposures
        costs the same single traversal and zero chmod processes when the tree
        is already correct.
        """
        year = tmp_path / "archive" / "2025"
        year.mkdir(parents=True)
        for i in range(30):
            (year / f"artifact-{i}.pdf").write_text("financial data")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        # Age the walked tree past the index's deliberate one-second overlap.
        # Files written in the same tick as the bootstrap are legitimately
        # "changed" to the shallow poll, which would harden the year again for
        # a reason that has nothing to do with the branch under test.
        for directory in (tmp_path / "archive", year):
            os.utime(directory, (1, 1))

        env_extra, log = _chmod_logging_shim(tmp_path)
        quiet = self._run(tmp_path, self.OPAQUE_BASH, env_extra=env_extra)
        assert quiet.returncode == 0, quiet.stderr
        calls = log.read_text() if log.exists() else ""
        assert "archive/2025" not in calls, (
            "an opaque command re-chmodded a correctly-moded archive")

        # The counterweight: a scan that found nothing must still be a scan.
        log.unlink(missing_ok=True)
        exposed = year / "late.pdf"
        exposed.write_text("financial data")
        exposed.chmod(0o644)
        os.utime(year, (1, 1))
        os.utime(tmp_path / "archive", (1, 1))

        repaired = self._run(tmp_path, self.OPAQUE_BASH, env_extra=env_extra)
        assert repaired.returncode == 0, repaired.stderr
        assert _mode(exposed) == 0o600, (
            "the exposure scan stopped finding exposures, which is the same "
            "observable state as the optimization above working perfectly")

    @needs_jq
    def test_an_exposed_file_under_a_mode_600_archive_directory_is_repaired_same_call(
            self, tmp_path):
        """A directory this user can READ but not execute is one find cannot
        descend into, and BSD find — the find macOS ships — reports that with
        exit status 0, an empty stderr, and the directory itself printed. The
        scan therefore hands back a clean archive while a 644 file sits one
        level below it, and no exit status anywhere says otherwise. The re-walk
        that catches it is what this drives.

        The first half is not a warm-up. Without it this passed unchanged
        against the hook it replaced, whose opaque branch was
        `harden_full_tree archive` — a walk that re-chmods the entire filing
        history on every opaque call and so repairs the file below by a route
        that involves no scan at all. Establishing FIRST that an
        already-correct archive costs zero chmods under `archive/2025` is what
        makes the repair in the second half attributable to the mechanism this
        test names.
        """
        year = tmp_path / "archive" / "2025"
        year.mkdir(parents=True)
        for i in range(5):
            (year / f"seed-{i}.md").write_text("financial data")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        # Age past the index's deliberate one-second overlap: a file written in
        # the same tick as the bootstrap is legitimately "changed" to the
        # shallow poll, which would harden the year again for a reason that has
        # nothing to do with the branch under test.
        for directory in (tmp_path / "archive", year):
            os.utime(directory, (1, 1))

        env_extra, log = _chmod_logging_shim(tmp_path)
        quiet = self._run(tmp_path, self.OPAQUE_BASH, env_extra=env_extra)
        assert quiet.returncode == 0, quiet.stderr
        assert "archive/2025" not in (log.read_text() if log.exists() else ""), (
            "a correctly-moded archive was re-chmodded, so the repair below "
            "would be the full walk's work and not the scan's")

        reviews = year / "reviews"
        reviews.mkdir()
        hidden = reviews / "2025-review.md"
        hidden.write_text("financial data")
        hidden.chmod(0o644)
        reviews.chmod(0o600)
        for directory in (tmp_path / "archive", year):
            os.utime(directory, (1, 1))

        try:
            r = self._run(tmp_path, self.OPAQUE_BASH, env_extra=env_extra)

            assert r.returncode == 0, r.stderr
            assert _mode(reviews) == 0o700
            assert _mode(hidden) == 0o600, (
                "the scan could not descend and stopped there")
        finally:
            # A guard mutation leaves this at 600; keep the failure path from
            # stranding pytest's temp tree in an unremovable state.
            reviews.chmod(0o700)

    @needs_jq
    def test_the_exposure_scan_teaches_the_archive_file_index(self, tmp_path):
        """The differential case, and the only shape of it that discriminates.

        An earlier version of this test added the late file ALREADY EXPOSED, so
        the scan found it, repaired it, and registered its parent on the way
        past. That passes equally against the hook this replaced — whose opaque
        branch walked and re-chmodded the whole history and rebuilt both
        indexes as a side effect — so it pinned nothing about the scan, while
        its docstring claimed to.

        The subtree that discriminates is the one arriving ALREADY CORRECT with
        directory mtimes preserved: `rsync -a`, `tar -p`, a restore from
        backup. Nothing is exposed, so the exposure list comes back EMPTY; the
        mtime poll has nothing to notice; and unless the scan marks the index
        dirty regardless of what it found, the new file enters NEITHER index
        and the `chmod 644` in the last step is invisible to every later pass.

        Two assertions are therefore load-bearing in opposite directions. The
        zero-chmod one says the work was done by the scan and not by the walk
        it replaced; the index one says the scan still teaches what the walk
        used to teach for free.
        """
        year = tmp_path / "archive" / "2024"
        year.mkdir(parents=True)
        (year / "a.pdf").write_text("financial data")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        index = tmp_path / ".claude" / ".archive-files"
        assert b"archive/2024/a.pdf" in index.read_bytes().split(b"\0"), (
            "the bootstrap never built an index, so nothing below measures one")

        # The arrival: correct modes throughout, and every EXISTING directory's
        # mtime put back. The new directory's own mtime is current and cannot
        # be, which is exactly the point — the poll walks the directories the
        # index already lists, and this one is not among them.
        documents = year / "documents"
        documents.mkdir(mode=0o700)
        late = documents / "b.pdf"
        late.write_text("financial data")
        late.chmod(0o600)
        for directory in (tmp_path / "archive", year):
            os.utime(directory, (1, 1))

        env_extra, log = _chmod_logging_shim(tmp_path)
        arrival = self._run(tmp_path, self.OPAQUE_BASH, env_extra=env_extra)
        assert arrival.returncode == 0, arrival.stderr
        assert "archive/2024" not in (log.read_text() if log.exists() else ""), (
            "the pass re-chmodded a correctly-moded archive, so whatever "
            "refreshed the index was the full walk and not this scan")
        assert b"archive/2024/documents/b.pdf" in index.read_bytes().split(b"\0"), (
            "a subtree that arrived already correct entered neither index, so "
            "a later chmod inside it is invisible to every pass")

        # And the consequence, which is the only reason the index matters. Age
        # the new directory too: left with a current mtime it is one the poll
        # would visit, and a repair by the poll proves nothing about the file
        # manifest that is actually under test here.
        os.utime(documents, (1, 1))
        late.chmod(0o644)
        assert self._run(tmp_path, {
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "result.json")},
        }).returncode == 0
        assert _mode(late) == 0o600, (
            "the file manifest never learned this file exists, so the one "
            "exposure a directory-mtime poll structurally cannot see survived")

    @needs_jq
    def test_a_no_read_archive_directory_takes_the_full_walk_fallback(
            self, tmp_path):
        """Row one of the same table, and nothing pinned it before this.

        With no READ bit the scan's own `find` FAILS — exit 1, "Permission
        denied" on stderr — instead of returning a quietly incomplete list, so
        it is the guard BEFORE the loop that has to catch this row, not the
        untraversable-directory flag inside it. The two rows read as one claim
        until something drives both, and collapsing them into one is exactly
        what an earlier comment on this branch did.

        `harden_full_tree` is what repairs it, because `restore_owner_traversal`
        chmods each directory before find descends into it. That is the reason
        the fallback is a full walk rather than a second scan.

        The zero-chmod preamble is here for the same reason as in the sibling
        test above: a hook that walks and re-chmods all of archive/ on every
        opaque call repairs this file too, and would pass every assertion below
        without a scan existing at all.
        """
        year = tmp_path / "archive" / "2025"
        year.mkdir(parents=True)
        for i in range(5):
            (year / f"seed-{i}.md").write_text("financial data")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        for directory in (tmp_path / "archive", year):
            os.utime(directory, (1, 1))

        env_extra, log = _chmod_logging_shim(tmp_path)
        quiet = self._run(tmp_path, self.OPAQUE_BASH, env_extra=env_extra)
        assert quiet.returncode == 0, quiet.stderr
        assert "archive/2025" not in (log.read_text() if log.exists() else ""), (
            "a correctly-moded archive was re-chmodded, so the repair below "
            "would be the full walk's work rather than the fallback's")

        reviews = year / "reviews"
        reviews.mkdir()
        hidden = reviews / "2025-review.md"
        hidden.write_text("financial data")
        hidden.chmod(0o644)
        reviews.chmod(0o000)
        for directory in (tmp_path / "archive", year):
            os.utime(directory, (1, 1))

        try:
            r = self._run(tmp_path, self.OPAQUE_BASH, env_extra=env_extra)

            assert r.returncode == 0, r.stderr
            assert r.stderr == "", (
                "find's own 'Permission denied' reached the user, or the hook "
                "warned instead of repairing — this case has a remedy")
            assert _mode(reviews) == 0o700
            assert _mode(hidden) == 0o600, (
                "the failed scan was treated as an empty one")
        finally:
            # A guard mutation leaves this unreadable; keep a failure from
            # stranding pytest's temp tree in an unremovable state.
            reviews.chmod(0o700)

    def test_a_terminated_pass_leaves_no_in_repo_temp_candidate(self, tmp_path):
        """The three index/stamp candidates are `mktemp`'d INSIDE the repository,
        because each has to land on the same filesystem as the file it atomically
        replaces — which is exactly what puts them beyond $TEMP_DIR's removal.
        `.claude/.archive-files.XXXXXX` is a NUL-separated list of every path
        under archive/, and the registered hook timeout killing a slow pass
        mid-refresh is the routine outcome on a large archive, not the exotic one.

        What this pins is `cleanup` LISTING those candidates at all — it used
        to remove only the scratch directory — and not the TERM/INT/HUP trap
        beside it. Measured on this host (GNU bash 3.2.57, the /bin/bash macOS
        ships): a shell killed by an UNTRAPPED SIGTERM while waiting on a
        foreground command still runs its EXIT trap and still removes the
        candidate, exiting 143. The extra trap buys promptness and the hook's
        own exit status, not the removal, and a comment claiming otherwise
        sends the next reader to defend the wrong line.

        The one bash property that does shape the test: a TRAPPED signal is
        deferred until the foreground command returns, which is why the
        assertion waits for the process to be REAPED rather than firing
        straight after the kill. The `touch` shim holds the pass open at a
        moment when a candidate exists and has not yet been renamed.
        """
        year = tmp_path / "archive" / "2025"
        year.mkdir(parents=True)
        (year / "doc.pdf").write_text("financial data")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()

        shim = tmp_path / "bin"
        shim.mkdir()
        (shim / "touch").write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *.claude/.archive-dirs.*) /bin/sleep 3 ;;\n"
            "esac\n"
            f"exec {shutil.which('touch')} \"$@\"\n"
        )
        (shim / "touch").chmod(0o755)

        process = subprocess.Popen(
            ["bash", str(PERMS_HOOK)], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path),
                 "PATH": f"{shim}:{os.environ['PATH']}"})
        candidates = []
        deadline = time.time() + 15
        while time.time() < deadline and not candidates:
            candidates = list(dot_claude.glob(".archive-dirs.??????"))
            time.sleep(0.05)
        assert candidates, "no in-repo candidate ever existed to be cleaned up"
        process.terminate()
        process.communicate(timeout=30)

        leftovers = sorted(p.name for pattern in (".archive-dirs.??????",
                                                  ".archive-files.??????",
                                                  ".perms-stamp.??????")
                           for p in dot_claude.glob(pattern))
        assert not leftovers, (
            f"a killed pass left {leftovers} behind — untracked, unignorable "
            "by name, and one `git add .` from being committed")

    def test_a_successfully_installed_index_survives_the_cleanup_trap(self,
                                                                      tmp_path):
        """The counterweight, and the one that fails if the cleanup above is
        ever 'simplified' into a glob sweep of `.claude/`. `mv -f` CONSUMES the
        candidate name, so removing the tracked names can only ever find the
        candidate of a pass that died before its rename.

        Stated plainly, because a reader is owed it: this test CANNOT fail
        against a hook that cleans up nothing, and it passes unchanged against
        the commit that had no trap at all. It is not evidence that the trap
        exists — the test above is — it is evidence that the trap is not too
        greedy, which is the failure mode a cleanup acquires later, in an edit
        that looks like tidying."""
        year = tmp_path / "archive" / "2025"
        year.mkdir(parents=True)
        (year / "doc.pdf").write_text("financial data")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()

        assert self._run(tmp_path).returncode == 0
        for name in (".archive-dirs", ".archive-files", ".perms-stamp"):
            installed = dot_claude / name
            assert installed.is_file(), f"{name} was removed by the trap"
            assert _mode(installed) == 0o600, (
                f"{name} lists archived paths and must stay owner-only")
            assert not list(dot_claude.glob(f"{name}.??????")), (
                f"a finished pass left a {name} candidate beside the file it "
                "installed, so the tracking list is never being cleared and "
                "the trap's reach grows with every call")

    def test_a_full_archive_walk_does_not_also_run_the_manifest_scan(self,
                                                                     tmp_path):
        """The manifest scan exists to find the one thing a directory-mtime poll
        cannot see — a chmod on an existing archived file — and it pays for that
        with a python3 process and an lstat of the whole file manifest. Run
        unconditionally, as it was, it also ran straight after the full walk,
        searching for modes that walk had just finished guaranteeing absent.

        An invalid stamp rather than a missing one is what makes this
        discriminating: with no `.claude/` at all the file index is missing too,
        so the old unconditional call fell through to its own fallback and
        spawned nothing either.
        """
        year = tmp_path / "archive" / "2025"
        year.mkdir(parents=True)
        (year / "doc.pdf").write_text("financial data")
        (tmp_path / ".claude").mkdir()
        assert self._run(tmp_path).returncode == 0
        (tmp_path / ".claude" / ".perms-stamp").write_text("1\n")  # old format

        shim = tmp_path / "bin"
        shim.mkdir()
        log = tmp_path / "python.log"
        (shim / "python3").write_text(
            "#!/bin/sh\n"
            "printf 'manifest scan\\n' >> \"$PERMS_PY_LOG\"\n"
            f"exec {shutil.which('python3')} \"$@\"\n"
        )
        (shim / "python3").chmod(0o755)

        r = self._run(tmp_path, self.OPAQUE_BASH,
                      env_extra={"PERMS_PY_LOG": str(log),
                                 "PATH": f"{shim}:{os.environ['PATH']}"})

        assert r.returncode == 0, r.stderr
        assert not log.exists(), (
            "the manifest scan ran after a full walk had already set every mode")

    def test_the_hook_creates_nothing_in_a_workspace_without_dot_claude(self, tmp_path):
        """A permissions hook must not add files to a taxpayer's workspace. With
        no `.claude/` there is no stamp, so every pass is a full walk — correct,
        merely slower, which is the safe direction to degrade in."""
        (tmp_path / "result.json").write_text("{}")
        before = {p.name for p in tmp_path.iterdir()}
        assert self._run(tmp_path).returncode == 0
        assert {p.name for p in tmp_path.iterdir()} == before

    def test_the_generated_pdf_script_is_covered(self, tmp_path):
        """`generate_tax_pdf.py` is source by extension and a runtime artifact
        in fact: tax-generate writes it per workspace and it carries the
        taxpayer's NAME in its section headers. It is also the one runtime file
        the commit-time PII scan structurally cannot vet, because that scan
        matches SSN and credential SHAPES and a name has neither — so this hook
        is the only thing covering it."""
        f = tmp_path / "generate_tax_pdf.py"
        f.write_text("# section header with a taxpayer name")
        f.chmod(0o644)
        self._run(tmp_path)
        assert _mode(f) == 0o600

    def test_runtime_directories_are_hardened_recursively(self, tmp_path):
        """`tax-archive` moves documents/ and reviews/ BENEATH archive/, out of
        reach of any root-scoped rule — the same reason `.gitignore` needed an
        explicit `archive/` entry."""
        nested = tmp_path / "archive" / "2025" / "reviews"
        nested.mkdir(parents=True)
        doc = nested / "2025-review.md"
        doc.write_text("x")
        doc.chmod(0o644)
        self._run(tmp_path)
        assert _mode(doc) == 0o600
        assert _mode(nested) == 0o700, (
            "a directory needs its owner execute bit to stay traversable, so "
            "owner-rwx plus go-rwx is required and 600 would be a bug")

    @pytest.mark.parametrize("locked_level", ["root", "nested"])
    def test_runtime_directory_owner_traversal_is_restored_before_walk(
            self, tmp_path, locked_level):
        """Mode-600 directories must become walkable before find descends."""
        documents = tmp_path / "documents"
        documents.mkdir()
        locked = documents
        if locked_level == "nested":
            locked = documents / "locked"
            locked.mkdir()
        artifact = locked / "financial.pdf"
        artifact.write_text("financial data")
        artifact.chmod(0o644)
        locked.chmod(0o600)

        try:
            r = self._run(tmp_path)

            assert r.returncode == 0, r.stderr
            assert _mode(locked) == 0o700
            assert _mode(artifact) == 0o600
        finally:
            # A deliberate guard mutation leaves this directory at 600. Keep
            # the regression test's failure path from stranding pytest's temp
            # tree in an unremovable state.
            locked.chmod(0o700)

    @pytest.mark.parametrize("directory", ["withholding", ".planning"])
    def test_sensitive_planning_directories_are_hardened(self, tmp_path, directory):
        """Planning reports and agent handoffs can quote return context even
        though their filenames are not in the root sensitive-file allowlist."""
        nested = tmp_path / directory / "nested"
        nested.mkdir(parents=True)
        artifact = nested / "artifact.md"
        artifact.write_text("x")
        artifact.chmod(0o644)

        assert self._run(tmp_path).returncode == 0
        assert _mode(artifact) == 0o600
        assert _mode(nested) == 0o700

    def test_the_public_tax_law_cache_is_left_alone(self, tmp_path):
        """The counterweight. `tax_law_<year>.json` holds published rates and
        brackets — public figures, not taxpayer data — and is deliberately
        outside the list. Without this, the list could quietly grow to cover
        everything and the hook's claim about WHAT it protects stops being
        checkable."""
        f = tmp_path / "tax_law_2025.json"
        f.write_text("{}")
        f.chmod(0o644)
        self._run(tmp_path)
        assert _mode(f) == 0o644

    def test_an_empty_workspace_does_not_block(self, tmp_path):
        """This hook runs after EVERY Write, Edit and Bash call. A permissions
        hook that can fail a session gets switched off, and a switched-off hook
        protects nothing — so it exits 0 even with nothing to protect."""
        r = self._run(tmp_path)
        assert r.returncode == 0
        assert r.stderr.strip() == ""

    def test_a_nonexistent_project_dir_does_not_block(self, tmp_path):
        r = subprocess.run(
            ["bash", str(PERMS_HOOK)], text=True, capture_output=True,
            env=dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path / "gone")))
        assert r.returncode == 0


# The number vocabulary MEASURED COSTS writes its deltas in. Spelled out rather
# than in digits because the block's own prose rule forbids a bare `N tests`
# that is not one of its derived figures, and a delta is not one — so the
# paragraph carries words and this reads them back.
#
# It was closed at 999, on the ground that 999 was every figure that paragraph
# had ever held, and a vocabulary closed at what a corpus happens to contain is
# a guard that blocks the first honest refresh past it. Words are the ONLY
# accepted spelling here — the pair patterns take `[a-z -]` and nothing else —
# so a delta of a thousand or more had no writable form at all, in words or in
# digits, and the exit would have been to edit this test. That is the same
# gate-blocks-the-refresh shape `has lost` and the hundreds probe already
# record, and this is its third instance. It is not hypothetical either: the
# suite pair stands in the low hundreds, and the feature branch immediately
# before this one moved that row by nearly EIGHT hundred on its own.
_NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_NUMBER_WORDS.update({w: (i + 2) * 10 for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())})
_NUMBER_WORDS["none"] = 0


def _words_to_int(text: str):
    """`"two hundred and sixty-one"` -> 261, or None if it is not a number.

    None rather than an exception, so the caller reports "this phrase is not a
    figure I can read" against the sentence it came from instead of dying with a
    KeyError three frames down — a guard whose failure arrives as a different
    error reports the wrong reason for its own red.

    NEGATIVE is readable, and it was not: a module that loses tests moves a
    count DOWN, and every figure derived from the six could then only be written
    in a shape this reader rejects. So the guard blocked the honest refresh
    precisely when a count went the one direction nothing here had ever seen —
    and the only way out was to edit the test, which is the shape of a gate that
    gets reasoned past. `minus` is the word the block writes it with.
    """
    total = current = 0
    seen = False
    sign = 1
    for word in re.findall(r"[a-z]+", text.lower().replace("-", " ")):
        if word == "and":
            continue
        if word in ("minus", "negative") and not seen and sign == 1:
            sign = -1
            continue
        if word == "hundred":
            if not seen:
                return None             # `hundred and eight` is not a number
            current *= 100
        elif word == "thousand":
            if not seen:
                return None             # ...and neither is `thousand and one`
            # BANKED rather than multiplied in place, which is what `total` has
            # always been for and what nothing until now put anything into: the
            # hundreds that may follow a thousand belong to a fresh group, so
            # `three thousand three hundred and four` has to close 3000 before
            # `hundred` multiplies the 3 beside it.
            total += current * 1000
            current = 0
        elif word in _NUMBER_WORDS:
            current += _NUMBER_WORDS[word]
        else:
            return None
        seen = True
    return sign * (total + current) if seen else None


# The counts-ahead paragraph's per-module sentence, as ONE pattern. The control
# below drives it over a spelling the live block does not currently contain, and
# a control carrying its own copy of a regex passes against the copy while the
# audit runs on something else — this suite's most-repeated finding.
#
# `lost` beside `gained`, because the pattern could express only NON-NEGATIVE
# deltas while the assertion beside it demands `moved == now - sitting`. Delete
# three tests from a module and there is no accepted spelling: `has lost three`
# did not parse, and a bare `three (212 to 209)` parsed to +3 against a pair
# giving -3. The guard blocked the honest refresh in the one direction it had
# never seen, and the only exit was to edit the test.
#
# The DIRECTION word carries the sign and the digits carry the truth, so a pair
# whose words and brackets disagree is red either way — which is what keeps the
# new arm from being a second way to write a stale figure.
MODULE_PAIR = (r"{name} (?:has (?P<dir>gained|lost) )?(?P<words>[a-z -]+?) "
               r"\((?P<was>\d+) to (?P<now>\d+)\)")


def _pair_delta(match):
    """The signed delta a MODULE_PAIR-shaped match states, or None."""
    moved = _words_to_int(match.group("words"))
    if moved is None:
        return None
    return -moved if match.group("dir") == "lost" else moved


def _code_only(source: str) -> str:
    """`source` with its COMMENTS removed, line numbering preserved.

    The chain locator below is a regex over text, so it reads prose as though
    it were code. It fired on a comment in this very module — one explaining
    which spelling of the tests directory NOT to use, and therefore quoting it
    — and demanded a hook arm that would put the 115-second hooks module
    behind every edit anywhere under the suite. A gate firing on correct code
    gets reasoned past, and this one had an exact fix sitting one function
    away: the sibling rule for the hook's own `RUN_` flags had just been
    anchored for the same reason.

    Comments only. A docstring is a string token and stays, because a path
    named in one may be a real constant; the rule is narrow on purpose.

    By TOKEN, not by cutting at the first `#`: a `#` inside a string literal
    is not a comment, and a textual cut would corrupt the line it sits on.
    """
    import io
    import tokenize
    out, last = [], (1, 0)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):   # pragma: no cover
        return source
    for token in tokens:
        if token.type == tokenize.COMMENT:
            continue
        (srow, scol), (erow, ecol) = token.start, token.end
        out.append("\n" * (srow - last[0]))
        out.append(" " * (scol - (last[1] if srow == last[0] else 0)))
        out.append(token.string)
        last = (erow, ecol)
    return "".join(out)


def _path_constants(source: str) -> list[str]:
    """Repo paths named by a constant tuple or list of string literals.

    The narrow rule is the whole point. "Any string literal that happens to name
    an existing path" was measured first and recovers fifty-four more entries
    across these six modules — `.git`, `.venv`, `documents/`, a gitignore
    pattern, a fixture name, a path quoted inside an assertion message — and a
    mapping obliged to route all of that is a mapping nobody can keep true. This
    rule requires the literals to be COLLECTED into one constant and EVERY
    element to name a file that exists, which is what a swept document set looks
    like and what an incidental mention never is. Measured against the same six
    modules it recovers exactly one constant, `test_preflight.DOCUMENTS`, and no
    noise.

    A tuple mixing an existing path with a string that is not one recovers
    nothing rather than the subset: half a set read as a whole set is the
    silent-undercount this test exists to prevent, and reporting none of it is
    at least legible.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        seq = node.value
        if isinstance(seq, ast.Constant) and isinstance(seq.value, str):
            # A LONE string constant too, which is how one module names another:
            # `PREFLIGHT = "tests/test_preflight.py"`, joined as `REPO / module`
            # over a loop. That is the coverage test's own read of the other five
            # modules, and it was invisible to this locator — so the six arms
            # that had to select the hooks module were a hand edit nothing
            # required. Measured across the six: it recovers exactly those six
            # constants and nothing else, tuple rule and this one together.
            values, seq = [seq.value], ast.Tuple(elts=[seq])
        elif isinstance(seq, (ast.Tuple, ast.List)) and seq.elts:
            values = [e.value for e in seq.elts
                      if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        else:
            continue
        if len(values) != len(seq.elts):
            continue
        if all(v and not v.startswith("/") and (REPO / v).is_file()
               for v in values):
            found += values
    return found


class TestHookRegistration:
    """A correct script that settings.json does not name is dead code, and the
    failure is silent in both directions."""

    def _settings(self) -> dict:
        return json.loads(SETTINGS.read_text())

    def _post_tool_use(self) -> list:
        return self._settings()["hooks"]["PostToolUse"]

    def test_both_hook_scripts_are_executable(self):
        for script in (SKILL_HOOK, PERMS_HOOK):
            assert script.is_file(), f"{script} is missing"
            assert os.access(script, os.X_OK), (
                f"{script.name} is not executable — the hook fails to launch "
                "and Claude Code reports only a non-zero status")

    def test_every_registered_command_names_a_script_that_exists(self):
        for entry in self._post_tool_use():
            for hook in entry["hooks"]:
                found = re.findall(r"\.claude/hooks/([\w.-]+\.sh)", hook["command"])
                assert found, f"unrecognised hook command: {hook['command']}"
                for name in found:
                    assert (HOOKS_DIR / name).is_file(), (
                        f"settings.json runs .claude/hooks/{name}, which does "
                        "not exist — renaming a script without updating the "
                        "registration disables it with no error")

    def test_the_permissions_hook_also_matches_bash(self):
        """`result.json` and the summary PDF are produced by RUNNING a script,
        which a Write|Edit-only matcher never observes. Dropping Bash from the
        matcher would leave exactly the files the hook exists for untouched."""
        matchers = [e["matcher"] for e in self._post_tool_use()
                    if any("harden-runtime-perms" in h["command"]
                           for h in e["hooks"])]
        assert matchers, "the permissions hook is not registered at all"
        for m in matchers:
            assert "Bash" in m, f"matcher {m!r} misses script-written files"

    def test_the_skill_invariant_hook_also_matches_bash(self):
        """Shell helpers can rewrite skills without emitting Write/Edit."""
        matchers = [e["matcher"] for e in self._post_tool_use()
                    if any("skill-invariants" in h["command"]
                           for h in e["hooks"])]
        assert matchers, "the skill invariant hook is not registered at all"
        for matcher in matchers:
            assert "Bash" in matcher, (
                f"matcher {matcher!r} misses shell-written skill files")

    def test_the_permissions_hook_also_matches_notebook_edit(self):
        """NotebookEdit writes financial notebooks without using Write/Edit."""
        matchers = [e["matcher"] for e in self._post_tool_use()
                    if any("harden-runtime-perms" in h["command"]
                           for h in e["hooks"])]
        assert matchers
        for matcher in matchers:
            assert "NotebookEdit" in matcher, (
                f"matcher {matcher!r} misses notebook-written financial data")

    # The archive this timeout has to cover, CHOSEN rather than measured, and
    # written down so the projection below is arguable instead of opaque: a
    # decade of retained filing history, each year holding documents/,
    # summaries/, reviews/ and the return artifacts — order 300 entries a year.
    # It is also the size the permissions hook's own comments use for their
    # synthetic archive, so the two agree about what "large" means here.
    REFERENCE_ARCHIVE_ENTRIES = 3000
    # The registered timeout is WALL clock; the measurement below is CPU, which
    # is the only figure that survives a machine doing something else. This
    # bridges them. On this host, with one competing session, the same walk's
    # wall time ran four to five times its CPU time; 8x leaves room for a busier
    # one. Raising it is not free in the other direction — the margin is also
    # what gives the assertion teeth, and a per-file chmod regression (measured:
    # it roughly triples the slope) has to stay inside what this catches.
    FULL_WALK_WALL_OVER_CPU = 8

    def _full_walk_cpu_seconds(self, workspace: Path) -> float:
        """CPU seconds the permissions hook spends on one pass over `workspace`.

        RUSAGE_CHILDREN counts only children this process has REAPED, and
        `subprocess.run` waits, so the delta is exactly this hook's pass — no
        other subprocess runs inside the window.
        """
        shutil.rmtree(workspace / ".claude")
        (workspace / ".claude").mkdir()
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        subprocess.run(["bash", str(PERMS_HOOK)], stdin=subprocess.DEVNULL,
                       capture_output=True,
                       env=dict(os.environ, CLAUDE_PROJECT_DIR=str(workspace)))
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        return ((after.ru_utime - before.ru_utime)
                + (after.ru_stime - before.ru_stime))

    def test_the_permissions_hook_timeout_covers_a_full_archive_walk(self,
                                                                     tmp_path):
        """Three states still legitimately walk the whole filing history: an
        invalid or missing stamp, a missing file index, and a directory the scan
        could not descend into. A dynamic archive write no longer does — it
        takes the exposure scan, which is one traversal and no chmod processes
        when the tree is already correct — so what this covers is the RARE pass,
        which on a large archive is also the expensive one. A pass the timeout
        cancels mid-refresh leaves an index candidate behind and reports
        nothing, the same silent mode as a hook that never fired.

        This used to assert `t >= 60` against a registered 60, which is not an
        assertion at all: no value the file could hold made it red, and it read
        as coverage. So it now does what its sibling does for the skill hook —
        derives a floor from the work the hook actually performs — with two
        differences forced by the subject.

        First, the driving quantity is not knowable at test time. The skill hook
        runs a fixed module list, so its pass can be counted; this one runs over
        whatever archive the taxpayer has. `REFERENCE_ARCHIVE_ENTRIES` is
        therefore a stated assumption, and moving it is the honest way to
        disagree with this test.

        Second, cost is measured as CPU, not wall. Wall time is the quantity the
        timeout actually bounds, but it is also the one that a second session on
        the machine inflates by several times, and a headroom test that goes red
        because something else was compiling is a test people delete.
        `FULL_WALK_WALL_OVER_CPU` carries that conversion explicitly instead.

        The slope, not the average: a small empty-archive pass measures the
        hook's fixed cost and is subtracted, so the projection is the marginal
        cost of an archive entry rather than that plus a constant amortized over
        a deliberately small tree. Minimum of several runs, because scheduling
        noise only ever adds.

        What it does NOT pin: the repair did not change this hook's registered
        timeout, so this passes against the pre-repair tree too. The repair's own
        cost change — the dominant path no longer taking this walk at all — is
        pinned by `test_opaque_bash_does_not_rechmod_an_already_hardened_archive`.
        """
        timeouts = [h.get("timeout") for e in self._post_tool_use()
                    for h in e["hooks"] if "harden-runtime-perms" in h["command"]]
        assert timeouts, "the permissions hook is not registered at all"
        assert all(isinstance(t, (int, float)) for t in timeouts), (
            f"permissions-hook timeouts {timeouts} are not all numbers — an "
            "unregistered timeout is Claude Code's default, not no limit")

        # Two subtrees a year so the walk pays for nested directories, which is
        # where its per-entry cost lives: every directory is chmod'd twice, once
        # by the traversal repair and once by the materialized pass.
        empty = tmp_path / "empty"
        (empty / ".claude").mkdir(parents=True)
        (empty / "archive").mkdir()
        loaded = tmp_path / "loaded"
        (loaded / ".claude").mkdir(parents=True)
        entries = 1
        for year in range(2016, 2026):
            entries += 1
            for sub in ("documents", "summaries"):
                directory = loaded / "archive" / str(year) / sub
                directory.mkdir(parents=True)
                entries += 1
                for i in range(10):
                    artifact = directory / f"doc-{i}.pdf"
                    artifact.write_text("financial data")
                    artifact.chmod(0o644)
                    entries += 1

        fixed = min(self._full_walk_cpu_seconds(empty) for _ in range(2))
        walked = min(self._full_walk_cpu_seconds(loaded) for _ in range(3))
        # The walk must actually have happened, or the slope below is measuring
        # a hook that refused the tree and the projection is meaningless.
        exposed = [p for p in (loaded / "archive").rglob("*.pdf")
                   if _mode(p) != 0o600]
        assert not exposed, f"the pass left {len(exposed)} archived files exposed"

        per_entry = (walked - fixed) / (entries - 1)
        projected = per_entry * self.REFERENCE_ARCHIVE_ENTRIES
        floor = projected * self.FULL_WALK_WALL_OVER_CPU
        for t in timeouts:
            assert t >= floor, (
                f"the {t}s registered timeout does not cover a full walk of a "
                f"{self.REFERENCE_ARCHIVE_ENTRIES}-entry archive: measured "
                f"{per_entry * 1000:.2f}ms of CPU per entry over {entries} "
                f"entries projects to {projected:.1f}s of CPU, and "
                f"{self.FULL_WALK_WALL_OVER_CPU}x that is {floor:.1f}s of wall")

    def test_the_hook_runs_test_files_that_exist(self):
        """A renamed module makes pytest exit 4 — a USAGE error, not a failed
        assertion — so the hook reports "invariants FAILED" with a
        file-not-found tail on every skill edit, while `pytest tests/` stays
        fully green and CI never notices. Nothing else pins script → test file;
        every test in this module shims the interpreter, so no assertion
        otherwise exercises the real argument list."""
        script = SKILL_HOOK.read_text()
        paths = sorted(set(re.findall(r"tests/[\w./-]+\.py", script)))
        assert paths, "the hook names no test modules — it checks nothing"
        missing = [p for p in paths if not (REPO / p).is_file()]
        assert not missing, f"the hook runs {missing}, which do not exist"

    def test_only_the_registry_pair_is_deselected_and_both_ids_resolve(self):
        """Deselection is how a gate stops firing, so it is worth naming exactly.

        These two necessarily fail while a NEW skill is being written — the
        directory exists before its EXPECTED_SKILLS entry and its symlink do —
        and left in, the hook reports a failure on correct in-progress work for
        every edit until three more land. That is the gates-that-cry-wolf
        pattern, and the cost of it is an agent that learns to read past the
        hook's output. CI and the review still enforce both.

        A stale node id is the quiet failure mode: pytest ignores a `--deselect`
        that matches nothing, so a renamed test would silently come back into
        the hook without anyone choosing that.
        """
        import ast

        expected = {
            ("tests/test_portability.py", "TestSkillLayout",
             "test_all_expected_skills_present"),
            ("tests/test_portability.py", "TestClaudeSymlinks",
             "test_one_symlink_per_skill"),
        }
        found = set(re.findall(r"--deselect\s+(\S+?)::(\w+)::(\w+)",
                               SKILL_HOOK.read_text()))
        assert found == expected, (
            f"the hook deselects {sorted(found)}; deselecting anything beyond "
            "the registry pair silently removes a real lint from the gate")

        for path, cls, func in sorted(found):
            tree = ast.parse((REPO / path).read_text())
            classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
            assert cls in classes, f"{path} has no class {cls}"
            names = {n.name for n in classes[cls].body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert func in names, (
                f"{path}::{cls} has no {func} — the --deselect matches nothing "
                "and pytest ignores it in silence")

    def test_the_measured_costs_counts_match_live_collection(self):
        """The block's own rule, enforced instead of asked for.

        MEASURED COSTS says counts "are derivable in a second and there is no
        excuse for a stale one", and refreshing them by hand has failed at
        least four times — most recently on the branch that made the block the
        SOLE copy by deleting the figure from test-invariants.md, where it
        shipped a portability row five tests light and a suite row twenty. A
        reader following the pointer got a wrong figure with nothing to flag
        it, because nothing related the prose to the suite.

        COLLECTED, not run: collection is deterministic across machines while
        the run count subtracts platform skips that are not. The block states
        both, so its collected figures are what can be checked here — and the
        DERIVED rows are checked as arithmetic over the parts, which is the
        half the block calls exact and the half that caught the last drift.
        """
        block = SKILL_HOOK.read_text()
        # The block's own HEADER, not the first mention of its name. `index`
        # on "MEASURED COSTS" alone landed a thousand lines above it, on a
        # paragraph that CITES the block — so "inside the block" included most
        # of the script, and a figure planted there answered for one in the
        # table. The name is cited by design, repeatedly; the header sentence
        # is the thing that occurs once.
        header = "MEASURED COSTS. This block is the ONE owner"
        assert block.count(header) == 1, (
            f"the MEASURED COSTS header matches {block.count(header)} times — "
            "every derivation here is scoped by it, and at zero this test "
            "silently checks the wrong span of the file")
        start = block.index(header)
        end = block.index("Running the union of every changed path")
        assert end > start, (
            "the string this test slices the block's END at appears BEFORE "
            "its header, so the block reads as empty and every derivation "
            "below reports a missing table rather than the real cause — move "
            "the quotation or give the end a longer anchor")
        costs = block[start:end]
        # Where the live half ends. Below this the block quotes its own
        # superseded figures on purpose, so a check that reads past it reads a
        # figure a reader is explicitly told is stale as a current one.
        marker = "EVERYTHING FROM HERE TO THE END OF THIS BLOCK IS HISTORY"
        assert costs.count(marker) == 1, (
            f"the history banner appears {costs.count(marker)} times in the "
            "block; it is where the live half ends, and these derivations "
            "cannot be scoped without exactly one of it")
        live_costs = costs[:costs.index(marker)]

        # ONE collection, attributed per file from the node ids. This test runs
        # inside the module MEASURED COSTS calls the dominant share of a cold
        # pass,
        # and it used to shell out seven times — six per-module collections
        # plus a whole-suite one — adding that to every cold pass of the hook
        # it measures. The block names this module's runtime as the lever when
        # the budget gets tight; a test enforcing the block should not be
        # spending it.
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "--collect-only",
             "-q", "-p", "no:cacheprovider"],
            cwd=REPO, capture_output=True, text=True)
        found = re.search(r"(\d+) tests? collected", out.stdout)
        assert found, f"could not collect tests/: {out.stdout[-400:]}"
        suite_collects = int(found.group(1))
        live: dict = {}
        for line in out.stdout.splitlines():
            node = line.strip()
            if "::" not in node:
                continue
            path = node.split("::", 1)[0]
            if path.startswith("tests/") and path.endswith(".py"):
                live[path] = live.get(path, 0) + 1
        assert sum(live.values()) == suite_collects, (
            f"node-id attribution sums to {sum(live.values())} against a "
            f"reported {suite_collects} — the per-module figures below would "
            "be derived from an incomplete listing")

        # "collects N and runs M" — the block's own reconciliation sentences.
        # Whitespace is flattened first so a REWRAPPED sentence still matches:
        # `.` does not cross newlines, and the fallback that used to cover that
        # built a str->str dict, so `stated_collects["hooks"] == live[...]`
        # compared "125" to 125 and could never hold — a red suite reporting
        # two identical numbers as a mismatch. `[^.]*?` keeps each match inside
        # one sentence so the two modules cannot be paired with each other's
        # figure.
        # Comment markers stripped BEFORE flattening: `\s+ -> " "` alone
        # leaves the per-line "#", so a sentence wrapped mid-phrase flattens to
        # "a cold # pass" and no phrase regex can match across the wrap.
        flat = re.sub(r"\s+", " ", re.sub(r"^#[ \t]?", "", costs, flags=re.M))
        stated_collects: dict = {}
        for mod, n in re.findall(r"(portability|hooks)\b[^.]*?collects (\d+)",
                                 flat):
            stated_collects.setdefault(mod, int(n))
        assert set(stated_collects) == {"portability", "hooks"}, (
            "the block's reconciliation sentences for the portability and "
            f"hooks modules no longer parse (found {sorted(stated_collects)}) "
            "— widen the pattern; a sentence this test cannot read is a "
            "figure nothing checks")
        # The per-module decomposition rows, parsed ONCE into all three of
        # their fields: "tests/test_x.py   N tests   A - Bs". The counts and
        # the seconds used to be two passes over the same table, which is two
        # patterns to keep matching one table. A dict keeps the LAST of two
        # matches, so a duplicate row silently replaces the first here and in
        # every figure derived from it — rejected outright rather than
        # tolerated, in the live half only.
        # THE TWO TABLES, PARSED ONCE, AS TABLES. Every earlier draft found a
        # row by searching the block's TEXT for what it hoped a row said, and
        # five review rounds found five ways for a row to stop saying it while
        # a "Superseded, do not use" copy nearby answered in its place: an en
        # dash, doubled spaces, backticks around the path, a changed indent, a
        # respelled label. Each fix taught the pattern one more spelling, which
        # is a game with no last move.
        #
        # A table is not a set of sentences that happen to match. It is a
        # CONTIGUOUS RUN OF LINES, and the live half holds exactly two of them
        # — the trigger table's five rows and the decomposition's seven. Found
        # that way, a quotation is outside both BY CONSTRUCTION however it is
        # spelled, and a row that stops parsing shortens the run it was in
        # instead of quietly leaving it. Every figure below is read from these
        # records and nothing searches the block for a row again.
        TABLE_ROW = re.compile(
            r"^#   (\S.*?)\s\s+(\d+) tests\s+"
            r"(\d+(?:\.\d+)?) - (\d+(?:\.\d+)?)s\s+\d+\s*$")
        # The fast path runs no pytest, so it states no count. It is a row of
        # the trigger table all the same, and leaving it out of the run would
        # put a hole in the middle of one.
        FAST_ROW = re.compile(
            r"^#   (\S.*?)\s\s+no pytest\s+"
            r"(\d+(?:\.\d+)?) - (\d+(?:\.\d+)?)s\s+\d+\s*$")
        # WHERE THE TABLES ARE, decided by text that is NOT a row. Discovering
        # them as "the runs of lines that parse" was still the same mistake in
        # its last costume: reindent all five trigger rows by one space and the
        # table stops being a run at all, leaving a quoted copy to be found
        # instead. A row cannot be identified by its own willingness to parse.
        #
        # Each table sits between two blank comment lines, under a heading that
        # is prose. Those are the boundaries — found without looking at a row —
        # and every line between them is then REQUIRED to parse. A reindented
        # row is still inside the region and fails; a quotation is outside both
        # regions and is not read at all.
        def _rows_under(anchor, what, blank_between):
            assert live_costs.count(anchor) == 1, (
                f"the {what} is located by {anchor!r}, which appears "
                f"{live_costs.count(anchor)} times above the history banner")
            at = live_costs.index(anchor)
            # The trigger table's rows begin on the line after its rule; the
            # decomposition's begin after the blank line that closes the
            # paragraph introducing it. Both END at the next blank line.
            start = (live_costs.index("\n#\n", at) + len("\n#\n")
                     if blank_between
                     else live_costs.index("\n", at) + 1)
            end = live_costs.index("\n#\n", start)
            return live_costs[start:end].split("\n")

        trigger_lines = _rows_under(
            "#   ------------------------", "trigger table", False)
        decomposition_lines = _rows_under(
            "survives a change of machine.", "decomposition", True)
        assert [len(trigger_lines), len(decomposition_lines)] == [5, 7], (
            f"the trigger table holds {len(trigger_lines)} lines and the "
            f"decomposition {len(decomposition_lines)}; they hold five and "
            "seven. A line spliced into either — a quotation, a note, a "
            "heading — is a line this test will then require to be a row:\n"
            + "\n".join(trigger_lines + decomposition_lines))
        table_runs = [trigger_lines, decomposition_lines]

        triggers: dict = {}
        for ln in table_runs[0]:
            counted = TABLE_ROW.match(ln)
            if counted:
                triggers[counted.group(1)] = (
                    int(counted.group(2)),
                    (float(counted.group(3)), float(counted.group(4))))
            else:
                fast = FAST_ROW.match(ln)
                triggers[fast.group(1)] = (
                    None, (float(fast.group(2)), float(fast.group(3))))
        # The labels are asserted as a SET, because looking one up by name is
        # how a renamed row becomes a row nothing reads. Renaming one is a
        # change to the block's shape and belongs with a sitting, not with a
        # round that happens to reword a line.
        countless = sorted(k for k, (n, _) in triggers.items() if n is None)
        assert countless == ["nothing watched changed"], (
            f"{countless} state no count. The fast-path row is the only row in "
            "either table that may — it runs no pytest — and a counted row "
            "that stops stating its count stops being checked against a "
            "collection")
        assert set(triggers) == {
            "nothing watched changed", "agent-definition edit",
            "skill .md edit", "cold pass, all seven modules",
            "(for scale) `pytest tests/`"}, (
            f"the trigger table's rows are {sorted(triggers)} — every figure "
            "derived below is looked up by those labels, and a renamed one is "
            "a row nothing checks")

        rows: dict = {}
        secs: dict = {}
        for ln in table_runs[1]:
            row = TABLE_ROW.match(ln)
            assert row, (
                f"a decomposition row states no count: {ln.strip()!r} — the "
                "fast-path row is the only row in this block that may, and it "
                "is in the other table")
            path = row.group(1)
            assert re.fullmatch(r"tests/test_\w+\.py", path), (
                f"the decomposition names {path!r}, which is not a test module "
                "path — every row here is a module the hook can select, and a "
                "label that is not one is a row keyed to nothing")
            assert path not in rows, (
                f"{path} has two decomposition rows")
            rows[path] = int(row.group(2))
            secs[path] = (float(row.group(3)), float(row.group(4)))

        # Portability's gap is the hook's own --deselect flags: derivable, so
        # it is derived rather than trusted from the prose.
        deselects = len(re.findall(r"--deselect\s+tests/test_portability\.py",
                                   block))
        missing = sorted(p for p in rows if p not in live)
        assert not missing, (
            f"the decomposition names {missing}, which the suite collection "
            "did not produce — a renamed or deleted module")
        assert rows["tests/test_portability.py"] == (
            live["tests/test_portability.py"] - deselects), (
            f"portability collects {live['tests/test_portability.py']} and the "
            f"hook deselects {deselects}, so the row should read "
            f"{live['tests/test_portability.py'] - deselects}, not "
            f"{rows['tests/test_portability.py']}")
        for path, count in rows.items():
            if path in ("tests/test_portability.py", "tests/test_claude_hooks.py"):
                continue   # gaps handled above / platform-dependent below
            assert count == live[path], (
                f"{path} collects {live[path]}; the block says {count}")
        # Both reconciliation sentences, not just the one that happened to
        # parse: `(portability|hooks) module` never matched "portability
        # collects 150 and runs 148" — it says no "module" — so the guard that
        # used to sit here (`if "hooks" in stated_collects`) checked one of the
        # two and silently skipped the other, on top of never being able to
        # hold at all once the str-valued fallback supplied the value.
        assert stated_collects["hooks"] == live["tests/test_claude_hooks.py"], (
            "the block's stated hooks-module COLLECTED figure is stale: "
            f"{stated_collects['hooks']} against {live['tests/test_claude_hooks.py']}")
        assert stated_collects["portability"] == live["tests/test_portability.py"], (
            "the block's stated portability COLLECTED figure is stale: "
            f"{stated_collects['portability']} against "
            f"{live['tests/test_portability.py']}")

        # The derived rows. A skill edit selects five of the seven; a cold pass
        # adds the other two. The block calls this half EXACT, and a
        # decomposition error shows up here first.
        #
        # The hooks module and the host guard are what a skill edit does NOT buy:
        # an `agents/lib/` edit selects the guard, skill prose never does, and no
        # skill edit can turn a hook assertion red. The agents module was on this
        # side of the line until V6 put a boundary claim — "this skill names the
        # enforced worker, and its sequential path says the boundary was NOT
        # enforced" — into three skills, at which point a skill edit could turn
        # it red and had to run it.
        #
        # This tuple is a hand-kept second copy of `select_modules_for`'s arms,
        # and it is the reason this assertion caught the change rather than
        # riding along with it. Keep it that way: derived from the hook, it would
        # agree with the hook by construction and check nothing.
        ADDED_BY_A_COLD_PASS = ("tests/test_claude_hooks.py",
                                "tests/test_host_guard.py")
        skill_edit = sum(rows[p] for p in rows
                         if p not in ADDED_BY_A_COLD_PASS)
        cold = sum(rows.values())
        stated_skill = triggers["skill .md edit"][0]
        stated_cold = triggers["cold pass, all seven modules"][0]
        assert stated_skill == skill_edit, (
            f"the skill-edit row reads {stated_skill}; its five parts sum to "
            f"{skill_edit}")
        assert stated_cold == cold, (
            f"the cold-pass row reads {stated_cold}; its seven parts sum to "
            f"{cold}")

        # The ARITHMETIC sentence, which is the block's own third statement of
        # these two rows and was the half nothing read. It shipped reading
        # `177 + 116 + 322 + 138 = 753 ... 124 + 36 = 913` against a table
        # already saying 350 and 37 — under a clause promising it "moves with
        # the table now", which is a promise a person keeps rather than a check.
        # A reader deriving a trigger's cost from the prose was out by twenty-one
        # tests, and the next refresh would have inherited three values for one
        # quantity, which is the state this whole block exists to end.
        #
        # The PARTS are checked and not only the sums, because a sentence whose
        # parts are stale and whose total is fresh is the shape that reads
        # correct: it is what a hand patch of the total produces.
        sums = re.search(r"EXACT: ([\d +]+?) = (\d+) for a skill edit, plus "
                         r"([\d +]+?) = (\d+) for a cold pass", flat)
        assert sums, (
            "the block no longer states its derived rows as `EXACT: a + b + c "
            "+ d + e = <skill edit> for a skill edit, plus f + g = <cold pass> "
            "for "
            "a cold pass` — reword around that shape rather than through it, "
            "or this sentence goes back to being prose nothing reads")
        four = sorted(int(n) for n in sums.group(1).split("+"))
        added = sorted(int(n) for n in sums.group(3).split("+"))
        by_a_skill_edit = sorted(rows[p] for p in rows
                                 if p not in ADDED_BY_A_COLD_PASS)
        assert four == by_a_skill_edit, (
            f"the arithmetic sentence adds {four}; the five rows a skill edit "
            f"selects are {by_a_skill_edit}")
        assert added == sorted(rows[p] for p in ADDED_BY_A_COLD_PASS), (
            f"the arithmetic sentence adds {added} for the two a cold pass "
            f"adds; their rows are "
            f"{sorted(rows[p] for p in ADDED_BY_A_COLD_PASS)}")
        assert int(sums.group(2)) == skill_edit, (
            f"the arithmetic sentence totals the skill edit at {sums.group(2)}; "
            f"its own parts sum to {skill_edit}")
        assert int(sums.group(4)) == cold, (
            f"the arithmetic sentence totals the cold pass at {sums.group(4)}; "
            f"its own parts sum to {cold}")

        stated_suite = triggers["(for scale) `pytest tests/`"][0]
        # THE HOOKS ROW, which is the one row no collection can reach and was
        # therefore the one nothing checked. Every other row is pinned to a live
        # count; this module's RUN figure is its collection minus platform skips,
        # and skips are conditional — a case-sensitive filesystem takes two of
        # them and a case-insensitive one takes two others — so only a real run
        # could measure it, and a real run is what this test exists not to do.
        #
        # So it is pinned to the block's own stated skip count instead, from BOTH
        # sides: the reconciliation sentence must be internally consistent, and
        # the scale row's gap must be that same number. That replaces a `0 <= gap
        # <= 4` tolerance whose stated purpose was exactly these skips — a
        # tolerance for one unknown quietly covering a second thing, which is the
        # defect the sentence above it already records once.
        hook_skips = re.search(
            r"the hooks module holds ([a-z -]+?) platform skips", flat)
        assert hook_skips, (
            "the block no longer states how many platform skips the hooks "
            "module holds — that figure is what makes its RUN row derivable, "
            "and without it the row a cold pass is summed from is unchecked")
        skips = _words_to_int(hook_skips.group(1))
        assert skips is not None, (
            f"the stated skip count {hook_skips.group(1)!r} is not a number "
            "this test can read — spell it in full words")
        assert rows["tests/test_claude_hooks.py"] == (
            stated_collects["hooks"] - skips), (
            f"the hooks row reads {rows['tests/test_claude_hooks.py']}; the "
            f"block says it collects {stated_collects['hooks']} and holds "
            f"{skips} platform skips, which is "
            f"{stated_collects['hooks'] - skips}")
        # The SUITE's skips are a different figure from the hooks module's, and
        # conflating them held only while that module was the only place a test
        # could be skipped. W6's Windows-only DACL cases are the first outside
        # it, so the block states both and this reads the one it is asking about.
        suite_skips_stated = re.search(
            r"THE SUITE holds ([a-z -]+?) skips", flat)
        assert suite_skips_stated, (
            "the block no longer states how many tests the SUITE skips — that "
            "figure is what makes the scale row's RUN count derivable from a "
            "collection, and the hooks module's own count cannot stand in for "
            "it once anything else is conditionally skipped")
        suite_skips = _words_to_int(suite_skips_stated.group(1))
        assert suite_skips is not None, (
            f"the stated suite skip count {suite_skips_stated.group(1)!r} is "
            "not a number this test can read — spell it in full words")
        assert suite_skips >= skips, (
            f"the block says the suite skips {suite_skips} and the hooks module "
            f"alone skips {skips}; the module's skips are part of the suite's")
        gap = suite_collects - stated_suite
        assert gap == suite_skips, (
            f"`pytest tests/` collects {suite_collects}; the block's scale row "
            f"reads {stated_suite}, a gap of {gap} against the {suite_skips} "
            "skips it states — re-derive the whole block, do not patch this row")

        # ...and the tripwire under both, because the two assertions above are
        # over STATED figures and a skip added to an existing test moves neither.
        # Collection does not change, every count stays green, and the hooks row
        # and the cold pass summed from it drift — the count drift this block's
        # own header says has already happened four times, arriving through the
        # one quantity no collection can see. A skip construct is countable in
        # the source even though which of them FIRE is not, so the count is the
        # signal: when it moves, the stated figure has to be re-measured by a
        # real run rather than carried.
        markers = len(re.findall(r"pytest\s*\.\s*mark\s*\.\s*skipif\s*\(",
                                 (REPO / HOOKS).read_text()))
        calls = len(re.findall(r"pytest\s*\.\s*skip\s*\(",
                               (REPO / HOOKS).read_text()))
        assert (markers, calls) == (2, 3), (
            f"this module declares {markers} skipif markers and {calls} skip "
            f"calls, against the 2 and 3 the block's stated skip count was "
            "taken with. Which of them FIRE is platform-dependent and cannot be "
            "derived here, so re-run this module and re-derive its RUN row, the "
            "cold pass summed from it and the scale row's gap — a skip added to "
            "an EXISTING test moves no collected count and nothing else here "
            "would see it")
        # The scale row's own reconciliation sentence states the COLLECTED
        # figure, which is machine-independent and therefore pinned EXACTLY.
        # The tolerance above exists for the platform skips the RUN figure
        # subtracts, and it was wide enough to sit green on a row a test out of
        # date — a tolerance for one unknown quietly covering a second thing.
        stated_suite_collects = int(re.search(
            r"`pytest tests/` collects (\d+) and runs (\d+)", flat).group(1))
        stated_suite_runs = int(re.search(
            r"`pytest tests/` collects (\d+) and runs (\d+)", flat).group(2))
        assert stated_suite_collects == suite_collects, (
            f"the block says `pytest tests/` collects {stated_suite_collects}; "
            f"it collects {suite_collects}")
        assert stated_suite_runs == stated_suite, (
            f"the scale row reads {stated_suite} tests and the sentence beside "
            f"it says {stated_suite_runs} run — one quantity, two values")

        # PROSE counts too, not only the table. The rows were pinned while a
        # sentence two paragraphs down said the hooks module "spawns bash
        # processes across its 122 tests" against a row reading 123 — CLAUDE.md
        # makes this block the single owner of these figures precisely because
        # one quantity with two values is what the block was created to end,
        # and a regex that reads only the table leaves prose stale for ever.
        # Every "N tests" in the block must be a figure the block itself
        # derives; a historical aside states its unit some other way ("205
        # spawns over 105 cases") rather than being exempted here.
        allowed = set(rows.values()) | {skill_edit, cold, stated_suite}
        for stale in {int(n) for n in re.findall(r"(\d+) tests\b", costs)} - allowed:
            raise AssertionError(
                f"the block says \"{stale} tests\" somewhere, which is none of "
                f"its own derived figures {sorted(allowed)} — a prose count "
                "left behind by a table refresh")

        # THE COUNTS-AHEAD PARAGRAPH, which is prose and was therefore the half
        # nothing read. It shipped one quantity with two values — a headline
        # saying "two hundred and forty-eight tests ahead" over a parenthetical
        # giving 259 — green throughout, because everything above reads the
        # TABLE. Two of its per-module pairs were last-round deltas spliced into
        # a sentence whose stated baseline is the seconds sitting, so the six
        # could not be reconciled against the total at all. Every figure in it
        # is derivable from figures this test already has, so it is derived.
        sitting = "the counts the seconds were taken at"
        assert sitting in flat, (
            "the block no longer states its counts against the sitting the "
            f"seconds were taken at (looked for {sitting!r}) — that paragraph "
            "is where one quantity came to have two values, and this test "
            "reads it")
        suite_pair = re.search(
            r"the suite has (?P<dir>gained|lost) (?P<words>[a-z -]+?) tests "
            r"\((?P<was>\d+) to (?P<now>\d+)\)", flat)
        assert suite_pair, (
            "the counts-ahead paragraph no longer states the suite gap as "
            "`gained|lost <words> tests (<sitting> to <now>)` — reword around "
            "that shape rather than through it")
        gained, was, now = (_pair_delta(suite_pair),
                            int(suite_pair.group("was")),
                            int(suite_pair.group("now")))
        assert gained is not None, (
            f"the suite gap {suite_pair.group('words')!r} is not a number this "
            "test can read — spell it in full words")
        assert now == stated_suite, (
            f"the counts-ahead paragraph says the suite now runs {now}; the "
            f"scale row says {stated_suite}")
        assert gained == now - was, (
            f"the paragraph says the suite gained {gained} tests and its own "
            f"pair gives {now - was} ({was} to {now}) — the headline figure "
            "left behind by a table refresh, which is exactly what shipped")

        # Every per-module pair, measured from the SITTING. The right-hand side
        # of each is a row this test already collected, so a refreshed table
        # with a stale paragraph is red here.
        by_name = {"portability": "tests/test_portability.py",
                   "preflight": "tests/test_preflight.py",
                   "the agents module": "tests/test_claude_agents.py",
                   "secure_fs": "tests/test_secure_fs.py",
                   "the hooks module": "tests/test_claude_hooks.py",
                   "capabilities": "tests/test_capabilities.py",
                   "the host guard": "tests/test_host_guard.py"}
        deltas = {}
        for name, path in by_name.items():
            pair = re.search(MODULE_PAIR.format(name=name), flat)
            assert pair, (
                f"the counts-ahead paragraph states no `{name} <words> "
                "(<sitting> to <now>)` pair — all seven modules are stated so "
                "the total can be reconciled against them")
            moved = _pair_delta(pair)
            assert moved is not None, (
                f"{name}'s delta {pair.group('words')!r} is not a number this "
                "test can read — spell it in full words")
            assert int(pair.group("now")) == rows[path], (
                f"the paragraph says {name} now runs {pair.group('now')}; its "
                f"row says {rows[path]}")
            assert moved == int(pair.group("now")) - int(pair.group("was")), (
                f"the paragraph says {name} moved by {moved} and its own pair "
                f"gives {int(pair.group('now')) - int(pair.group('was'))} — a "
                "last-round delta on a sitting-relative baseline, which is the "
                "second thing that shipped wrong here. A module that SHRANK is "
                "written `has lost <words>`; the bare form reads as a gain")
            deltas[name] = moved

        # The pair pattern must read a count of a HUNDRED or more, which is
        # written "one hundred and two" and which `[a-z-]+` — one hyphenated
        # word — matched no part of. The pair then failed to parse at all and
        # the assertion above blamed the paragraph for a shape it had written
        # correctly, on exactly the round where a module moved far enough for it
        # to matter. The suite-gap pattern beside it has taken spaces since it
        # was written, so one prose convention had two readings. No module has
        # moved that far yet, so only a probe can exercise it.
        probe = re.search(MODULE_PAIR.format(name="preflight"),
                          "preflight has gained one hundred and two (289 to 391)")
        assert probe and _pair_delta(probe) == 102, (
            "the per-module pair pattern cannot read a count of a hundred or "
            f"more: it read {probe.group('words')!r}" if probe else
            "the per-module pair pattern did not match a hundreds-spelled count")

        # ...and the other direction, which the pattern could not express at
        # all. A module that loses tests moved a count DOWN, `has lost` did not
        # parse, and the bare spelling parsed to a POSITIVE delta against a pair
        # that gives a negative one — so the refresh had no accepted shape and
        # the only exit was to edit this test. No module has shrunk yet, so a
        # probe is the only way to exercise it, and the negative sum below is
        # the same construct one sentence along.
        shrunk = re.search(MODULE_PAIR.format(name="portability"),
                           "portability has lost three (212 to 209)")
        assert shrunk and _pair_delta(shrunk) == -3, (
            "the per-module pair pattern cannot express a module that SHRANK: "
            f"it read {shrunk.group('words')!r}" if shrunk else
            "the per-module pair pattern did not match `has lost <words>`")
        assert _words_to_int("minus three") == -3, (
            "a negative total is unwritable, so a round in which the six lose "
            "more than they gain cannot be reconciled in any accepted shape")

        # ...and a THOUSAND, which the vocabulary below was closed under and
        # which no digit spelling could stand in for: both pair patterns take
        # `[a-z -]` for the delta, so a gap of four figures had no accepted
        # shape at all and the only exit was to edit this test. The SUITE pair
        # is where it lands first — the feature branch before this one moved
        # that row by nearly eight hundred on its own — so it is probed on the
        # suite shape as well as the module one.
        big = re.search(
            r"the suite has (?P<dir>gained|lost) (?P<words>[a-z -]+?) tests "
            r"\((?P<was>\d+) to (?P<now>\d+)\)",
            "the suite has gained one thousand and twelve tests (3010 to 4022)")
        assert big and _pair_delta(big) == 1012 == (
            int(big.group("now")) - int(big.group("was"))), (
            "the suite pair cannot read a gap of a thousand or more: "
            f"{big.group('words')!r} -> {_pair_delta(big)}" if big else
            "the suite pair pattern did not match a thousands-spelled gap")
        thousands = re.search(MODULE_PAIR.format(name="preflight"),
                              "preflight has gained three thousand three "
                              "hundred and four (289 to 3593)")
        assert thousands and _pair_delta(thousands) == 3304, (
            "the per-module pair pattern cannot read a thousands-spelled "
            f"delta: it read {thousands.group('words')!r} -> "
            f"{_pair_delta(thousands)}" if thousands else
            "the per-module pair pattern did not match a thousands-spelled "
            "delta")
        assert _words_to_int("minus one thousand and twelve") == -1012, (
            "a four-figure SHRINK has no accepted spelling either, and the six "
            "reconcile against the suite gap as a signed sum")
        assert _words_to_int("thousand and one") is None, (
            "a bare `thousand` reads as a number, so a phrase that is not one "
            "would be reported as a stale figure rather than as unreadable")

        summed = re.search(r"They sum to ([a-z -]+?);", flat)
        remaining = re.search(r"the remaining ([a-z -]+?) are modules", flat)
        assert summed and remaining, (
            "the paragraph no longer reconciles its six pairs against the "
            "suite gap (`They sum to <words>; the remaining <words> are "
            "modules this hook does not select`) — that sentence is what makes "
            "the six checkable as a set rather than one at a time")
        assert _words_to_int(summed.group(1)) == sum(deltas.values()), (
            f"the paragraph says the six sum to {summed.group(1)!r}; they sum "
            f"to {sum(deltas.values())}")
        # The SAME remainder is stated twice in that paragraph — once in the
        # reconciling sentence and once in the prose list of unselected
        # modules — and only the first was read. They diverged by thirty-eight
        # and three successive rounds stepped past it, because the half
        # nothing reads is the half that drifts. Both are read now.
        prose_remainder = re.search(r"the other ([a-z -]+?) in test_", flat)
        assert prose_remainder, (
            "the paragraph no longer says `the other <words> in test_...` — "
            "that phrase is the second statement of the remainder, and it is "
            "read here so it cannot drift from the reconciling sentence")
        assert _words_to_int(prose_remainder.group(1)) == \
            _words_to_int(remaining.group(1)), (
            f"the paragraph states the remainder twice and they disagree: "
            f"{prose_remainder.group(1)!r} against {remaining.group(1)!r}")
        assert _words_to_int(remaining.group(1)) == gained - sum(deltas.values()), (
            f"the paragraph leaves {remaining.group(1)!r} to the modules this "
            f"hook does not select; the arithmetic leaves "
            f"{gained - sum(deltas.values())}")

        # The RATIO half, which the count half leaves untouched. The commit
        # that pinned the counts fixed "roughly nine tenths" against the
        # block's own "at least 84%" BY HAND, and a hand-fixed figure in this
        # block has drifted every time. Both halves of that quantity are
        # derived here from the seconds the block itself states, and every
        # restatement of it must be the same numeral: the failure mode is not
        # a wrong percentage, it is TWO of them.
        others = float(re.search(r"at most (\d+(?:\.\d+)?)s even taking each at "
                                 r"its slowest", flat).group(1))
        floor_s = float(re.search(r"cold pass that never measured below "
                                  r"(\d+(?:\.\d+)?)s", flat).group(1))
        derived = int(100 * (floor_s - others) / floor_s)
        # The WHOLE script, not the sliced block: the restatement that
        # contradicted the derivation sat at line 1032, past
        # "Running the union of every changed path" and so outside `costs`
        # entirely — which is exactly why nothing caught it. A quantity's
        # copies are wherever they are; only its DERIVATION is local.
        whole = re.sub(r"\s+", " ",
                       re.sub(r"^#[ \t]?", "", block, flags=re.M))
        stated_share = {int(n) for n in
                        re.findall(r"at least (\d+)% ", whole)}
        assert stated_share == {derived}, (
            f"the script states the hooks share as {sorted(stated_share)}%; the "
            f"block's own seconds ({floor_s}s cold pass, at most {others}s for "
            f"the other six) give at least {derived}%")

        # No SECOND spelling of the same quantity. A sentence that names the
        # hooks module and a cold pass together may carry the canonical
        # percentage and nothing else — not a fraction in words, not a
        # different percentage.
        fraction = re.compile(
            r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten)[ -]"
            r"(?:halves|thirds|quarters|fifths|sixths|sevenths|eighths|"
            r"ninths|tenths)\b")
        for sentence in whole.split(". "):
            if "cold pass" not in sentence:
                continue
            if "hooks" not in sentence and "that module" not in sentence:
                continue
            assert not fraction.search(sentence), (
                "a sentence states the hooks module's share of a cold pass as "
                f"a fraction in words: {sentence.strip()[:160]!r} — quote the "
                f"block's own 'at least {derived}%' instead. One quantity with "
                "two spellings is what this block exists to end")
            for pct in re.findall(r"(\d+)%", sentence):
                assert int(pct) == derived, (
                    f"a sentence states the hooks share as {pct}% against the "
                    f"block's derived {derived}%: {sentence.strip()[:160]!r}")

        # THE TWO FIGURES THAT DERIVATION READS ARE THEMSELVES PROSE, which is
        # the hole the paragraph above left open: `others` and `floor_s` are
        # parsed out of sentences, so the share stayed internally consistent
        # while both of its inputs described a sitting that had been replaced.
        # A re-taken sitting moved every row and left "at most 31.2s" over a
        # table summing to 33.0 and "never measured below 148.9s" over a cold
        # pass whose floor was 158.6 — and the percentage they produce happened
        # to round the same, so nothing here went red. Both are derived from
        # the rows now, and the seconds column is parsed once for this and for
        # the ARITHMETIC sentence below it.
        #
        # THE AGENT-DEFINITION ROW IS THE AGENTS ROW, both halves of it. The
        # block says so — "the SAME invocation, stated with the same range on
        # purpose. Two figures for one measurement is how this block started
        # drifting from itself the first time" — so the COUNT is as much a
        # second statement as the seconds are, and checking only the seconds
        # left the count free to take any value the allowed set already held:
        # 48 to 94 passed, because 94 is the host guard's.
        assert triggers["agent-definition edit"] == \
            (rows["tests/test_claude_agents.py"],
             secs["tests/test_claude_agents.py"]), (
            f"the agent-definition trigger row reads "
            f"{triggers['agent-definition edit']} against the agents module's "
            f"{(rows['tests/test_claude_agents.py'], secs['tests/test_claude_agents.py'])}"
            " — one invocation measured once, stated twice, and the two have "
            "drifted")

        # WHICH SECONDS ARE DERIVED AND WHICH ARE MEASURED, since the
        # difference decides what can be asserted about them at all. Every
        # figure in these tables is a MEASUREMENT; nothing here re-measures.
        # What can be checked is a figure stated TWICE — a row against its own
        # restatement, or against another row the block says is the same
        # invocation — and that covers the cold pass, the agents row and the
        # skill-edit row's prose copy below.
        #
        # Three sets of seconds are stated once and are therefore held to
        # nothing: the fast path's, the scale row's, and the skill-edit row's
        # own. The last of those cannot be derived even in principle — five
        # modules in one invocation is BOUNDED by the sum of their rows and
        # equal to nothing, and a bound that loose is a check that cannot fail
        # rather than one that does not — which is why its prose restatement
        # below is worth pinning: two copies of an unheld figure drift exactly
        # as readily as two copies of a held one.
        cold_lo, cold_hi = triggers["cold pass, all seven modules"][1]

        six = round(sum(hi for p, (_, hi) in secs.items()
                        if p != "tests/test_claude_hooks.py"), 1)
        assert others == six, (
            f"the share sentence says the other six are at most {others}s; "
            f"the rows above it sum to {six}s")
        assert floor_s == cold_lo, (
            f"the share sentence reads a cold-pass floor of {floor_s}s; the "
            f"cold-pass row says {cold_lo}s")

        # THE SKILL-EDIT ROW IS RESTATED IN PROSE further down, which is the
        # only thing holding those seconds to anything: the restatement read
        # 17.0-21.7s against 131.5-133.1s while its row read 26.6-27.5s against
        # 158.6-169.6s, two sittings apart.
        #
        # ANCHORED, not searched. Counting matches let the real sentence be
        # respelled out of the pattern while a quotation elsewhere kept the
        # count at one — the same substitution this block has now produced in
        # six different places. The anchor phrase must occur ONCE, and the
        # figures are read from that occurrence or nowhere.
        anchor = "a skill edit skips that module"
        assert flat.count(anchor) == 1, (
            f"{anchor!r} appears {flat.count(anchor)} times in the block; the "
            "skill-edit row's prose restatement is read from that phrase, and "
            "a second copy is a second place to read it from")
        restatement = re.match(
            r"a skill edit skips that module and costs [a-z- ]+ of a cold pass "
            r"\((\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)s against "
            r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)s\)",
            flat[flat.index(anchor):])
        assert restatement, (
            "the skill-edit row's prose restatement no longer parses where it "
            "is anchored — widen the pattern rather than leaving the only "
            "check on a figure nothing else holds unread:\n"
            + flat[flat.index(anchor):][:200])
        restated = [restatement.groups()]
        assert (float(restated[0][0]), float(restated[0][1])) == \
            triggers["skill .md edit"][1], (
            f"the prose says a skill edit costs "
            f"{restated[0][0]}-{restated[0][1]}s; its row says "
            f"{triggers['skill .md edit'][1]}")
        assert (float(restated[0][2]), float(restated[0][3])) == \
            (cold_lo, cold_hi), (
            f"the prose states the cold pass as {restated[0][2]}-"
            f"{restated[0][3]}s; its row says {cold_lo}-{cold_hi}s")

        # THE ARITHMETIC SENTENCE, the one place a reader can check the two
        # tables against each other. (The block's own "EXACT" is about the
        # COUNT arithmetic beside it, which is exact; this half is seconds and
        # can only ever agree to within their noise.) It stated the previous
        # sitting's sums against the previous sitting's cold pass, under a
        # table holding neither, for the same reason every other figure here
        # has drifted: a sum a person re-does by hand is a sum nobody re-does.
        # Both halves are re-derived, from the live table only — quoting the
        # right sentence in the history below a wrong one satisfied the first
        # draft of this check.
        parts_lo = round(sum(lo for lo, _ in secs.values()), 1)
        parts_hi = round(sum(hi for _, hi in secs.values()), 1)
        # THE PARAGRAPH, not the live half. Scoping to everything above the
        # banner was still too wide: reword the real sentence past the pattern
        # and quote the correct one anywhere above, labelled "superseded, do
        # not use", and this read the label's own contents as the derivation.
        # A quotation is not a statement, and the only way to tell them apart
        # here is WHERE it sits.
        # SLICED, because the paragraph has an end now — a blank comment line
        # was added under it with this change, for exactly this reason. Three
        # drafts tried to define its extent from the text as it stood: the
        # whole live half, a thirty-five-line window reaching past the COUNT
        # arithmetic, then a punctuation rule standing in for a sentence
        # boundary. The last of those both admitted "Those seven total 100s!
        # Superseded, do not use: they sum to 158.9s" and REFUSED "Those seven,
        # i.e. the modules above, sum to", which is the same failure pointing
        # both ways. Punctuation is not structure. A blank line is.
        #
        # The second half is what makes the slice worth anything: the phrase
        # this derivation is written in must appear NOWHERE ELSE in the live
        # half, so a quotation cannot sit outside the paragraph and answer for
        # a sentence inside it that has been reworded past the pattern.
        def _flat(text):
            return re.sub(r"\s+", " ", re.sub(r"^#[ \t]?", "", text, flags=re.M))

        head = "# ARITHMETIC, stated because"
        assert live_costs.count(head) == 1, (
            f"the ARITHMETIC paragraph opens {live_costs.count(head)} times "
            "above the history banner — the derivation is read out of that "
            "paragraph, and a second opening is a second place to read it")
        para_start = live_costs.index(head)
        para_end = live_costs.find("\n#\n", para_start)
        assert para_end != -1, (
            "the ARITHMETIC paragraph no longer ends at a blank comment line "
            "before the history banner — that blank line is what gives this "
            "derivation an extent, and without it the slice runs on into "
            "whatever follows")
        ariths = re.findall(
            r"sum to (\d+(?:\.\d+)?)s at their minima and "
            r"(\d+(?:\.\d+)?)s at their maxima, against "
            r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)s measured",
            _flat(live_costs[para_start:para_end]))
        assert len(ariths) == 1, (
            f"the ARITHMETIC sentence parses {len(ariths)} times inside its "
            "own paragraph — at zero, widen the pattern rather than leaving "
            "the block's only parts-against-whole check unread; above one, two "
            "copies of it are two things to keep true")

        # AND NO OTHER SECONDS IN THE PARAGRAPH. The slice above says where the
        # derivation lives; this says nothing else lives beside it. "Those
        # seven total 100s! Superseded, do not use: they sum to 158.9s …"
        # satisfies every check up to here — one matching sentence, inside the
        # paragraph, correct — while the sentence a reader believes is the
        # false one in front of it. No pattern can tell which sentence is meant,
        # so the paragraph is required to state ONLY the four figures it
        # derives. It is a paragraph about one arithmetic; a fifth number in
        # seconds is either a second arithmetic or a superseded one.
        # A LIST, not a set, and every spelling of the unit. "Those seven
        # total 169.6s! Superseded, do not use: they sum to …" adds no new
        # VALUE, so a set saw nothing; "100 seconds" and "100 s" were not seen
        # at all. Four figures, four occurrences, however the unit is written.
        para_figures = []
        for lo, hi in re.findall(
                r"(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?\s*(?:s\b|secs?\b"
                r"|seconds\b)", _flat(live_costs[para_start:para_end])):
            para_figures.append(float(lo))
            if hi:
                para_figures.append(float(hi))
        assert sorted(para_figures) == sorted(
                [parts_lo, parts_hi, cold_lo, cold_hi]), (
            f"the ARITHMETIC paragraph states {sorted(para_figures)} in "
            f"seconds; it derives {sorted([parts_lo, parts_hi, cold_lo, cold_hi])}"
            " and may state those and nothing else, each once — a percentage "
            "is not a figure in seconds and is unaffected")
        arith = ariths[0]
        assert (float(arith[0]), float(arith[1])) == \
            (parts_lo, parts_hi), (
            f"the ARITHMETIC sentence sums the seven to "
            f"{arith[0]}-{arith[1]}s; the rows sum to "
            f"{parts_lo}-{parts_hi}s")
        assert (float(arith[2]), float(arith[3])) == \
            (cold_lo, cold_hi), (
            f"the ARITHMETIC sentence states the whole as "
            f"{arith[2]}-{arith[3]}s against a cold-pass row "
            f"reading {cold_lo}-{cold_hi}s")

    # Every SECOND about this hook lives in ONE place: the MEASURED COSTS block
    # in .claude/hooks/skill-invariants.sh. Read the cold pass, the per-module
    # decomposition and the headroom ratio there. They are deliberately not
    # restated here, and that is not fastidiousness: this comment used to carry
    # its own set, the script a second and CLAUDE.md a third, and the copy that
    # sat here longest — a cold-pass figure — was later measured and did not
    # reproduce, while still reading as freshly taken. A quantity kept in three
    # places is a quantity nobody can check.
    #
    # What survives a change of machine, and so is worth stating here, is the
    # SHAPE. One module dominates the hook's own pass — this one, because a hook
    # can only be tested by running it, and every assertion spawns bash while a
    # handful sleep on purpose. The hook pays that on a hook edit and on nothing
    # else: a skill edit selects the two prose modules and skips it entirely, an
    # agent-definition edit is smaller again by more than an order of magnitude.
    # The block quantifies all three.
    #
    # The floor is expressed PER COLLECTED TEST rather than as a number of
    # seconds, because a bare number goes stale in silence and this one already
    # had: it asserted `t >= 30` under a docstring quoting a measurement taken
    # before the hook gained two more modules, and as written it would have
    # passed a timeout well under the real pass — a hook CANCELLED every time it
    # fires, which is the precise outcome the assertion exists to prevent and is
    # indistinguishable from a hook that passed. Deriving the floor from the
    # module list the script actually names means the next test added raises it
    # without anyone remembering to.
    #
    # The two constants below are BUDGETS, not copies of the block: each was
    # chosen above the measurement rather than read off it, which is why they
    # survive the rule that keeps the seconds in one place. They also cover
    # different failure shapes. SECONDS_PER_COLLECTED_TEST tracks a suite that
    # grows by many cheap tests; MEASURED_FULL_PASS_FLOOR covers the shape a
    # per-test budget cannot see — few tests, each slow. Which of the two binds
    # is arithmetic on the pair: the derived floor overtakes the absolute one at
    # MEASURED_FULL_PASS_FLOOR / SECONDS_PER_COLLECTED_TEST collected tests, and
    # at (registered timeout) / SECONDS_PER_COLLECTED_TEST it exceeds the
    # timeout itself and asks for that to be raised. Producing that prompt is
    # the whole point of the assertion; when it fires, act on it rather than
    # re-deriving the constant that produced it.
    #
    # The absolute one no longer binds anything: the measured cold pass has
    # OVERTAKEN it (MEASURED COSTS), and the derived floor passed it long before
    # that. It stays as the floor for a hypothetical suite of a few very slow
    # tests, which is the shape the per-test budget cannot see — but read it as
    # that, not as a bound on today's runtime, and do not raise it to chase the
    # measurement. Chasing is how a budget becomes a second, staler copy of the
    # block.
    SECONDS_PER_COLLECTED_TEST = 0.35
    MEASURED_FULL_PASS_FLOOR = 120

    def test_the_skill_hook_has_headroom_over_its_measured_runtime(self):
        """A timeout near the measured runtime turns an ordinary cold start into
        a cancelled check, and a cancelled check reads to the agent exactly like
        a check that passed.

        Collection is the cheap half of a pytest run — a rounding error against
        the pass itself, per the MEASURED COSTS block in
        `.claude/hooks/skill-invariants.sh` — and the count it returns is
        deterministic across machines, so deriving the floor this way does not
        make this a wall-clock test that flakes on a slow CI runner.
        """
        # The modules the hook APPENDS to its pytest argv, not every
        # `tests/*.py` string in the file: two more are named in the KNOWN GAP
        # comment precisely because they are too slow to run here, and counting
        # those would demand headroom for a pass that never happens.
        modules = sorted(set(re.findall(
            r"modules\[\$\{#modules\[@\]\}\]=(tests/[\w./-]+\.py)",
            SKILL_HOOK.read_text())))
        assert modules, (
            "no module list found — the hook either checks nothing or builds "
            "its argv some other way, and this floor is measuring neither")
        # `no:cacheprovider` so counting the hook's tests cannot leave a
        # `.pytest_cache/` behind in the working tree — the tracked .gitignore
        # does not name it, and a test must not create repository state.
        collected = subprocess.run(
            [sys.executable, "-m", "pytest", *modules, "--collect-only", "-q",
             "-p", "no:cacheprovider"],
            cwd=REPO, capture_output=True, text=True)
        counted = re.search(r"(\d+) tests? collected", collected.stdout)
        assert counted, (
            f"could not count the hook's own pass: {collected.stdout[-400:]}")
        floor = max(self.MEASURED_FULL_PASS_FLOOR,
                    math.ceil(int(counted.group(1))
                              * self.SECONDS_PER_COLLECTED_TEST))

        timeouts = [h.get("timeout") for e in self._post_tool_use()
                    for h in e["hooks"] if "skill-invariants" in h["command"]]
        assert timeouts, "the skill hook is not registered at all"
        for t in timeouts:
            assert t is not None and t >= floor, (
                f"timeout {t}s leaves no headroom over the hook's own "
                f"{counted.group(1)}-test pass; {floor}s is the floor that "
                "suite size implies. For the observed ranges behind that, see "
                "the MEASURED COSTS block in .claude/hooks/skill-invariants.sh "
                "— naming it rather than quoting a second is the point: the "
                "figure this message used to carry was measured wrong twice")
