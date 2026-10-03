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
  sequence for that reason, ordered by declared priority. The generated
  `_ARMS` being DATA rather than text is what retired a whole class of test: a scan over `case` arms
  cannot tell an arm's prose from its code, and it counted
  `# RUN_SECUREFS=1  (temporarily off)` as a selection.
- **`*` crosses `/`.** In a `case` pattern — unlike filename globbing — `*`
  matches slashes, so `agents/lib/*` really does reach `agents/lib/a/b.py`.
  `fnmatch` has the same semantics, which is why it is the right primitive
  and a hand-rolled segment matcher would not be.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import sys
from pathlib import Path

# The eight flags, in the order `skill-invariants.sh` declares them. Named
# here so a comparison harness can render a stable vector.
MODULES = ("PORTABILITY", "CAPABILITIES", "AGENTS", "HOOKS", "HOOKSDEPS",
           "PREFLIGHT", "SECUREFS", "HOSTGUARD")

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

# This roster is structural: flags name the seven suites the shell can run.
# Dependency patterns live ONLY in each suite's WATCHES declaration.
MODULE_FILES = {
    "PORTABILITY": "tests/test_portability.py",
    "CAPABILITIES": "tests/test_capabilities.py",
    "AGENTS": "tests/test_claude_agents.py",
    "HOOKS": "tests/test_claude_hooks.py",
    "PREFLIGHT": "tests/test_preflight.py",
    "SECUREFS": "tests/test_secure_fs.py",
    "HOSTGUARD": "tests/test_host_guard.py",
}


def _literal_watches(source: str, name: str) -> tuple:
    """Read one module-level literal without executing a test or its imports.

    Refuse absent, duplicate, computed or malformed declarations. The shell
    already runs all checks when its selector fails; never report an empty map
    for a file being saved or for an incomplete checkout.
    """
    tree = ast.parse(source)
    writes = [node for node in ast.walk(tree)
              if isinstance(node, ast.Name) and node.id == name
              and isinstance(node.ctx, ast.Store)]
    if len(writes) != 1:
        raise ValueError("watch declaration must be a single assignment")
    values = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            values.append(ast.literal_eval(node.value))
    if len(values) != 1 or not isinstance(values[0], tuple) or not values[0]:
        raise ValueError("missing or malformed watch declaration")
    seen = set()
    for entry in values[0]:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise ValueError("invalid watch entry")
        priority, patterns = entry
        if type(priority) is not int or priority < 0 or priority in seen:
            raise ValueError("invalid watch priority")
        seen.add(priority)
        if not isinstance(patterns, tuple) or not patterns:
            raise ValueError("invalid watch patterns")
        for pattern in patterns:
            if (not isinstance(pattern, str) or not pattern
                    or pattern.startswith("/") or "\\" in pattern
                    or any(part in ("", ".", "..", "__pycache__")
                           for part in pattern.split("/"))):
                raise ValueError("invalid watch path")
    return values[0]


def load_declarations(root: Path) -> dict:
    """Load once per selector process, from the checkout beside this script."""
    declarations = {}
    for flag, filename in MODULE_FILES.items():
        source = (root / filename).read_text(encoding="utf-8")
        declarations[flag] = _literal_watches(source, "WATCHES")
        if flag == "HOOKS":
            declarations["HOOKSDEPS"] = _literal_watches(
                source, "WATCHES_DEPENDENTS")
    return declarations


def build_arms(declarations: dict) -> tuple:
    """Merge shared priority groups, preserving first-match selection costs.

    A priority identifies one pattern group. Modules sharing it must agree on
    that group; refusing a collision prevents an added dependency from quietly
    shadowing another module's declaration. New groups need no selector edit.
    """
    groups = {}
    for flag, watches in declarations.items():
        for priority, patterns in watches:
            if priority not in groups:
                groups[priority] = (patterns, set())
            group_patterns, flags = groups[priority]
            if patterns != group_patterns:
                raise ValueError("conflicting watch priority")
            flags.add(flag)
    return tuple((patterns, frozenset(flags))
                 for _, (patterns, flags) in sorted(groups.items()))


DECLARATIONS = load_declarations(Path(__file__).resolve().parents[3])
_ARMS = build_arms(DECLARATIONS)


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
