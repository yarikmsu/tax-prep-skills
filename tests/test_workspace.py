"""Y4: one answer to which directory holds the taxpayer's return.

The rule these pin is `agents/lib/workspace.py`'s docstring: explicit
`--workspace`, then `$TAX_WORKSPACE`, then the current directory. The last of
the three is the whole of the backwards compatibility, and the middle one is the
point — a development clone and a live return stop sharing a directory.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents.lib import workspace as W  # noqa: E402

LIB = REPO / "agents" / "lib"


@pytest.fixture(autouse=True)
def _no_inherited_root(monkeypatch):
    """Every test states its own environment.

    A developer with `TAX_WORKSPACE` exported would otherwise run a suite that
    passes for a reason none of these tests names — and the fallback case, the
    one that keeps every existing workspace working, is exactly the one such an
    export hides.
    """
    monkeypatch.delenv(W.ENV_VAR, raising=False)


class TestThePrecedence:

    def test_the_flag_wins(self, tmp_path, monkeypatch):
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv(W.ENV_VAR, str(tmp_path))
        assert W.resolve_workspace(str(other)) == other

    def test_the_variable_is_next(self, tmp_path, monkeypatch):
        monkeypatch.setenv(W.ENV_VAR, str(tmp_path))
        assert W.resolve_workspace(None) == tmp_path

    def test_the_current_directory_is_last(self, monkeypatch, tmp_path):
        """Unset, no flag, and every command means what it meant before this
        module existed — which is what every skill's `--workspace .` said."""
        monkeypatch.chdir(tmp_path)
        assert W.resolve_workspace(None) == Path(".")

    def test_an_empty_variable_reads_as_unset(self, monkeypatch, tmp_path):
        """`TAX_WORKSPACE=$(lookup-that-printed-nothing)` exports "", which
        `Path` turns into the current directory — silently, which is the one
        outcome this module exists to prevent."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(W.ENV_VAR, "   ")
        assert W.from_environment() is None
        assert W.resolve_workspace(None) == Path(".")


class TestWhatItRefuses:

    def test_an_empty_flag_is_an_error_not_a_fallback(self):
        """GIVEN-and-empty is not NOT-GIVEN. `--workspace ""` is a caller whose
        own lookup returned nothing, and falling back would write a return into
        whatever directory the command happened to run in."""
        with pytest.raises(W.WorkspaceError, match="empty path"):
            W.resolve_workspace("")

    def test_a_directory_that_is_not_there(self, tmp_path):
        with pytest.raises(W.WorkspaceError, match="no workspace at") as caught:
            W.resolve_workspace(str(tmp_path / "ghost"))
        # The message has to say where the path CAME FROM: "no workspace at
        # /x/y" is the same sentence whether the user typed it or exported it,
        # and only one of those is fixed by editing a command line.
        assert "--workspace" in str(caught.value)
        assert W.ENV_VAR in str(caught.value)

    def test_a_file_is_not_a_workspace(self, tmp_path):
        f = tmp_path / "result.json"
        f.write_text("{}")
        with pytest.raises(W.WorkspaceError, match="not a directory"):
            W.resolve_workspace(str(f))

    def test_the_variable_is_named_when_IT_is_wrong(self, monkeypatch, tmp_path):
        monkeypatch.setenv(W.ENV_VAR, str(tmp_path / "ghost"))
        with pytest.raises(W.WorkspaceError, match=r"\$" + W.ENV_VAR):
            W.resolve_workspace(None)

    def test_it_is_not_an_OSError(self):
        """`FileNotFoundError` was the first attempt and looked free — half the
        CLIs here already catch `OSError`. That is what is wrong with it: the
        other half do not, and widening them would make every unrelated IO
        fault in those commands exit 1 with a one-line message instead of the
        traceback that says where it happened."""
        assert not issubclass(W.WorkspaceError, OSError)


class TestTheArgument:

    def test_it_is_never_required_and_defaults_to_none(self):
        """argparse complaining about a missing flag would hide a
        `$TAX_WORKSPACE` that IS set — the default arrives from the resolver,
        not from the parser."""
        import argparse
        parser = argparse.ArgumentParser()
        W.add_workspace_argument(parser)
        assert parser.parse_args([]).workspace is None
        assert parser.parse_args(["--workspace", "x"]).workspace == "x"

    def test_the_help_names_the_variable(self, capsys):
        import argparse
        parser = argparse.ArgumentParser(prog="x")
        W.add_workspace_argument(parser)
        with pytest.raises(SystemExit):
            parser.parse_args(["--help"])
        assert W.ENV_VAR in capsys.readouterr().out


class TestEveryCliResolvesThroughIt:
    """The one-read-site rule, structurally. A CLI that goes back to
    `Path(args.workspace)` silently loses `$TAX_WORKSPACE` for that command
    alone — and the failure is invisible, because the flag still works."""

    def _sources(self):
        return {p.name: p.read_text(encoding="utf-8") for p in LIB.glob("*.py")}

    def test_no_module_reads_the_flag_without_the_resolver(self):
        offenders = [name for name, src in self._sources().items()
                     if "Path(args.workspace)" in src]
        assert offenders == [], (
            f"{offenders} build a path from --workspace directly; call "
            "workspace.resolve_workspace so $TAX_WORKSPACE is honoured and a "
            "bad directory is refused the same way everywhere")

    def test_every_workspace_flag_is_declared_through_the_helper(self):
        """One call site per CLI, so the flag's help says the same thing in all
        of them and the variable is documented wherever the flag is."""
        allowed = {"workspace.py",   # declares the helper
                   "preflight.py",   # a DIFFERENT --workspace; see below
                   "parse_utils.py"}  # parses its own argv, command word first
        offenders = sorted(
            name for name, src in self._sources().items()
            if name not in allowed and 'add_argument("--workspace"' in src)
        assert offenders == [], (
            f"{offenders} declare --workspace by hand; use "
            "workspace.add_workspace_argument")

    def test_the_preflight_flag_is_the_code_root_and_says_so(self):
        """The one exception, and the reason it is written down in three
        places. Everywhere else `--workspace` names the directory holding the
        RETURN; in preflight it names the root to resolve `.venv` against — the
        CODE root, which under Y4 is the checkout while the return lives
        somewhere else. Pointing it at $TAX_WORKSPACE would look for the
        engine's venv inside the taxpayer's directory, find none, and certify a
        run against an interpreter the engine is not installed in."""
        src = (LIB / "preflight.py").read_text(encoding="utf-8")
        assert "CODE root" in src
        assert f"not ${W.ENV_VAR}" in src
        # ...and it must not have been wired to the resolver by a later sweep.
        # The CALL form, not the name: preflight's own roster lists
        # `resolve_workspace` as the symbol it checks `agents.lib.workspace`
        # exports, which is the opposite of using it.
        assert "resolve_workspace(" not in src

    def test_no_module_takes_the_workspace_from_the_process_directory(self):
        """The three that spell it differently, and the one that took no flag
        at all. `archive` used `Path.cwd()` and MOVES `documents/` and
        `reviews/` under `archive/{year}/` — it would have archived whatever
        directory the command ran from, which stopped being the taxpayer's the
        moment this variable existed."""
        offenders = sorted(
            name for name, src in self._sources().items()
            if name != "preflight.py" and "Path.cwd()" in src)
        assert offenders == [], (
            f"{offenders} take the workspace from the process directory; call "
            "workspace.resolve_workspace")

    def test_the_older_spellings_still_run(self):
        """`--to` and `--current` are ALIASES, not renames: a documented
        command that starts erroring is worse than a second spelling."""
        import agents.lib.yoy_diff as Y
        import agents.lib.rollover as R
        assert 'add_workspace_argument(parser, "--current"' in \
            (LIB / "yoy_diff.py").read_text(encoding="utf-8")
        assert 'add_workspace_argument(p, "--to", dest="dst")' in \
            (LIB / "rollover.py").read_text(encoding="utf-8")
        assert Y and R  # imported to prove the modules still load

    def test_a_relative_path_means_the_workspace_in_the_positional_CLIs(self):
        """Four commands take their path as a bare positional, where the sweeps
        above cannot see them. `secure_fs taxpayer_profile.json` is the one that
        matters most: left against the CWD it would have chmod'ed a stale copy
        in the checkout while the real profile — the file AGENTS.md §1 is most
        explicit about — kept whatever mode it was created with."""
        for name, marker in (
                ("secure_fs.py", "os.path.isabs(t) else str(root / t)"),
                ("parse_utils.py", "if not Path(path).is_absolute():"),
                ("crypto_importer.py", "def _under_the_workspace"),
                # `archive` bound the resolved root to a local when it began
                # taking the run lock — the call is the same one, a line
                # earlier. The marker follows it rather than being widened to
                # something both spellings match: a marker that matches
                # anything is a sweep that sees nothing.
                ("archive.py", "root = resolve_workspace()")):
            src = (LIB / name).read_text(encoding="utf-8")
            assert marker in src, (
                f"{name} no longer resolves its positional path against the "
                "workspace — the structural sweeps cannot see this one")

    def test_every_module_that_resolves_can_report_the_refusal(self):
        """A `WorkspaceError` that escapes as a traceback is a worse answer
        than the one it replaced: `--workspace /nope` used to print a line."""
        for name, src in self._sources().items():
            if name == "workspace.py" or "resolve_workspace(" not in src:
                continue
            assert "WorkspaceError" in src, (
                f"{name} resolves a workspace but never names WorkspaceError — "
                "its refusal reaches the user as a traceback")


class TestTheCli:

    def _run(self, *args, env=None):
        environ = dict(os.environ)
        environ.pop(W.ENV_VAR, None)
        environ.update(env or {})
        return subprocess.run(
            [sys.executable, str(LIB / "workspace.py"), *args],
            capture_output=True, text=True, cwd=REPO, env=environ)

    def test_it_says_where_the_answer_came_from(self, tmp_path):
        out = self._run(env={W.ENV_VAR: str(tmp_path)})
        assert out.returncode == 0
        assert str(tmp_path.resolve()) in out.stdout
        assert f"${W.ENV_VAR}" in out.stdout

    def test_it_says_when_the_variable_is_unset(self):
        out = self._run()
        assert out.returncode == 0
        assert "is unset" in out.stdout

    def test_a_bad_directory_is_one_error_line(self, tmp_path):
        out = self._run(str(tmp_path / "ghost"))
        assert out.returncode == 1
        assert out.stdout == ""
        assert out.stderr.startswith("ERROR:")


class TestTheGatesHonourIt:
    """End to end through a real CLI, because the sweep above is textual and a
    module can import the resolver and still not use its answer."""

    def test_a_write_lands_in_the_directory_the_variable_names(self, tmp_path):
        """Run from the CHECKOUT — which is where the skills invoke `run.sh`
        from — and pointed at a workspace outside it by the variable alone. The
        file has to appear THERE and nowhere here; a resolver that were ignored
        would put a taxpayer's receipt in the git clone, which is the whole
        thing Y4 exists to stop."""
        stray = REPO / "validation_receipt.json"
        # What the checkout held BEFORE, not whether it holds anything: a
        # developer who has ever run a gate from here has one of these sitting
        # in the clone (gitignored, and exactly the cohabitation Y4 is about),
        # and an absolute assertion would fail for the wrong reason.
        before = stray.stat().st_mtime_ns if stray.exists() else None
        env = dict(os.environ, **{W.ENV_VAR: str(tmp_path)})
        out = subprocess.run(
            [sys.executable, "-m", "agents.lib.receipt", "record",
             "--gate", "capabilities", "--outcome", "PASS"],
            capture_output=True, text=True, cwd=REPO, env=env)
        assert out.returncode == 0, out.stderr
        assert (tmp_path / "validation_receipt.json").is_file()
        after = stray.stat().st_mtime_ns if stray.exists() else None
        assert after == before, (
            "the receipt landed in the checkout as well as the workspace")

    def test_a_variable_pointing_nowhere_is_refused_by_name(self, tmp_path):
        env = dict(os.environ, **{W.ENV_VAR: str(tmp_path / "ghost")})
        out = subprocess.run(
            [sys.executable, "-m", "agents.lib.receipt", "verify"],
            capture_output=True, text=True, cwd=REPO, env=env)
        assert out.returncode == 1
        assert W.ENV_VAR in out.stderr


class TestTheRunLock:
    """V10's concurrency half. `atomic_paths` made each PUBLISH atomic; two
    runs still interleave, each publishing a consistent artifact built from the
    other's half-finished inputs."""

    def _held(self, ws):
        import json
        return json.loads((ws / W._LOCK_NAME).read_text(encoding="utf-8"))

    def test_it_holds_and_releases(self, tmp_path):
        with W.workspace_lock(tmp_path, "tax-compute") as path:
            assert path.exists()
            assert self._held(tmp_path)["command"] == "tax-compute"
            assert self._held(tmp_path)["pid"] == os.getpid()
        assert not (tmp_path / W._LOCK_NAME).exists()

    def test_the_lock_file_is_owner_only(self, tmp_path):
        """It sits in the workspace beside the return. Nothing in it is the
        taxpayer's, and it is secured anyway — AGENTS.md §1's rule is the
        DIRECTORY's contents, not a judgement per file."""
        if os.name == "nt":
            pytest.skip("POSIX mode bits; the Windows path is secure_fs's own")
        with W.workspace_lock(tmp_path, "tax-compute") as path:
            assert path.stat().st_mode & 0o777 == 0o600

    def test_a_live_holder_refuses_a_second_process(self, tmp_path):
        """The pid is a DIFFERENT live process — this one's parent, which is
        alive by construction — so the re-entrancy branch cannot mask it."""
        import json
        other = os.getppid()
        (tmp_path / W._LOCK_NAME).write_text(json.dumps(
            {"pid": other, "started": "2026-09-11T09:00:00",
             "command": "tax-compute"}), encoding="utf-8")
        with pytest.raises(W.WorkspaceBusy) as e:
            with W.workspace_lock(tmp_path, "tax-generate"):
                pass
        assert "tax-compute" in str(e.value) and str(other) in str(e.value)
        assert (tmp_path / W._LOCK_NAME).exists(), "a refused run took the lock"

    def test_the_refusal_names_the_file_and_not_its_path(self, tmp_path):
        """The reader is standing in their own workspace. The absolute path
        adds nothing and carries the directory name, which for an archived
        return is the taxpayer's own."""
        import json
        (tmp_path / W._LOCK_NAME).write_text(json.dumps(
            {"pid": os.getppid(), "started": "x", "command": "tax-compute"}),
            encoding="utf-8")
        with pytest.raises(W.WorkspaceBusy) as e:
            with W.workspace_lock(tmp_path, "tax-generate"):
                pass
        assert W._LOCK_NAME in str(e.value)
        assert str(tmp_path) not in str(e.value)

    def test_busy_is_a_workspace_error(self, tmp_path):
        """Every CLI already catches `WorkspaceError` and prints one line; a
        new exception class would have needed twenty exception tuples widened,
        and the ones nobody widened would have printed a traceback."""
        assert issubclass(W.WorkspaceBusy, W.WorkspaceError)

    def test_a_stale_lock_becomes_an_ABANDONED_RECORD_not_nothing(self,
                                                                  tmp_path):
        """Reclaiming answers "may this run proceed" and throws away the more
        interesting fact: a run STARTED and never finished, so the artifacts it
        was part-way through are a mixture of two generations. A line on a
        terminal nobody is watching is not a record."""
        import json
        held = {"pid": 2 ** 22, "started": "2026-09-11T09:00:00",
                "command": "tax-compute"}
        (tmp_path / W._LOCK_NAME).write_text(json.dumps(held), encoding="utf-8")
        with W.workspace_lock(tmp_path, "tax-generate"):
            pass
        assert W.abandoned_run(tmp_path) == held
        assert (tmp_path / W.ABANDONED_NAME).exists()

    def test_the_record_is_the_lock_MOVED_not_a_copy(self, tmp_path):
        """`os.replace` is atomic, so there is no instant where the lock is
        gone and the record is not yet there — a second run arriving between
        the two would see an unlocked, unmarked workspace and believe the
        interrupted run never happened."""
        import json
        (tmp_path / W._LOCK_NAME).write_text(json.dumps(
            {"pid": 2 ** 22, "started": "x", "command": "tax-compute"}),
            encoding="utf-8")
        with W.workspace_lock(tmp_path, "tax-generate"):
            # the live lock is this run's, not the dead one's
            assert self._held(tmp_path)["command"] == "tax-generate"
        assert W.abandoned_run(tmp_path)["command"] == "tax-compute"

    def test_no_interruption_leaves_no_record(self, tmp_path):
        with W.workspace_lock(tmp_path, "tax-compute"):
            pass
        assert W.abandoned_run(tmp_path) == {}
        assert not (tmp_path / W.ABANDONED_NAME).exists()

    def test_clearing_reports_whether_there_was_one(self, tmp_path):
        assert W.clear_abandoned_run(tmp_path) is False
        (tmp_path / W.ABANDONED_NAME).write_text("{}", encoding="utf-8")
        assert W.clear_abandoned_run(tmp_path) is True
        assert W.clear_abandoned_run(tmp_path) is False

    def test_a_stale_lock_is_reclaimed_and_SAID(self, tmp_path, capsys):
        """A killed terminal is the common case, not the rare one. A lock that
        silently disappears teaches nobody that a run died."""
        import json
        (tmp_path / W._LOCK_NAME).write_text(json.dumps(
            {"pid": 2 ** 22, "started": "2026-09-11T09:00:00",
             "command": "tax-compute"}), encoding="utf-8")
        with W.workspace_lock(tmp_path, "tax-generate"):
            assert self._held(tmp_path)["command"] == "tax-generate"
        note = capsys.readouterr().err
        assert "reclaimed a stale workspace lock" in note
        assert "tax-compute" in note and "did not finish" in note
        assert W.ABANDONED_NAME in note, (
            "the note must say WHERE the interruption was recorded, or the "
            "reader has a line and no artifact")

    def test_an_unreadable_lock_is_HELD_not_reclaimed(self, tmp_path):
        """A file whose contents nothing can vouch for is not evidence its
        writer is gone — it is a run killed part-way through writing it."""
        (tmp_path / W._LOCK_NAME).write_text("{ truncated", encoding="utf-8")
        with pytest.raises(W.WorkspaceBusy, match="cannot be read"):
            with W.workspace_lock(tmp_path, "tax-compute"):
                pass
        assert (tmp_path / W._LOCK_NAME).exists()

    def test_a_lock_with_no_pid_is_HELD(self, tmp_path):
        import json
        (tmp_path / W._LOCK_NAME).write_text(json.dumps({"command": "x"}),
                                             encoding="utf-8")
        with pytest.raises(W.WorkspaceBusy):
            with W.workspace_lock(tmp_path, "tax-compute"):
                pass

    def test_it_is_reentrant_within_one_process(self, tmp_path):
        """A process cannot race itself, and refusing here would fire on
        correct data — the failure mode that teaches a caller to work around a
        gate. The inner hold leaves the OUTER record in place and does not
        unlink on the way out."""
        with W.workspace_lock(tmp_path, "tax-compute"):
            outer = self._held(tmp_path)
            with W.workspace_lock(tmp_path, "tax-compute"):
                assert self._held(tmp_path) == outer
            assert (tmp_path / W._LOCK_NAME).exists(), (
                "the inner release removed the outer hold's lock")
        assert not (tmp_path / W._LOCK_NAME).exists()

    def test_the_release_only_removes_a_lock_it_still_owns(self, tmp_path):
        """Two runs a second apart can both decide a stale lock is
        reclaimable. The loser must not delete the winner's lock on its way
        out and leave the workspace unlocked while a run is live."""
        import json
        with W.workspace_lock(tmp_path, "tax-compute"):
            (tmp_path / W._LOCK_NAME).write_text(json.dumps(
                {"pid": os.getppid(), "started": "later",
                 "command": "someone-else"}), encoding="utf-8")
        assert (tmp_path / W._LOCK_NAME).exists()
        assert self._held(tmp_path)["command"] == "someone-else"

    def test_a_failed_write_leaves_no_unreadable_lock(self, tmp_path,
                                                     monkeypatch):
        """A lock nothing can read refuses every later run with "unknown
        holder" until a person deletes it — worse than no lock at all."""
        monkeypatch.setattr(W, "secure_path",
                            lambda p: (_ for _ in ()).throw(OSError("boom")))
        with pytest.raises(OSError):
            with W.workspace_lock(tmp_path, "tax-compute"):
                pass
        assert not (tmp_path / W._LOCK_NAME).exists()

    def test_hold_workspace_releases_at_exit(self, tmp_path):
        """The CLI form. Run as a subprocess so `atexit` actually fires."""
        script = (
            "import sys, os\n"
            f"sys.path.insert(0, {str(REPO)!r})\n"
            "from agents.lib.workspace import hold_workspace\n"
            f"p = hold_workspace({str(tmp_path)!r}, 'probe')\n"
            "assert p.exists()\n"
            "print('held')\n")
        r = subprocess.run([sys.executable, "-c", script],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert "held" in r.stdout
        assert not (tmp_path / W._LOCK_NAME).exists(), (
            "hold_workspace did not release at process exit")


class TestEveryWriterOfSharedStateTakesTheLock:
    """`LOCKED_CLIS` is the judgement, made once; this pins it in BOTH
    directions, so neither a new writer that forgets the lock nor a reader that
    acquires one can land quietly."""

    def _sources(self):
        return {p.stem: p.read_text(encoding="utf-8")
                for p in sorted(LIB.glob("*.py"))}

    def test_every_declared_cli_actually_takes_it(self):
        sources = self._sources()
        for name in W.LOCKED_CLIS:
            assert name in sources, f"{name} is declared but is not a module"
            assert "hold_workspace(" in sources[name], (
                f"{name} is in LOCKED_CLIS and never calls hold_workspace — "
                "a declaration is not a lock")

    def test_no_undeclared_module_takes_it(self):
        extra = sorted(n for n, src in self._sources().items()
                       if n not in W.LOCKED_CLIS and n != "workspace"
                       and "hold_workspace(" in src)
        assert not extra, (
            f"{extra} take the run lock without being in LOCKED_CLIS. The set "
            "is where the judgement lives — a lock on a read-only command "
            "refuses an operation that cannot corrupt anything.")

    def test_the_derived_writers_are_deliberately_absent(self):
        """`pdf_render`, `filing_package` and `tax_calendar` publish files
        nothing in the pipeline reads back: a concurrent run can only make them
        STALE, which V8's receipt already detects. Pinned so the absence reads
        as a decision rather than an oversight."""
        for name in ("pdf_render", "filing_package", "tax_calendar"):
            assert name not in W.LOCKED_CLIS, name
        assert "DERIVED artifacts are deliberately NOT here" in (
            LIB / "workspace.py").read_text(encoding="utf-8")

    def test_the_set_is_not_empty_and_names_the_pipeline(self):
        assert len(W.LOCKED_CLIS) >= 7
        for name in ("tax_law_fetcher", "return_builder", "tie_out",
                     "parse_records", "archive"):
            assert name in W.LOCKED_CLIS, name
