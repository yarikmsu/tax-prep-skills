"""Tests for the engine's host-interpreter guard (agents/lib/__init__.py).

P1: with a workspace `.venv` present, importing the engine on an interpreter
    that is not that venv fails loudly (exit 2) instead of computing a return
    against whatever dependency versions the host happens to have. With no
    `.venv`, the guard says nothing at all — CI and every pre-`tax-setup` clone
    are in that state, and a gate that fires on a correct state teaches its
    reader to reason past it.
P2: the guard and `preflight._venv_error` agree. The rule is deliberately
    written twice (importing preflight here would invert the dependency
    direction and pull 2,400 lines into every engine process), so the two
    copies are pinned equal the way `.gitignore` and `IGNORE_PATTERNS` are.
"""
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agents.lib as G  # noqa: E402
from tests.engine_tree import (  # noqa: E402
    GUARD as ENGINE_GUARD,
    engine_files,
    engine_relpath,
)
from agents.lib import preflight as P  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# Hook input declarations: (priority, path patterns). Lower priority wins.
# Parsed as literals by the selector; this module is never imported by it.
WATCHES = (
    (120, ('tests/test_claude_hooks.py',)),
    (140, ('agents/lib/capabilities.py', 'agents/lib/rollover.py')),
    (150, ('agents/lib/tax_law_fetcher.py',)),
    (155, ('agents/lib/tie_out.py',)),
    (160, ('agents/lib/ptet.py',)),
    (170, ('agents/lib/tax_computations', 'agents/lib/tax_computations/*')),
    (180, ('tests/test_claude_agents.py',)),
    (190, ('agents/lib/secure_fs.py',)),
    (210, ('agents/lib',)),
    (240, ('agents/lib/*',)),
    (250, ('tests/conftest.py',)),
    (260, ('tests/test_preflight.py',)),
    (270, ('tests/test_secure_fs.py',)),
    (280, ('tests/test_host_guard.py',)),
    (300, ('tests/test_capabilities.py',)),
    (330, ('tests/skill_text.py',)),
    (340, ('tests/engine_tree.py',)),
    (440, ('tests/test_portability.py',)),
    (450, ('tests',)),
    (470, ('tests', 'tests/*.py')),
)
LIB = REPO / "agents" / "lib"

# A host interpreter, spelled the way `sys` spells it when no venv is active.
HOST = "/usr/local/stock-python"

# The sentence the refusal is recognisable by. One line of it, not the whole
# message: the silent-state test asserts this is ABSENT, and a marker that
# appears three times over would make a count of it read oddly.
REFUSAL_MARK = "The tax engine was imported by an interpreter"


def _defect(root, prefix, base_prefix=HOST, orig_argv=("python3", "-c", "")):
    return G.host_interpreter_defect(root=root, prefix=prefix,
                                     base_prefix=base_prefix,
                                     orig_argv=list(orig_argv))


class TestTheRule:
    def test_no_workspace_venv_is_no_opinion(self, tmp_path):
        """The CI and fresh-clone state. Nothing to be inconsistent with."""
        assert _defect(tmp_path, HOST, base_prefix=HOST) == ""

    def test_running_inside_the_workspace_venv_passes(self, tmp_path):
        venv = tmp_path / ".venv"
        venv.mkdir()
        assert _defect(tmp_path, venv) == ""

    def test_a_symlinked_spelling_of_the_same_venv_passes(self, tmp_path):
        """`os.path.samefile`, not `==` on strings.

        macOS and Windows are case-insensitive by default and a workspace is
        routinely reached through a symlink, so two spellings of one directory
        must not read as two directories — that is a refusal of a correctly
        activated run, with no way out.
        """
        venv = tmp_path / ".venv"
        venv.mkdir()
        link = tmp_path / "same-venv"
        link.symlink_to(venv, target_is_directory=True)
        assert _defect(tmp_path, link) == ""

    def test_no_virtualenv_at_all_blocks(self, tmp_path):
        """The stock-macOS `/usr/bin/python3` case: prefix == base_prefix."""
        (tmp_path / ".venv").mkdir()
        assert _defect(tmp_path, HOST, base_prefix=HOST) != ""

    def test_a_different_virtualenv_blocks(self, tmp_path):
        (tmp_path / ".venv").mkdir()
        other = tmp_path / "some-other-venv"
        other.mkdir()
        assert _defect(tmp_path, other) != ""

    def test_a_dangling_venv_symlink_blocks(self, tmp_path):
        """Present and uncomparable is a DEFECT, not an absence.

        `os.lstat` succeeds on a dangling link, which is the whole reason the
        check is written on `lstat` plus an explicit follow rather than on
        `Path.is_dir()` — that swallows every OSError and answers False, so an
        unstattable `.venv` would read as "no venv here" and fail OPEN.
        """
        (tmp_path / ".venv").symlink_to(tmp_path / "gone")
        assert _defect(tmp_path, tmp_path / ".venv") != ""

    def test_a_venv_that_is_a_regular_file_blocks(self, tmp_path):
        (tmp_path / ".venv").write_text("", encoding="utf-8")
        assert _defect(tmp_path, tmp_path / "anywhere") != ""

    def test_a_venv_symlinked_to_a_directory_is_a_venv(self, tmp_path):
        """A link is a legitimate spelling; only a broken one is a defect."""
        real = tmp_path / "elsewhere"
        real.mkdir()
        (tmp_path / ".venv").symlink_to(real, target_is_directory=True)
        assert _defect(tmp_path, real) == ""


class TestTheExemption:
    """Preflight, and only preflight, may run on the host interpreter.

    It has to be able to REPORT this defect with its own full remedy, and under
    `-m agents.lib.preflight` this package imports before preflight's own code
    runs, so without an exemption preflight would never start.

    The rule ASKS CPython what the program is — `__main__.__file__` first, and
    `sys.argv[0]` when there is none — instead of re-deriving it by walking the
    interpreter's option table. These cases are grouped by which of those two
    answers, because that is what the rule branches on, and the two holes the
    walk shipped are pinned as the regressions they were.
    """

    def _blocked_state(self, tmp_path):
        """A workspace with a venv, judged from an interpreter that is not it,
        plus a real `preflight.py` for the exemption to be measured AGAINST."""
        (tmp_path / ".venv").mkdir()
        preflight = tmp_path / "preflight.py"
        preflight.write_text("", encoding="utf-8")
        return tmp_path, preflight

    def _verdict(self, root, preflight, main_file=None, argv=("-c", ""),
                 orig_argv=("python3", "-c", "")):
        """Every input EXPLICIT, including the two that default to this process.

        Left to default they read whatever program is running the suite, so a
        rule that consulted the wrong one would be measured against pytest and
        every assertion here would still hold. `test_the_defaults_read_this_
        process` is the one case that does read them, and it is the reason
        these do not.
        """
        return G.host_interpreter_defect(
            root=root, prefix=HOST, base_prefix=HOST, main_file=main_file,
            argv=None if argv is None else list(argv),
            orig_argv=None if orig_argv is None else list(orig_argv),
            preflight_path=preflight)

    # ---- the program is a SCRIPT: `__main__.__file__` answers it ----------

    @pytest.mark.parametrize("options", [
        [],
        ["-B"],
        ["-X", "dev"],
        ["-Xdev"],
        # THE SECOND HOLE, reported against PR #82 and fixed by deleting the
        # rule rather than correcting it. "The last letter of the cluster
        # spends the next token" was applied to the WHOLE token, so an attached
        # option value that happens to end in c, m, W or X was read as
        # operand-taking and preflight lost its exemption under it: end to end,
        # `python3 -X tracemalloc agents/lib/preflight.py` printed the refusal
        # once per roster module and never rendered preflight's own diagnosis.
        ["-Xtracemalloc"],
        ["-Wignore::DeprecationWarning:pkg.algorithm"],
        # `--` is end-of-options, and the walk read it as a long option and
        # refused everything after it.
        ["--"],
        ["--check-hash-based-pycs", "always"],
    ])
    def test_the_script_form_is_exempt_under_any_option(self, tmp_path,
                                                        options):
        """CPython sets `__main__.__file__` to the script before the script's
        own imports run, so the options in front of it cannot matter — which is
        the whole reason the table is no longer consulted. A future interpreter
        may add options none of these spell, and this stays true."""
        root, preflight = self._blocked_state(tmp_path)
        assert self._verdict(
            root, preflight, main_file=str(preflight), argv=[str(preflight)],
            orig_argv=["python3", *options, str(preflight)]) == ""

    def test_a_relative_spelling_of_the_same_file_is_exempt(self, tmp_path,
                                                            monkeypatch):
        """`os.path.samefile`, so identity survives how the path was written."""
        root, preflight = self._blocked_state(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert self._verdict(root, preflight,
                             main_file="./preflight.py") == ""

    def test_a_symlink_to_the_same_file_is_exempt(self, tmp_path):
        """A clone reached through a link is still this engine's preflight."""
        root, preflight = self._blocked_state(tmp_path)
        link = tmp_path / "linked-preflight.py"
        link.symlink_to(preflight)
        assert self._verdict(root, preflight, main_file=str(link)) == ""

    def test_a_stranger_named_preflight_is_NOT_exempt(self, tmp_path):
        """The first hole reported against PR #82.

        A basename check granted the exemption to any script anywhere called
        `preflight.py`. With a workspace `.venv` present, such a script could
        import the engine and compute on the host interpreter with no refusal —
        an exemption whose predicate is wider than its stated scope, which is a
        hole by construction.
        """
        root, preflight = self._blocked_state(tmp_path)
        impostor = tmp_path / "elsewhere" / "preflight.py"
        impostor.parent.mkdir()
        impostor.write_text("", encoding="utf-8")
        assert self._verdict(root, preflight, main_file=str(impostor))

    def test_a_program_that_does_not_exist_is_NOT_exempt(self, tmp_path):
        """Unverifiable is not verified, and the bias here runs to refusing."""
        root, preflight = self._blocked_state(tmp_path)
        assert self._verdict(root, preflight, main_file="/gone/preflight.py")

    @pytest.mark.parametrize("main_file", ["<stdin>", "<string>", ""])
    def test_a_main_file_that_is_not_a_path_is_NOT_exempt(self, tmp_path,
                                                          main_file):
        """`python3 - < script` sets `__main__.__file__` to the literal string
        `<stdin>`, which no `samefile` can resolve. Refusing is the answer that
        needs no special case."""
        root, preflight = self._blocked_state(tmp_path)
        assert self._verdict(root, preflight, main_file=main_file,
                             argv=["-", ""], orig_argv=["python3", "-"])

    # ---- no `__main__.__file__`: `sys.argv[0]` answers it -----------------

    def _dash_m(self, root, preflight, module, options=(), args=(),
                attached=False):
        """The argv PAIR CPython produces for `-m`, built the way it builds it.

        `sys.argv` holds `['-m', *the module's own arguments]` until runpy
        rewrites `argv[0]` to the resolved module path — which happens AFTER
        this package is imported, so this is the state the guard actually sees.
        The module name is recovered from the two lengths, which is why an
        option list of any shape has to be exercised here.
        """
        head = [f"-m{module}"] if attached else ["-m", module]
        return self._verdict(
            root, preflight, main_file=None, argv=["-m", *args],
            orig_argv=["python3", *options, *head, *args])

    @pytest.mark.parametrize("args", [(), ("--workspace", "."), ("-m", "x")])
    @pytest.mark.parametrize("attached", [False, True])
    @pytest.mark.parametrize("options", [(), ("-B",), ("-X", "dev"),
                                         ("-Xdev",), ("-B", "-S", "-X", "dev")])
    def test_the_dash_m_form_is_exempt(self, tmp_path, options, attached,
                                       args):
        """The arithmetic, over every shape that moves the module's index.

        `('-m', 'x')` as the module's OWN arguments is the case a scan would
        get wrong and this cannot: nothing is searched for, so a second `-m`
        further right is just a string.
        """
        root, preflight = self._blocked_state(tmp_path)
        assert self._dash_m(root, preflight, "agents.lib.preflight",
                            options=options, args=args,
                            attached=attached) == ""

    @pytest.mark.parametrize("module", ["agents.lib.preflight_extra",
                                        "agents.lib.tax_computations",
                                        "agents.lib", "preflight",
                                        "agents.lib.preflight.extra"])
    @pytest.mark.parametrize("attached", [False, True])
    def test_another_module_under_dash_m_is_not(self, tmp_path, module,
                                                attached):
        """Anchored, never a prefix or a substring."""
        root, preflight = self._blocked_state(tmp_path)
        assert self._dash_m(root, preflight, module, attached=attached)

    @pytest.mark.parametrize("argv,orig_tail", [
        # A `-c` body that merely NAMES preflight is a bare interpreter
        # computing on the host, not preflight reporting on it.
        (["-c"], ["-c", "import agents.lib.preflight"]),
        # THE FIRST HOLE, reported against PR #82, in both spellings. Only the
        # bare token `-c` ended the walk, so an ATTACHED `-c<code>` fell through
        # to the generic option branch, was skipped, and the NEXT token —
        # preflight's own path — was read as the program. That took the
        # exemption and imported the engine on a host interpreter with no
        # refusal at all. CPython writes `-c` into `argv[0]` for both, which is
        # what closes it without learning a spelling.
        (["-c", "{preflight}"], ["-c", "import agents.lib", "{preflight}"]),
        (["-c", "{preflight}"], ["-cimport agents.lib", "{preflight}"]),
        (["-c", "{preflight}"],
         ["-X", "dev", "-cimport agents.lib", "{preflight}"]),
        (["-c", "{preflight}"], ["-Xdev", "-cimport agents.lib", "{preflight}"]),
    ])
    def test_a_dash_c_body_is_never_exempt(self, tmp_path, argv, orig_tail):
        root, preflight = self._blocked_state(tmp_path)

        def fill(seq):
            return [t.format(preflight=preflight) for t in seq]

        assert self._verdict(root, preflight, main_file=None, argv=fill(argv),
                             orig_argv=["python3", *fill(orig_tail)])

    def test_an_empty_argv_is_not_exempt(self, tmp_path):
        """An embedded interpreter can leave `sys.argv` empty. There is no
        program to be preflight, and the bias runs to refusing."""
        root, preflight = self._blocked_state(tmp_path)
        assert self._verdict(root, preflight, main_file=None, argv=[],
                             orig_argv=["python3"])

    # ---- the wiring, and the versions ------------------------------------

    def test_the_defaults_read_this_process(self, tmp_path, monkeypatch):
        """Every case above passes its inputs explicitly, so not one of them
        can see the guard reading the WRONG attribute off `sys` — a rule wired
        to something that is never preflight would satisfy all of them.

        Both directions, on the real `__main__`: as the suite runs it, and with
        its `__file__` moved to the stand-in preflight.
        """
        root, preflight = self._blocked_state(tmp_path)
        main = sys.modules["__main__"]
        assert G.host_interpreter_defect(root=root, prefix=HOST,
                                         base_prefix=HOST,
                                         preflight_path=preflight) != "", (
            "the program running this suite is not preflight, so nothing here "
            "may be exempt")
        monkeypatch.setattr(main, "__file__", str(preflight), raising=False)
        assert G.host_interpreter_defect(root=root, prefix=HOST,
                                         base_prefix=HOST,
                                         preflight_path=preflight) == ""

    def test_the_script_form_needs_no_orig_argv(self, tmp_path):
        """Below MIN_PYTHON, preflight is exactly what still has to run.

        `sys.orig_argv` is 3.10+, so on the 3.9 a stock macOS ships there is
        none — and the script form no longer consults it at all, because
        `__main__.__file__` exists on every version. The walk this replaced had
        to synthesise the interpreter token `orig_argv` puts at index 0, and
        got it wrong once: preflight blocked itself on the one interpreter it
        exists to diagnose (`test_preflight.py::
        TestTheDiagnosisSurvivesAnOldInterpreter` is the end-to-end half).
        """
        root, preflight = self._blocked_state(tmp_path)
        assert self._verdict(root, preflight, main_file=str(preflight),
                             argv=[str(preflight)], orig_argv=None) == ""

    def test_the_pre_3_10_dash_m_form_is_knowingly_not_exempt(self, tmp_path):
        """Recorded as a decision, not discovered as a surprise.

        Recovering the module name needs `orig_argv`, which arrived in 3.10 —
        below the floor, not at it — so on an older host there is none and
        `sys.argv` holds only `-m` while this package imports.
        So `-m agents.lib.preflight` on a 3.9 host gets the guard rather than
        preflight's own verdict. Fail-CLOSED, and the message names
        `sh agents/lib/run.sh agents/lib/preflight.py`; the script-path form
        above is exempt on every version, and is what all twenty-five shipped
        invocations use.
        """
        root, preflight = self._blocked_state(tmp_path)
        message = self._verdict(root, preflight, main_file=None, argv=["-m"],
                                orig_argv=None)
        assert message
        assert "sh agents/lib/run.sh agents/lib/preflight.py" in message

    def test_there_is_no_environment_variable_opt_out(self):
        """`run.sh` resets `TAXPY` because an exported variable from the
        caller's shell would otherwise choose the interpreter. An env opt-out
        here is the same hole, reached one layer up.
        """
        source = (LIB / "__init__.py").read_text(encoding="utf-8")
        assert "os.environ" not in source
        assert "getenv" not in source

    def test_the_option_table_is_gone(self):
        """The rule is that CPython is asked, and a table creeping back is how
        it would stop being. Both holes above lived in one, and a table has to
        stay complete against every future interpreter to stay safe.
        """
        source = (LIB / "__init__.py").read_text(encoding="utf-8")
        assert "_SPENDS_THE_NEXT_TOKEN" not in source, (
            "the option-table walk is back in the guard — it is what shipped "
            "the attached `-c` hole and the attached-value misread, and "
            "neither is a spelling that can be added to a list")


class TestTheMessage:
    """A refusal names what failed and what to run, and no path.

    `run.sh` drops `$0` from its own errors for the reason AGENTS.md §1 gives:
    a workspace folder may carry a taxpayer's name or SSN, and `sys.prefix` is
    a path under it.
    """

    @pytest.fixture(params=["no-venv-at-all", "another-venv", "dangling",
                            "regular-file"])
    def blocking(self, request, tmp_path):
        secret = tmp_path / "returns-for-Jane-Q-Taxpayer"
        secret.mkdir()
        if request.param == "dangling":
            (secret / ".venv").symlink_to(secret / "gone")
            return _defect(secret, secret / ".venv"), secret, secret / ".venv"
        if request.param == "regular-file":
            (secret / ".venv").write_text("", encoding="utf-8")
            return _defect(secret, secret / "x"), secret, secret / "x"
        (secret / ".venv").mkdir()
        if request.param == "another-venv":
            other = secret / "other"
            other.mkdir()
            return _defect(secret, other), secret, other
        return (_defect(secret, HOST, base_prefix=HOST), secret, Path(HOST))

    def test_it_names_the_launcher_and_preflight(self, blocking):
        message, _root, _prefix = blocking
        assert message
        assert "agents/lib/run.sh" in message
        assert "preflight" in message

    def test_it_names_no_path(self, blocking):
        message, root, prefix = blocking
        assert str(root) not in message
        assert str(prefix) not in message
        assert "Jane-Q-Taxpayer" not in message


class TestAgreementWithPreflight:
    """The two copies of one rule, pinned over the three states that matter.

    Written twice on purpose (decision 5 of the plan): `__init__` importing
    `preflight` would pull `importlib.metadata`, `dataclasses` and 2,400 lines
    into every engine process and invert the dependency direction — preflight
    has to stay runnable when the guard is what is wrong.
    """

    def _pretend(self, monkeypatch, prefix, base=HOST):
        monkeypatch.setattr(sys, "prefix", str(prefix))
        monkeypatch.setattr(sys, "base_prefix", str(base))

    def _intact_venv(self, root):
        venv = root / ".venv"
        bin_dir = venv / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "activate").write_text("export VIRTUAL_ENV=%s\n" % venv, encoding="utf-8")
        (bin_dir / "python3").write_text("", encoding="utf-8")
        return venv

    def test_no_venv_both_clean(self, tmp_path, monkeypatch):
        self._pretend(monkeypatch, HOST)
        assert bool(P._venv_error(tmp_path)[0]) is False
        assert bool(_defect(tmp_path, HOST, base_prefix=HOST)) is False

    def test_inside_the_venv_both_clean(self, tmp_path, monkeypatch):
        venv = self._intact_venv(tmp_path)
        self._pretend(monkeypatch, venv)
        assert bool(P._venv_error(tmp_path)[0]) is False
        assert bool(_defect(tmp_path, venv)) is False

    def test_outside_the_venv_both_block(self, tmp_path, monkeypatch):
        self._intact_venv(tmp_path)
        self._pretend(monkeypatch, HOST)
        assert bool(P._venv_error(tmp_path)[0]) is True
        assert bool(_defect(tmp_path, HOST, base_prefix=HOST)) is True


class TestTheWiring:
    """That the module-level call EXISTS — the function tests cannot show it.

    Run in a subprocess against a COPY of `__init__.py` in a throwaway tree, so
    the assertion is about the shipped file and touches neither this repo's
    `.venv` nor this process's already-imported `agents.lib`.
    """

    def _tree(self, tmp_path, with_venv):
        pkg = tmp_path / "agents" / "lib"
        pkg.mkdir(parents=True)
        (tmp_path / "agents" / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "__init__.py").write_text((LIB / "__init__.py").read_text(encoding="utf-8"), encoding="utf-8")
        if with_venv:
            (tmp_path / ".venv").mkdir()
        return tmp_path

    def _run(self, root, argv_tail):
        return subprocess.run(
            [sys.executable, *argv_tail],
            cwd=str(root), capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"})

    def test_importing_with_a_foreign_venv_present_exits_two(self, tmp_path):
        root = self._tree(tmp_path, with_venv=True)
        done = self._run(root, ["-c", "import agents.lib"])
        assert done.returncode == 2, done.stderr
        assert REFUSAL_MARK in done.stderr
        assert "agents/lib/run.sh" in done.stderr
        assert done.stdout == ""

    def test_importing_with_no_venv_is_silent(self, tmp_path):
        """THE GUARD says nothing — not "stderr is empty".

        The child inherits this environment, so any interpreter-level chatter
        landed on the same stream and made the guard look loud: verified with
        `PYTHONVERBOSE=1`, which fails an emptiness assertion outright, and a
        malformed `.pth` in site-packages does the same. Neither is a fact
        about this rule. Isolating the child instead (`-I`, `-E`) is not the
        fix either: both drop the `PYTHONPATH` this tree is reached through.
        """
        root = self._tree(tmp_path, with_venv=False)
        done = self._run(root, ["-c", "import agents.lib"])
        assert done.returncode == 0, done.stderr
        assert REFUSAL_MARK not in done.stderr, (
            "the guard refused a workspace with no `.venv` — that state is CI "
            "and every clone before `tax-setup` Step 6")
        assert done.stdout == ""

    def test_the_preflight_spelling_is_exempt_end_to_end(self, tmp_path):
        root = self._tree(tmp_path, with_venv=True)
        (root / "agents" / "lib" / "preflight.py").write_text(
            "import agents.lib\nprint('preflight ran')\n", encoding="utf-8")
        done = self._run(root, ["agents/lib/preflight.py"])
        assert done.returncode == 0, done.stderr
        assert "preflight ran" in done.stdout


class TestTheRefusalReachesPytestAsAStatus:
    """`tests/conftest.py` turns the refusal into ONE message and exit 2.

    Forty test modules import the engine, so without that file a single
    environmental fact becomes forty collection errors and the reader scrolls
    past the sentence that says what to do.

    IT HAD TO BE A HOOK, and that is what these assert. `pytest.exit` raises
    `_pytest.outcomes.Exit`, which pytest honours — status and all — only from
    inside a hook. Raised in the conftest's module BODY it is caught by
    `_importconftest`'s own `except Exception` and re-raised as a
    `ConftestImportFailure`: the run ended with an "ImportError while loading
    conftest" banner, a full traceback — the second copy of the message that
    file exists to prevent — and status 4, a USAGE error, while the
    `returncode=2` argument sat there doing nothing. Reported against PR #82
    and reproduced on the pytest the lock pins. Nothing pinned the status,
    which is why it shipped.

    Driven end to end rather than read off the source: the defect was that a
    correct-looking `pytest.exit(..., returncode=2)` call was ignored, and no
    scan of the text can see that.
    """

    def _tree(self, tmp_path, with_venv=True):
        pkg = tmp_path / "agents" / "lib"
        pkg.mkdir(parents=True)
        (tmp_path / "agents" / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "__init__.py").write_text((LIB / "__init__.py").read_text(encoding="utf-8"), encoding="utf-8")
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "conftest.py").write_text(
            (REPO / "tests" / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8")
        (tests / "test_placeholder.py").write_text(
            "def test_the_run_reached_a_test():\n    assert True\n", encoding="utf-8")
        if with_venv:
            (tmp_path / ".venv").mkdir()
        return tmp_path

    def _pytest(self, root):
        return subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q",
             "-p", "no:cacheprovider"],
            cwd=str(root), capture_output=True, text=True, timeout=120,
            env={**os.environ, "PYTHONPATH": str(root),
                 "PYTHONDONTWRITEBYTECODE": "1"})

    def test_a_refused_interpreter_exits_with_the_engines_own_status(
            self, tmp_path):
        """2 is preflight's "blocked" and every skill branches on it. 4 is
        pytest's "your command line was wrong", which is a different thing and
        is what this used to produce."""
        done = self._pytest(self._tree(tmp_path))
        assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)

    def test_the_refusal_is_reported_once_and_without_a_traceback(self,
                                                                  tmp_path):
        done = self._pytest(self._tree(tmp_path))
        combined = done.stdout + done.stderr
        assert combined.count(REFUSAL_MARK) == 1, (
            "the refusal is printed more than once — the whole reason this "
            f"conftest exists:\n{combined}")
        assert "ImportError while loading conftest" not in combined
        assert "Traceback" not in combined, combined

    def test_a_workspace_the_engine_accepts_runs_the_suite(self, tmp_path):
        """The control, and without it every assertion above holds against a
        conftest that refuses everything."""
        root = self._tree(tmp_path, with_venv=False)
        done = self._pytest(root)
        assert done.returncode == 0, (done.stdout, done.stderr)
        assert "1 passed" in done.stdout
        assert REFUSAL_MARK not in done.stdout + done.stderr


class TestEveryModuleIsCovered:
    """The script-path form is covered because every module imports the package.

    `python3 agents/lib/scenario.py` does not import `agents.lib` by virtue of
    being under it — it imports it because the module's own second line does,
    after the `sys.path.insert` every one of them already carries. A new leaf
    module with no package import would be a silent hole, so the coverage is
    asserted statically and fails the day such a module is written.
    """

    # BY PATH, not by name, and that is the whole of the fix. The set used to
    # hold the bare string `"__init__.py"` to name ONE file — the package that
    # IS the guard. W2 gave the engine four more initialisers and the exemption
    # silently covered all five, so `tax_computations/federal/__init__.py` and
    # `state/__init__.py` were excused a check they had never passed. The
    # roster count did not move and nothing went red: the same shape that took
    # the federal engine out of `secure_fs`'s sweep one slice earlier.
    EXEMPT = {
        # The package itself — it IS the guard.
        ENGINE_GUARD,
        # Exempt BY DESIGN: preflight must run on the host interpreter to
        # report the defect. It reaches its siblings through `importlib`
        # inside `_load`, not through a module-level package import.
        LIB / "preflight.py",
    }

    def test_every_engine_module_imports_the_package(self):
        pattern = re.compile(r"^(from agents\.lib\b|import agents\.lib\b)", re.M)
        missing = sorted(
            engine_relpath(path) for path in engine_files()
            if path not in self.EXEMPT
            and not pattern.search(path.read_text(encoding="utf-8")))
        assert not missing, (
            f"{missing} reach the engine without importing `agents.lib`, so "
            f"`python3 agents/lib/<module>.py` runs them on a host interpreter "
            f"with no guard. Add the package import the siblings carry; the "
            f"only exempt paths are "
            f"{sorted(engine_relpath(x) for x in self.EXEMPT)}.")

    def test_the_exemption_names_paths_and_not_filenames(self):
        """The regression this pins is the set going back to bare names.

        `"__init__.py"` reads as one file and matches five. A package
        initialiser that grows code would then keep an exemption written for
        the guard, on a check whose whole subject is whether the guard is
        reachable.
        """
        assert all(isinstance(x, Path) for x in
                   TestEveryModuleIsCovered.EXEMPT), (
            "the host-guard exemption holds a bare filename again — it must "
            "hold PATHS, or it excuses every package's `__init__.py`")
        initialisers = [p for p in engine_files() if p.name == "__init__.py"]
        assert len(initialisers) > 1, (
            "the engine holds one initialiser, so this guard is about nothing "
            "— read it again rather than keeping it")
        assert sum(p in TestEveryModuleIsCovered.EXEMPT
                   for p in initialisers) == 1, (
            "more than one `__init__.py` is exempt from the host-guard check")


class TestNoTestSpawnsABareInterpreter:
    """A test that spawns the engine must spawn THIS interpreter.

    `subprocess.run(["python3", "-m", "agents.lib.X"])` resolves through PATH,
    so it is the workspace venv only while the shell is activated — and
    AGENTS.md §7 documents the suite exactly that way, which is what makes the
    failure silent. Run as `.venv/bin/python -m pytest tests/` on the same
    tree, ten such spawns landed on a host interpreter and the guard refused
    them. The engine is right and the spawn is wrong: `sys.executable` is the
    one interpreter a test knows is the one running it.

    Read from the AST, not by regex over lines: the argv FIXTURES in this
    module are the same shape as a spawn and are not one, and
    `/usr/bin/python3` in `test_preflight.py` is a deliberate absolute path.
    Only the first element of a `.run(...)` call's argv counts.
    """

    def test_no_module_spawns_the_engine_through_path(self):
        offenders = []
        for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "run" and node.args):
                    continue
                argv = node.args[0]
                if not (isinstance(argv, ast.List) and argv.elts):
                    continue
                head = argv.elts[0]
                if (isinstance(head, ast.Constant)
                        and head.value in ("python", "python3")):
                    offenders.append(f"{path.name}:{head.lineno}")
        assert not offenders, (
            f"{offenders} spawn a subprocess through a PATH lookup for "
            f"`python3`, so they pass or fail on whether the shell was "
            f"activated. Use sys.executable.")
