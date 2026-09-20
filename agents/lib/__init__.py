# Tax preparation agent utilities
"""Refuse a host interpreter at import, whenever a workspace venv exists.

AGENTS.md §4 requires every engine call to go through `agents/lib/run.sh`,
which activates `<engine root>/.venv` and execs the interpreter that venv
installed. Until this guard, that requirement was enforced by an audit of the
skill texts (`tests/test_preflight.py`) — a hand-written approximation of bash
grammar, which spent ~20 review rounds discovering one more spelling of a bare
interpreter call per round because bash grammar is unbounded relative to a
regex. The audit stays as a documentation lint; the RULE lives here, where the
spelling of the command that started the process cannot matter, because every
form of reaching this engine — `-m`, a script path, `-c`, a heredoc — imports
this package first.

Why each decision, since the code only shows what:

* **In `__init__`, not in each `_main`.** `_main` covers the CLI forms only,
  and four shipped skill steps reach the engine as `from agents.lib import …`
  inside `-c` or a heredoc. Package import is the one chokepoint all of them
  pass.
* **No `.venv` ⇒ no opinion.** Identical to `preflight._venv_error`'s
  `_NO_VENV`: CI installs into the runner's own interpreter, and a fresh clone
  has no venv until `tax-setup` Step 6 makes one. A gate that fires on a
  correct state teaches its reader to reason past it.
* **Present but uncomparable ⇒ block.** A `.venv` that is a dangling symlink, a
  regular file, or unstattable is a defect, not an absence. §4's bias rule:
  the cost of a false block is re-running through the launcher; the cost of a
  false pass is a return computed against unpinned dependency versions.
* **Preflight alone is exempt**, and by what CPython says the PROGRAM is, never
  by re-deriving it from the interpreter's own option table. Preflight must be
  able to REPORT this defect with its own full remedy, and under
  `-m agents.lib.preflight` it would otherwise never start, since `runpy`
  imports this package before it rewrites `sys.argv`. `_preflight_is_the_program`
  holds the two questions that answer and why the table is not consulted.
  There is deliberately no environment-variable opt-out: `run.sh` resets
  `TAXPY` precisely because an exported variable from the caller's shell would
  otherwise choose the interpreter, and an opt-out here is that same hole.
* **The rule is re-stated, not imported from `preflight`.** Importing it would
  pull ~2,400 lines and `importlib.metadata` into every engine process and
  invert the dependency direction — preflight has to stay runnable when this
  guard is the thing that is wrong. `tests/test_host_guard.py` pins the two
  copies equal over the three states that matter.
* **The message names no path.** `run.sh` drops `$0` from its own errors
  because a workspace folder may carry a taxpayer's name or SSN (AGENTS.md
  §1), and `sys.prefix` is a path under it. What failed and what to run is
  enough; where is not this message's to disclose.

Exit status 2 is preflight's "blocked", so a skill branching on the status
reads this the same way.
"""

import os
import stat
import sys
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent

_EXEMPT_MODULE = "agents.lib.preflight"
# THIS package's preflight, not a name. Kept as a path so the check below can
# compare identity rather than spelling.
_EXEMPT_PATH = Path(__file__).resolve().parent / "preflight.py"

# "The caller did not pass this" and "CPython does not have this" are OPPOSITE
# answers for every input below, and `None` cannot say both: an absent
# `__main__.__file__` is itself the evidence that the program is `-m`, `-c` or
# stdin, and a test has to be able to state that case explicitly rather than
# fall through to whatever this process happens to be.
_UNSET = object()

_DEFECT = (
    "The tax engine was imported by an interpreter that is not the workspace "
    "virtualenv, and a workspace virtualenv exists.\n"
    "The dependency versions a return is computed against are the ones pinned "
    "in that venv, so this interpreter is not one this engine will compute on.\n"
    "Run engine commands through the launcher:\n"
    "    sh agents/lib/run.sh -m agents.lib.<module>\n"
    "    sh agents/lib/run.sh agents/lib/<module>.py\n"
    "For the full diagnosis, including what else is missing:\n"
    "    sh agents/lib/run.sh agents/lib/preflight.py"
)


def _is_this_preflight(token, preflight_path) -> bool:
    """`os.path.samefile`, never a basename — the same rule `_venv_error` uses.

    The exemption is for THIS package's preflight, and a basename comparison
    granted it to any script anywhere called `preflight.py`: a stranger's file
    of that name, in a workspace that has a `.venv`, could import the engine
    and compute on the host interpreter with no refusal at all. An exemption
    whose predicate is wider than its stated scope is a hole by construction,
    and this one was reported against PR #82.

    `samefile` compares device and inode, so a symlink to the real file, a
    relative spelling and a differently-cased path all still match, while a
    different file never does — the same reason `_venv_error` refuses `==` on
    resolved paths. A token that cannot be stat'd is NOT exempt: unverifiable
    is not verified, and the bias here runs toward refusing. That is also what
    answers the two `__main__.__file__` values which are not paths at all,
    `<stdin>` and `<string>`.
    """
    try:
        return os.path.samefile(token, preflight_path)
    except (OSError, TypeError, ValueError):
        return False


def _preflight_is_the_program(main_file, argv, orig_argv, preflight_path) -> bool:
    """True when THIS process was started to run preflight itself.

    ASKS CPYTHON WHAT THE PROGRAM IS; it does not re-derive it. The walk that
    stood here read `sys.orig_argv` from the left, skipping interpreter options
    until it reached a token that was not one — which requires a model of
    CPython's option table, and a model of an option table is wrong the way a
    regex over bash grammar is wrong. It shipped two holes of its own, both
    reported against PR #82: an ATTACHED `-c<code>` is not the token `-c`, so
    `python -c'<engine call>' agents/lib/preflight.py` fell through to the
    generic option branch and read preflight's path as the program, taking the
    exemption on a bare `-c` body; and the "last letter of the cluster spends
    the next token" rule was applied to the whole token, so `-Xtracemalloc` and
    `-Wignore::DeprecationWarning:pkg.algorithm` were misread as operand-taking
    and preflight lost its exemption under either. The first fails OPEN, which
    is the class that matters. Two questions replace the table:

    1. **`__main__.__file__`, when there is one.** CPython sets it to the
       script before the script's own imports run, so it is already correct
       here, whatever options preceded it — `-X tracemalloc`, `-W`, an attached
       value, a bundle, a long option, anything a future interpreter adds. It
       is compared by IDENTITY (`_is_this_preflight`), never by name.
    2. **`sys.argv[0]`, when there is not.** No `__main__.__file__` means `-m`,
       `-c` or stdin, and CPython has ALREADY told us which: it writes `-m`,
       `-c` or `-` into `sys.argv[0]` itself and `runpy` does not overwrite it
       until after this package is imported. Only `-m` can be preflight, so
       `-c` and stdin are refused on CPython's own word rather than on a
       reading of the command line — which is what closes the attached-`-c`
       hole outright rather than by learning one more spelling.

    Recovering WHICH module `-m` names is then arithmetic, not a scan.
    `sys.argv` is `['-m', *the module's own arguments]` while `orig_argv` is
    `[executable, *interpreter options, '-m', module, *those same arguments]`,
    so the module sits exactly `len(orig_argv) - len(argv)` tokens in, however
    many options precede it and whether or not the value is attached. A `-m`
    appearing again among the module's own arguments cannot confuse it, because
    nothing is searched for.

    One spelling is knowingly unexempted, fail-CLOSED and with a working remedy
    in the message: `sys.orig_argv` arrived in 3.10, so on an interpreter older
    than that `-m agents.lib.preflight` gets the refusal rather than preflight's
    own verdict. That boundary is BELOW MIN_PYTHON and no longer tracks it —
    every supported interpreter has the attribute, and the gap is the sub-floor
    range preflight exists to diagnose, which is why the remedy has to work
    there. The script-path form needs no `orig_argv` at all and is exempt on
    every version — which is the form all twenty-five shipped invocations use,
    and the one the refusal names.
    """
    if main_file is not None:
        return _is_this_preflight(main_file, preflight_path)
    if not argv or argv[0] != "-m":
        return False
    if not orig_argv:
        return False
    index = len(orig_argv) - len(argv)
    if not 0 < index < len(orig_argv):
        return False
    token = orig_argv[index]
    # `python3 -magents.lib.preflight` — an attached option value, one of the
    # spellings the launcher audit had to learn the hard way. It occupies the
    # single token the arithmetic above lands on, so only the `-m` comes off.
    if token.startswith("-m"):
        token = token[2:]
    return token == _EXEMPT_MODULE


def host_interpreter_defect(root=None, prefix=None, base_prefix=None,
                            orig_argv=_UNSET, preflight_path=None,
                            main_file=_UNSET, argv=_UNSET) -> str:
    """The refusal message, or `""` when this interpreter may run the engine.

    Every input is an explicit argument so the rule can be tested over states
    this process is not in — by the time a test imports it, the module-level
    call below has already run against the real `sys`.
    """
    root = _ENGINE_ROOT if root is None else root
    prefix = sys.prefix if prefix is None else prefix
    base_prefix = sys.base_prefix if base_prefix is None else base_prefix
    preflight_path = _EXEMPT_PATH if preflight_path is None else preflight_path
    if main_file is _UNSET:
        # `sys.modules.get`, not an import: `__main__` is always there in a
        # normal interpreter and never worth importing to find out.
        main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    if argv is _UNSET:
        argv = sys.argv
    if orig_argv is _UNSET:
        # `sys.orig_argv` is 3.10+ — below MIN_PYTHON, not equal to it. Absent,
        # the `-m` form cannot be identified at all — see
        # `_preflight_is_the_program`, which records that as a decision rather
        # than a surprise. The script form does not consult it.
        orig_argv = getattr(sys, "orig_argv", None)
    if _preflight_is_the_program(main_file, argv, orig_argv, preflight_path):
        return ""
    venv = os.path.join(str(root), ".venv")
    try:
        st = os.lstat(venv)
    except FileNotFoundError:
        return ""  # No workspace venv: nothing to be inconsistent with.
    except OSError:
        return _DEFECT
    if not stat.S_ISDIR(st.st_mode):
        try:
            # `.venv` may legitimately BE a symlink to a directory; lstat sees
            # the link. Anything else present-but-not-a-directory, and a link
            # whose target is gone, is a defect rather than an absence.
            if not stat.S_ISDIR(os.stat(venv).st_mode):
                return _DEFECT
        except OSError:
            return _DEFECT
    if str(prefix) == str(base_prefix):
        return _DEFECT  # Not in any virtualenv — the stock-macOS python3 case.
    try:
        # `os.path.samefile`, never `==` on resolved paths: macOS and Windows
        # are case-insensitive by default, so two spellings of one directory
        # would compare unequal and block a correctly activated run.
        same = os.path.samefile(str(prefix), venv)
    except OSError:
        return _DEFECT
    return "" if same else _DEFECT


if _defect := host_interpreter_defect():
    print(_defect, file=sys.stderr)
    sys.exit(2)
