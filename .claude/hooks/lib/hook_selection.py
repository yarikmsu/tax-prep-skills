"""Which test modules an edited path can turn red.

W3 slice 1. `select_modules_for` in `skill-invariants.sh` is the hook's
largest single decision — 218 lines of `case` arms mapping a repository path
to the modules an edit to it can break — and this is that decision in Python.

**The bash is still what runs.** Nothing here is wired into the hook yet, and
that is the point of doing it this way: a port that changes behaviour on the
day it lands cannot be told apart from a port that changes behaviour by
mistake. What lands first is the CONTRACT, executable, with
`test_the_python_selection_matches_the_shell` running both implementations over
every tracked path in the repository and requiring identical answers. The flip
comes later, against a contract that has already been green for a while.

TWO PROPERTIES OF `case` THAT A PORT GETS WRONG IF IT IS NOT LOOKING:

- **First match wins, and the order is load-bearing.** `docs/ops` appears
  twice, once early selecting three modules and once late selecting one; so
  does `tests`. An implementation that collected every matching arm, or that
  stored the arms in a dict, would quietly take the wrong one. The arms are a
  LIST here for that reason, in source order.
- **`*` crosses `/`.** In a `case` pattern — unlike filename globbing — `*`
  matches slashes, so `agents/lib/*` really does reach `agents/lib/a/b.py`.
  `fnmatch` has the same semantics, which is why it is the right primitive
  and a hand-rolled segment matcher would not be.
"""

from __future__ import annotations

import fnmatch
import os

# The eight flags, in the order `skill-invariants.sh` declares them. Named
# here so a comparison harness can render a stable vector.
MODULES = ("PORTABILITY", "CAPABILITIES", "AGENTS", "HOOKS", "HOOKSDEPS",
           "PREFLIGHT", "SECUREFS", "HOSTGUARD")

_EVERY = frozenset(MODULES)

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
    (("agents/templates/result_template.json",
      "agents/templates/projection_inputs_template.json"),
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

    PER PATH. The shell calls its version in a loop and the RUN_ globals
    ACCUMULATE across every changed path, so the flip must union these sets —
    a caller that takes the last path's answer would silently narrow the pass
    to whatever was scanned last, which is the shape of a green hook over an
    unchecked edit.

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
