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

REPO = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO / ".claude" / "hooks"
SETTINGS = REPO / ".claude" / "settings.json"
SKILL_HOOK = HOOKS_DIR / "skill-invariants.sh"
PERMS_HOOK = HOOKS_DIR / "harden-runtime-perms.sh"
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

# The four modules the skill hook can run, named here so a test can say which
# one a given edit is supposed to select. `test_the_hook_runs_test_files_that
# _exist` pins that these paths resolve; this set pins what they MEAN.
PORTABILITY = "tests/test_portability.py"
CAPABILITIES = "tests/test_capabilities.py"
AGENTS = "tests/test_claude_agents.py"
HOOKS = "tests/test_claude_hooks.py"
PREFLIGHT = "tests/test_preflight.py"
SECUREFS = "tests/test_secure_fs.py"

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


def _run_skill_hook(project_dir: Path, edited: str, path=None):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir))
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        ["bash", str(SKILL_HOOK)],
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
                         indent=None):
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
        env=dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir)))


def _selected_modules(result) -> list:
    """The module list the hook handed pytest, read back from an echoing shim.

    Substring-matching the reported tail instead is wrong in one direction that
    matters: the `--deselect` arguments name `tests/test_portability.py`
    unconditionally, so `"tests/test_portability.py" in stderr` is satisfied by
    a hook that selected something else entirely.
    """
    m = re.search(r"-m pytest (.+?) --deselect", result.stderr)
    return m.group(1).split() if m else []


def _fake_venv(root: Path, exit_code: int, lines=()):
    """A stand-in for `.venv/bin/python` with a chosen exit status.

    Shimming the interpreter rather than running the real suite is what makes
    the failure path deterministic AND fast: the check under test is how the
    hook reacts to a non-zero status, not whether the suite is currently green.

    `lines=["$*"]` turns it into an argv echo, which is how `_selected_modules`
    reads back WHICH modules a given edit chose — the shim is inside a `sh`
    script, so the line is expanded rather than printed literally.
    """
    bindir = root / ".venv" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    python = bindir / "python"
    body = ["#!/bin/sh"] + [f'echo "{line}"' for line in lines] + [f"exit {exit_code}"]
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


@needs_jq
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
                                                  PREFLIGHT, SECUREFS], (
            "a skill edit must run the two modules that read skill prose and "
            "pay for neither of the other two")

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
        ("scripts/pre-commit-pii-scan.sh", PORTABILITY),
        ("scripts/pre-commit-pii-scan.sh", SECUREFS),
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
            if r.returncode != 2 or module not in _selected_modules(r):
                wrong.append(f"{relative} -> {_selected_modules(r) or 'nothing'}"
                             f" (needs {module})")
        assert not wrong, (
            "these watched paths do not reach the module that reads them: "
            f"{wrong}")

    def test_a_per_file_arm_never_selects_less_than_the_generic_one(
            self, quiescent_workspace):
        """`case` is first-match-wins, so an engine module with its OWN arm gets
        exactly what that arm lists — never the `agents/lib/*.py` arm below it.

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
        """
        script = (HOOKS_DIR / "skill-invariants.sh").read_text()
        arms = re.findall(r"^\s{8}((?:agents/lib/\w+\.py\|?)+)\)$",
                          script, re.MULTILINE)
        paths = sorted({p for arm in arms for p in arm.split("|")})
        assert len(paths) >= 3, (
            f"no per-file engine arms found — the pattern stopped matching the "
            f"script, so this test now asserts nothing (found {paths})")

        generic = {PREFLIGHT, SECUREFS, PORTABILITY}
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
            f"than `agents/lib/*.py` would: {short}")

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
        "docs/roadmap/2026-tax-planning-features.md",
    ])
    def test_an_unwatched_source_edit_stays_silent(self, tmp_path, relative):
        """The counterweight to the parametrization above: the watched set is a
        list, not a mood.

        `agents/lib/*.py` USED to be the sharpest case here, on the reasoning
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
        invariant test loads, and a roadmap document outside
        `docs/contributing`. `result_template.json` was the template here until
        `test_portability.py` began building a prior-year return out of it, and
        it moved to the watched side in the same edit — the two lists are one
        claim seen from both directions, so a path leaves this one by joining
        that one, never by being quietly dropped.

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

        Only `REPO / "…"` literals are recoverable this way. An import edge
        (`test_claude_agents.py` reaching `tax_law_fetcher.py`) and a glob
        inside `skill_text()` are invisible here, which is why the explicit
        list above exists alongside this.
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
        for module in (PORTABILITY, CAPABILITIES, AGENTS, HOOKS, PREFLIGHT,
                       SECUREFS):
            for match in chain.finditer((REPO / module).read_text()):
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
                          if m not in selected]
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
                                        HOOKS, PREFLIGHT, SECUREFS], (
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
        cases outright. Seeing the concurrent write as a change selects the one
        module that reads AGENTS.md; distrusting the stamp selects all four.
        Verified by mutation, replacing the stamp's `touch -r` with `true`.
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
        assert _selected_modules(after) == [PORTABILITY], (
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
                                        HOOKS, PREFLIGHT, SECUREFS], (
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
        assert _selected_modules(red) == [PORTABILITY, CAPABILITIES, PREFLIGHT,
                                          SECUREFS]
        assert _stamp_body(tmp_path) == [SKILL_STAMP_VERSION, "portability",
                                         "capabilities", "preflight",
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
        # test has already closed raises ValueError on Python 3.12 — the version
        # CI pins and requirements.txt is compiled for. It does NOT raise on
        # 3.13+, so a contributor venv on a newer interpreter sees this test
        # pass and learns nothing. Dropping the reference is what the sibling
        # test at the slow-full-walk case already does.
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
        # stream raises ValueError on Python 3.12 — the version CI pins and
        # requirements.txt is compiled for — but not on 3.13+. Dropping the
        # reference keeps the test honest on both.
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
        start = block.index("MEASURED COSTS")
        end = block.index("Running the union of every changed path")
        costs = block[start:end]

        # ONE collection, attributed per file from the node ids. This test runs
        # inside the module MEASURED COSTS calls at least 84% of a cold pass,
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
        # The per-module decomposition rows: "tests/test_x.py   N tests".
        rows = {p: int(n) for p, n in
                re.findall(r"(tests/test_\w+\.py)\s+(\d+) tests", costs)}
        assert len(rows) == 6, (
            f"the decomposition names {sorted(rows)} — six module rows are "
            "what the derived rows below are summed from")

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

        # The derived rows. A skill edit selects the four non-hook, non-agent
        # modules; a cold pass adds the other two. The block calls this half
        # EXACT, and a decomposition error shows up here first.
        skill_edit = sum(rows[p] for p in rows if p not in (
            "tests/test_claude_hooks.py", "tests/test_claude_agents.py"))
        cold = sum(rows.values())
        stated_skill = int(re.search(r"skill \.md edit\s+(\d+) tests", costs).group(1))
        stated_cold = int(re.search(r"cold pass, all six modules\s+(\d+) tests",
                                    costs).group(1))
        assert stated_skill == skill_edit, (
            f"the skill-edit row reads {stated_skill}; its four parts sum to "
            f"{skill_edit}")
        assert stated_cold == cold, (
            f"the cold-pass row reads {stated_cold}; its six parts sum to {cold}")

        stated_suite = int(re.search(r"`pytest tests/`\s+(\d+) tests", costs).group(1))
        # The scale row is the PLAIN invocation, so its gap to collection is
        # only the platform skips the block reconciles in prose.
        gap = suite_collects - stated_suite
        assert 0 <= gap <= 4, (
            f"`pytest tests/` collects {suite_collects}; the block's scale row "
            f"reads {stated_suite}, a gap of {gap} that its stated platform "
            "skips do not explain — re-derive the whole block, do not patch "
            "this row")
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
            f"the other five) give at least {derived}%")

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
