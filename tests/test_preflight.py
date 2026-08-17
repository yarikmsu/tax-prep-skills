"""Tests for the engine-presence preflight (agents/lib/preflight.py).

P1: the check blocks when the computation engine is missing, partially copied,
    or present but not computing correctly — and passes in a full clone.
P2: every skill that invokes the engine carries the gate, and the gate's
    load-bearing sentence (a failure to RUN the check IS the block) survives
    editing. That sentence is the only thing standing between a skill copied
    away from `agents/lib/` and a return computed with no hard gates at all,
    because the script cannot report its own absence.
"""
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import preflight as P  # noqa: E402
from tests.skill_text import CONTRIBUTOR_SKILLS, skill_text  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO / ".agents" / "skills"


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
INLINE_PY = re.compile(r"""(?:python3? +|run\.sh +)-c +(["'])""")

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
RUNS_PYTHON = re.compile(r"\bpython3?[ }]|run\.sh")


def _calls_engine(text):
    """Both invocation forms, because the skills use both.

    `agents/lib/tie_out.py` is a PATH; `python3 -m agents.lib.projection` is a
    MODULE, and the dots make it invisible to a path-only search. Matching only
    the path form silently excluded eleven skills — every planning skill, whose
    whole output is a number the taxpayer acts on, plus the two that move a
    prior year's files.
    """
    return "agents/lib/" in text or "agents.lib." in text


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
        lines = path.read_text().splitlines()
        i = 0
        while i < len(lines):
            start, unit = i, [lines[i]]
            opener = INLINE_PY.search(lines[i])
            if opener:
                quote = opener.group(1)
                # BOUNDED at the fence that closes the block. Unbounded, an
                # opening quote that never balances — an unterminated example, a
                # doubled quote inside a `-c` body — runs to EOF and makes ONE
                # unit of the rest of the file. That unit contains an activation
                # somewhere, so the audit sees a single activated invocation and
                # passes over every genuine offender below it: the file's own
                # recurring bug, arriving this time as a join that matches more
                # than it should rather than less.
                while ("".join(unit).count(quote) % 2 and i + 1 < len(lines)
                       and not lines[i + 1].startswith("```")):
                    i += 1
                    unit.append(lines[i])
            while unit[-1].rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                unit.append(lines[i])
            yield path.relative_to(REPO), start + 1, "\n".join(unit)
            i += 1


# A handful of tests assert the gate PASSES here, which is only true when the
# suite itself runs on the workspace interpreter. Run from an unchained shell —
# the exact mistake AGENTS.md §7 chains the activation to prevent — they would
# report four failures about the code when the fault is the environment. Skipped
# with a message that names the real problem instead.
# Routed through the library rather than spelled as a repo-relative literal.
# The watched-set coverage test in test_claude_hooks.py locates each module's
# inputs by scanning for that literal form, and it scans the raw file — comments
# included, which is how the first version of THIS note tripped it. Spelling the
# virtualenv that way enrols it as a repository path the hook must watch, firing
# the whole selection on every `pip install`. It is an environment probe, not an
# input.
_WORKSPACE_VENV = P._workspace_venv(REPO)
def _running_on_workspace_venv():
    """`os.path.samefile`, for the reason `_venv_error` uses it.

    `resolve()` does not canonicalise case, so on a case-insensitive filesystem
    reached through a differently-cased path a correctly activated venv compares
    unequal under `==` — and the guard below would then SKIP four green-path
    tests with a message blaming the operator. Vacuity with no failure, in a
    guard written to prevent a confusing failure.
    """
    if sys.prefix == sys.base_prefix:
        return False
    try:
        return os.path.samefile(sys.prefix, _WORKSPACE_VENV)
    except OSError:
        return False


_OUTSIDE_WORKSPACE_VENV = (
    _WORKSPACE_VENV.is_dir() and not _running_on_workspace_venv()
)
needs_workspace_venv = pytest.mark.skipif(
    _OUTSIDE_WORKSPACE_VENV,
    reason="suite is not running on the workspace .venv — activate it first: "
           "source .venv/bin/activate")


class TestEngineCheck:
    @needs_workspace_venv
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
        (pkg / "__init__.py").write_text("")
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
        """
        on_disk = {p.stem for p in (REPO / "agents" / "lib").glob("*.py")}
        listed = {e[0].rsplit(".", 1)[-1] for e in P.ENGINE_MODULES}
        assert listed == on_disk - {"__init__", "preflight"}

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
        (pkg / "__init__.py").write_text("")
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

    @needs_workspace_venv
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

    @needs_workspace_venv
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
            units = [u for _, _, u in _units(name)
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
        engine_at = [body.index(u) for _, _, u in _units(skill)
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

        A unit counts as an invocation when it both RUNS python and names the
        engine — prose about `agents/lib/` carries no `python3`, and the line
        warning against inline `-c` strings names no engine module.
        """
        offenders = [f"{path}:{n}" for path, n, unit in _units(skill)
                     if RUNS_PYTHON.search(unit) and _calls_engine(unit)
                     and LAUNCHER not in unit]
        assert not offenders, (
            f"engine invoked without activating the workspace venv: {offenders}. "
            f"Route each through `{LAUNCHER}`.")

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
        for path, n, unit in _units(skill):
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
            for line in unit[m.end():].split("\n")[1:]:
                if line.startswith("```"):
                    break
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
        for path, n, unit in _units(skill):
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
        offenders = [u for _, _, u in _shell_units("demo")
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
        units = [u for _, _, u in _shell_units("demo")]
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
        for _, _, unit in _shell_units("tax-setup"):
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

    @needs_workspace_venv
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
    unchecked — and `docs/ops/` is not in the hook's watched roots either."""

    # Documents that instruct a HUMAN OR AGENT to run an engine command. Not a
    # glob over docs/: most of them discuss the engine without invoking it, and
    # a rule that fires on prose is one that gets weakened until it fires on
    # nothing.
    DOCUMENTS = ("docs/ops/season-open-runbook.md",
                 "docs/QUICKSTART.md",
                 "README.md",
                 "CONTRIBUTING.md",
                 "AGENTS.md",
                 ".github/ISSUE_TEMPLATE/bug_report.yml")

    def test_no_document_invokes_the_engine_on_a_bare_interpreter(self):
        """The season-open runbook ran `python3 -c "from agents.lib..."` with no
        activation at all — on a stock macOS host `python3` (3.9) that dies with
        the PEP 604 TypeError this module exists to diagnose, and on a host with
        a stale global install it silently verifies source pinning for a return
        using the wrong tree. AGENTS.md §4 says every engine call goes through
        the launcher; nothing checked the documents that make them.
        """
        # `python3 -m agents.lib...`, `python3 agents/lib/...`, or a `-c` body
        # importing from the package — the three forms that actually reach the
        # engine. `python3 -m venv` and `python3 -m pytest` are not engine calls.
        pattern = re.compile(
            r"(?<!run\.sh )\bpython3?\b[^\n`]*?"
            r"(?:-m\s+agents\.lib|agents/lib/\w+\.py|from\s+agents\.lib)")
        offenders = []
        for rel in self.DOCUMENTS:
            path = REPO / rel
            assert path.exists(), f"{rel} moved — this test now checks nothing"
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{rel}:{n}: {line.strip()[:70]}")
        assert not offenders, (
            f"engine invoked on a bare interpreter outside the skills: "
            f"{offenders}. Route each through `{LAUNCHER}`.")


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
        (pkg / "__init__.py").write_text("")
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
