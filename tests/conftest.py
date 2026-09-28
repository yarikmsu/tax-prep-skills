"""Report the engine's refusal of this interpreter ONCE, not per test module.

`agents/lib/__init__.py` exits 2 at package import when a workspace `.venv`
exists and this interpreter is not it. Roughly forty test modules import the
engine, so without this file that single environmental fact becomes forty
collection errors, each carrying its own copy of the message — and the reader
scrolls past the one sentence that says what to do. This runs before any test
module is imported, so the refusal is reported once, in the engine's own words
on stderr, and the run stops with the engine's own status.

This replaces a `skipif` guard in `tests/test_preflight.py` that made an
off-venv run legible for four tests. The engine now makes such a run
impossible, which is the better version of the same intent.

WHY `pytest_configure` AND NOT THE MODULE BODY. `pytest.exit` raises
`_pytest.outcomes.Exit`, and pytest honours it — status and all — only from
inside a hook. Raised while pytest is IMPORTING a conftest it is caught by
`_importconftest`'s own `except Exception` and re-raised as a
`ConftestImportFailure`, so the run ended with an "ImportError while loading
conftest" banner, a full traceback, and status 4 — a USAGE error. The
`returncode=` argument was dead, the second copy of the message this file
exists to prevent was printed anyway as a traceback, and a caller branching on
2 to mean "blocked" saw the one status that means the command line was wrong.
Reported against PR #82; reproduced on the pytest the lock pins, and pinned by
`tests/test_claude_hooks.py`.

`pytest_configure` runs after the initial conftests are loaded and before any
test module is imported, which is the whole window that matters here: the
engine import that ends the run has to happen before forty modules each attempt
their own.
"""
import sys
from pathlib import Path

import pytest

# MODULE LEVEL, unlike the import below: every test module resolves `agents.lib`
# through this, and they are imported after `pytest_configure` only in the runs
# that get that far.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_configure(config):
    try:
        import agents.lib  # noqa: F401
    except SystemExit as refusal:
        # The guard has already written its message to stderr. Repeating it
        # here would be the second copy this file exists to prevent.
        pytest.exit("the engine refused this interpreter — see the message "
                    "above",
                    returncode=refusal.code if isinstance(refusal.code, int)
                    else 2)


# THE ROUTES BY WHICH BASH HANDS A SCRIPT A FUNCTION IT NEVER DEFINED, in one
# place so that every gate re-running itself under `-p` is tested against all of
# them, and a route found later reaches every gate's test at once. A function
# runs ahead of the PATH lookup, so whichever arrives stands in for the command
# of its name.
#
#   export -f                 an environment entry bash imports before line one
#   BASH_ENV                  a file bash runs before line one, defining it
#                             behind an exported include guard, as startup
#                             files often are, so a second run defines nothing
#   SHELLOPTS=privileged      the export, with `-p` already on when the script
#                             looks, since bash reads SHELLOPTS after importing
#   exported exec             the export, beside a no-op `exec` for the re-run
#   BASH_ENV under SHELLOPTS  the file, beside an ordinary exported SHELLOPTS,
#                             which a gate's re-run hands on as `privileged`
INHERITED_FUNCTION_ROUTES = ("export -f", "BASH_ENV", "SHELLOPTS=privileged",
                             "exported exec", "BASH_ENV under SHELLOPTS")


@pytest.fixture(params=INHERITED_FUNCTION_ROUTES)
def inherited_function(request, tmp_path_factory):
    """`inherited_function(name, body)` is the environment that hands a bash
    script a function called `name` running `body`, by one route per case."""
    route = request.param

    def environment(name: str, body: str) -> dict:
        if route.startswith("BASH_ENV"):
            startup = tmp_path_factory.mktemp("bash-env") / "startup.sh"
            startup.write_text('[ -n "${INHERITED_FUNCTION_LOADED-}" ] && return\n'
                               "export INHERITED_FUNCTION_LOADED=1\n"
                               f"{name}() {{ {body}; }}\n")
            entries = {"BASH_ENV": str(startup)}
            if route == "BASH_ENV under SHELLOPTS":
                entries["SHELLOPTS"] = "braceexpand"
            return entries
        entries = {f"BASH_FUNC_{name}%%": f"() {{  {body}\n}}"}
        if route == "SHELLOPTS=privileged":
            entries["SHELLOPTS"] = "privileged"
        elif route == "exported exec":
            entries["BASH_FUNC_exec%%"] = "() {  :\n}"
        return entries

    return environment
