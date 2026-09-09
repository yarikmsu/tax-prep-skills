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
                ("archive.py", "root=resolve_workspace()")):
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
