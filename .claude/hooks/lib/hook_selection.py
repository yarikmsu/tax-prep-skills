"""Which test modules an edited path can turn red.

W3, slices 1 and 2. This is the hook's largest single decision — which test
modules an edit to a repository path can turn red — and `skill-invariants.sh`
runs it. It was 218 lines of `case` arms there until slice 2; slice 1 landed
this file beside them, held to them by a differential over every tracked path
in the repository, and slice 2 spent that differential and deleted the bash.

A `case` is still what these arms ARE, and two of its properties are wrong in
any port that is not looking for them:

- **First match wins, and the order is load-bearing.** `docs/ops` appears
  twice, once early selecting three modules and once late selecting one; so
  does `tests`. An implementation that collected every matching arm, or that
  stored the arms in a dict, would quietly take the wrong one. The arms are a
  LIST here for that reason, in source order — and `_ARMS` being DATA rather
  than text is what retired a whole class of test: a scan over `case` arms
  cannot tell an arm's prose from its code, and it counted
  `# RUN_SECUREFS=1  (temporarily off)` as a selection.
- **`*` crosses `/`.** In a `case` pattern — unlike filename globbing — `*`
  matches slashes, so `agents/lib/*` really does reach `agents/lib/a/b.py`.
  `fnmatch` has the same semantics, which is why it is the right primitive
  and a hand-rolled segment matcher would not be.
"""

from __future__ import annotations

import fnmatch
import os
import sys

# The eight flags, in the order `skill-invariants.sh` declares them. Named
# here so a comparison harness can render a stable vector.
MODULES = ("PORTABILITY", "CAPABILITIES", "AGENTS", "HOOKS", "HOOKSDEPS",
           "PREFLIGHT", "SECUREFS", "HOSTGUARD")

_EVERY = frozenset(MODULES)

# The first line `main` writes, and the shell refuses an answer without it.
#
# A SELECTOR THAT CANNOT ANSWER MUST NOT BE ABLE TO ANSWER "NOTHING", and an
# exit status alone does not buy that: an EMPTY file is valid Python, runs
# clean and prints nothing, which the shell would read as "no module can break
# that path" and report as a silent green. That is not a hypothetical shape —
# the hook fires on every Write, this file included, so the reachable case is a
# save that leaves it truncated while someone is editing the selector itself.
# The sentinel makes "the port ran" a POSITIVE claim rather than the absence of
# a complaint. `test_the_shell_and_the_port_agree_on_the_protocol_line` reads
# it from here and from the script, so the two cannot drift.
PROTOCOL = "SELECTION1"

# (patterns, modules) in SOURCE ORDER. First match wins, exactly as `case`.
_ARMS: tuple[tuple[tuple[str, ...], frozenset], ...] = (
    ((".agents/skills", ".agents/skills/*", ".claude/skills", ".claude/skills/*"),
     frozenset({"PORTABILITY", "CAPABILITIES", "SECUREFS", "PREFLIGHT", "AGENTS"})),
    ((".claude/agents", ".claude/agents/*.md"), frozenset({"AGENTS"})),
    (("docs/ops/review-subagent-acceptance.md",),
     frozenset({"AGENTS", "PREFLIGHT", "PORTABILITY"})),
    (("docs/contributing/residency-guardrails.md",), frozenset({"CAPABILITIES"})),
    (("docs/contributing/parse-boundary.md",), frozenset({"AGENTS"})),
    (("docs/ops/season-open-runbook.md",),
     frozenset({"PREFLIGHT", "AGENTS", "PORTABILITY"})),
    (("docs/ops",), frozenset({"PREFLIGHT", "AGENTS", "PORTABILITY"})),
    # `.claude/hooks/*`, not an extension list. The arm read `*.sh` and was
    # faithful while the hooks were shell-only; W3 puts Python under
    # `.claude/hooks/lib/`, and every file this slice adds selected NOTHING —
    # visible to the stamp scan, matching no arm, reported as a green pass
    # over an unchecked edit, which CLAUDE.md makes an ALWAYS.
    #
    # Widened rather than extended by one more suffix, because this repo has
    # already paid for the narrow form: three arms were scoped to `*.py`/`*.sh`
    # to dodge `__pycache__`, and a rule phrased around what it DODGES stops
    # covering what it is FOR — a directory has no extension and matched none
    # of them.
    #
    # What the prologue excludes is `__pycache__` PATHS, not bytecode in
    # general: a stray `.pyc` beside the source, or an editor's `.swp`, does
    # select this module. That is the same exposure `agents/lib/*` has carried
    # since it was written — `agents/lib/ptet.py.swp` selects four modules
    # today — so this widening matches the convention rather than departing
    # from it. Stated because the shorter claim ("bytecode is excluded") was
    # written here first and is not true.
    ((".claude/hooks", ".claude/hooks/*", ".claude/settings.json"),
     frozenset({"HOOKS"})),
    (("tests/test_claude_hooks.py",), frozenset({"HOOKS", "HOSTGUARD"})),
    (("scripts/pre-commit-pii-scan.sh",),
     frozenset({"HOOKS", "PORTABILITY", "SECUREFS"})),
    (("agents/lib/capabilities.py", "agents/lib/rollover.py"),
     frozenset({"CAPABILITIES", "AGENTS", "PREFLIGHT", "PORTABILITY",
                "SECUREFS", "HOSTGUARD"})),
    (("agents/lib/tax_law_fetcher.py",),
     frozenset({"AGENTS", "PREFLIGHT", "PORTABILITY", "SECUREFS", "HOSTGUARD"})),
    (("agents/lib/ptet.py",),
     frozenset({"AGENTS", "PREFLIGHT", "PORTABILITY", "SECUREFS", "HOSTGUARD"})),
    (("agents/lib/tax_computations", "agents/lib/tax_computations/*"),
     frozenset({"AGENTS", "PREFLIGHT", "PORTABILITY", "SECUREFS", "HOSTGUARD"})),
    (("tests/test_claude_agents.py",),
     frozenset({"AGENTS", "HOOKSDEPS", "HOSTGUARD"})),
    (("agents/lib/secure_fs.py",),
     frozenset({"SECUREFS", "CAPABILITIES", "AGENTS", "PREFLIGHT",
                "PORTABILITY", "HOSTGUARD"})),
    (("agents", "scripts"), frozenset({"SECUREFS"})),
    (("agents/lib",),
     frozenset({"PREFLIGHT", "CAPABILITIES", "AGENTS", "HOSTGUARD",
                "SECUREFS", "PORTABILITY"})),
    (("requirements.in", "requirements.txt"), frozenset({"PREFLIGHT"})),
    (("agents/lib/*.sh",), frozenset({"PREFLIGHT", "PORTABILITY", "SECUREFS"})),
    (("agents/lib/*",),
     frozenset({"PREFLIGHT", "HOSTGUARD", "SECUREFS", "PORTABILITY"})),
    (("tests/conftest.py",), _EVERY),
    (("tests/test_preflight.py",),
     frozenset({"PREFLIGHT", "HOOKSDEPS", "HOSTGUARD"})),
    (("tests/test_secure_fs.py",),
     frozenset({"SECUREFS", "HOOKSDEPS", "HOSTGUARD"})),
    (("tests/test_host_guard.py",),
     frozenset({"HOSTGUARD", "HOOKSDEPS", "PORTABILITY"})),
    (("agents/templates/taxpayer_profile_template.json",),
     frozenset({"CAPABILITIES"})),
    (("tests/test_capabilities.py",),
     frozenset({"CAPABILITIES", "HOOKSDEPS", "HOSTGUARD"})),
    # result_template.json gained CAPABILITIES with the EIC disclosure:
    # test_capabilities.py reads the template to prove the rule's text carries
    # no year-specific figure and to derive the EIC lines rather than list them
    # again. An arm that stayed PORTABILITY-only would report a silent green
    # over an edit that can turn the capability module red.
    (("agents/templates/result_template.json",),
     frozenset({"PORTABILITY", "CAPABILITIES"})),
    (("agents/templates/projection_inputs_template.json",),
     frozenset({"PORTABILITY"})),
    (("tests/skill_text.py",),
     frozenset({"PORTABILITY", "CAPABILITIES", "PREFLIGHT", "HOSTGUARD",
                "AGENTS"})),
    (("tests/engine_tree.py",),
     frozenset({"PORTABILITY", "HOSTGUARD", "PREFLIGHT", "SECUREFS"})),
    (("docs/QUICKSTART.md", "docs/contributing/agents-compat.md"),
     frozenset({"PREFLIGHT", "PORTABILITY"})),
    (("CONTRIBUTING.md", "README.md"), frozenset({"PORTABILITY", "PREFLIGHT"})),
    (("agents/install.sh",), frozenset({"PORTABILITY", "SECUREFS"})),
    (("CLAUDE.md",), frozenset({"PORTABILITY", "AGENTS"})),
    # pyproject.toml, for a reason that is not ruff or mypy config. Its
    # `[tool.pytest.ini_options]` section is what stops pytest's rootdir search
    # before it reaches an ancestor directory's `pytest.ini` — and an ancestor
    # holding `addopts = --collect-only` makes this gate report a suite that
    # ran nothing. `test_the_project_inifile_stops_an_ancestor_pytest_ini`
    # asserts that section is there, so an edit to this file has to be able to
    # turn the module holding it red.
    (("pyproject.toml",), frozenset({"HOOKS"})),
    (("AGENTS.md",), frozenset({"PORTABILITY", "PREFLIGHT"})),
    ((".github/ISSUE_TEMPLATE", ".github/ISSUE_TEMPLATE/bug_report.yml"),
     frozenset({"PREFLIGHT"})),
    ((".github/workflows/ci.yml",), frozenset({"AGENTS"})),
    (("LICENSE", ".gitignore", ".pii-allowlist", ".vscode/settings.json",
      "docs/contributing", "docs/contributing/*.md",
      "docs/ops", "docs/ops/*.md",
      "docs/roadmap", "docs/roadmap/*.md"),
     frozenset({"PORTABILITY"})),
    (("tests/test_portability.py",),
     frozenset({"PORTABILITY", "HOOKSDEPS", "HOSTGUARD"})),
    (("tests",), frozenset({"PORTABILITY", "HOSTGUARD"})),
    (("agents/*.py", "agents/*.sh", "scripts/*.py", "scripts/*.sh"),
     frozenset({"SECUREFS"})),
    (("tests", "tests/*.py"), frozenset({"HOSTGUARD"})),
)


def select_modules_for(path: str, *, isdir=os.path.isdir) -> frozenset:
    """The modules an edit to `path` can turn red. Empty means none.

    PER PATH, and the accumulation across a scan is `union_for`, not this.
    The shell used to call its own version in a loop and let the RUN_ globals
    accumulate; that loop is gone, so a caller reaching for this function
    directly over many paths has to union the answers itself. Taking the last
    path's answer would narrow a whole scan to whatever `find` printed last,
    which is the shape of a green hook over an unchecked edit.

    `isdir` is injected so the package-directory arm can be exercised without
    a filesystem: it is the one place this decision is not pure, and hiding
    that behind a default would make the differential test's agreement depend
    on which machine it ran on.
    """
    # A `.` or `..` component makes the textual match meaningless, and
    # `__pycache__` is never source. Both select nothing, as the shell does.
    wrapped = f"/{path}/"
    for bad in ("/../", "/./", "//"):
        if bad in wrapped:
            return frozenset()
    if "/__pycache__/" in wrapped:
        return frozenset()

    # A package DIRECTORY normalises to its own `__init__.py`. The first arm
    # is a no-op in the shell too — it exists to claim any path whose tail
    # carries a dot before the directory arm can see it.
    if not fnmatch.fnmatchcase(wrapped, "/agents/lib/*.*/") \
            and fnmatch.fnmatchcase(wrapped, "/agents/lib/*/") \
            and isdir(path):
        path = f"{path}/__init__.py"

    for patterns, modules in _ARMS:
        if any(fnmatch.fnmatchcase(path, p) for p in patterns):
            return modules
    return frozenset()


def union_for(paths) -> frozenset:
    """The union across every changed path — never the last one's answer.

    The shell accumulated into its `RUN_` globals across a loop; this is where
    that accumulation lives now. A caller taking one path's answer would
    narrow the pass to whatever `find` printed last, which is the shape of a
    green hook over an unchecked edit.
    """
    return frozenset().union(*(select_modules_for(p) for p in paths),
                             frozenset())


def main() -> int:
    """NUL-delimited paths in, one selected flag suffix per line out.

    ONE PROCESS PER SCAN, not one per path — and per SCAN rather than per
    pass, which is not the same thing and was written as if it were. A Write
    with a changed list behind it enters this twice: once for the payload's
    own path, once for the scan. Instrumented, two interpreter starts. `case`
    was a shell function call costing nothing measurable and an interpreter
    start is not, so the loop had to move inside the process: a scan that
    hands over a thousand changed paths must not pay that start a thousand
    times, which is the claim that holds.

    NUL-delimited because `find -print0` is what the scan already produces and
    because a repository path may contain a newline. Reading it as lines would
    split one such path into two fragments, each matching no arm — and two
    nothings is indistinguishable from one, so the miss would be silent.

    TWO WAYS OF NOT ANSWERING, and the shell refuses on both. A raise exits
    CPython non-zero, which the command substitution's `|| return 1` catches —
    there is no `try` here, because a `try` adds nothing the exit status does
    not already say and the traceback goes to a stderr the hook discards.
    `PROTOCOL` closes the half the exit status cannot reach; see its comment.
    """
    raw = sys.stdin.buffer.read().decode("utf-8", "surrogateescape")
    # The sentinel goes first, and the reason is that the shell can refuse on
    # it before reading anything else — not that writing it last would be
    # weaker. Last would in fact be marginally stronger, proving the loop ran
    # to the end; the exit status already proves that, since anything killing
    # this process leaves it non-zero. An earlier version of this comment said
    # a trailing sentinel would make an empty selection indistinguishable from
    # a crash, which is simply false: sentinel plus exit 0 is one, a missing
    # sentinel is the other.
    print(PROTOCOL)
    for module in union_for(p for p in raw.split("\0") if p):
        print(module)
    return 0


if __name__ == "__main__":
    sys.exit(main())
