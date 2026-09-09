"""What CI runs, and on which events — `.github/workflows/ci.yml` (W6).

The workflow ran on `pull_request` alone, and every gap this module pins follows
from widening that:

  - `github.event.pull_request.base.sha` is EMPTY on every other event, so the
    inline range expression would have handed the PII scanner `...HEAD` the
    moment a push or schedule trigger existed. The range now comes from
    `scripts/ci-pii-range.sh`, which is a script rather than a workflow
    expression precisely so it can be run here.
  - the test runner was installed by a bare `pip install pytest`, pinning
    nothing, while the runtime dependencies are hash-locked.
  - Windows behaviour is claimed in 27 places across `secure_fs` and `run.sh`,
    and one of its branches carries `# pragma: no cover - POSIX CI`.

READ AS TEXT, not as parsed YAML. Every other assertion about this file in the
suite does the same, and the reason is that PyYAML is not a dependency of this
project — adding one so a test can read a config file would put a parser in the
runtime lock for the benefit of the tests.

NOT SELECTED BY THE SKILL-INVARIANTS HOOK, and stated so nobody assumes it is:
the hook runs seven modules and this is not one of them, the same standing
`test_pii_content_scan.py` already has. Editing `ci.yml` therefore turns this red
in CI rather than at tool time. Adding it to the hook would move the cold-pass
row and cost a MEASURED COSTS sitting, which is not worth it for a file that
changes a few times a year.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
RANGE_SCRIPT = REPO / "scripts" / "ci-pii-range.sh"
DEV_IN = REPO / "requirements-dev.in"
DEV_LOCK = REPO / "requirements-dev.txt"
TEST_IN = REPO / "requirements-test.in"
TEST_LOCK = REPO / "requirements-test.txt"


WINDOWS_MODULES = ["tests/test_secure_fs.py", "tests/test_host_guard.py"]


def _extract_scan_step() -> str:
    """The shell of the pii-scan step, dedented, so it can be RUN in a stub
    tree rather than pattern-matched."""
    body = _ci().split("run: |\n", 1)
    body = _ci()[_ci().index("EVENT_NAME: ${{ github.event_name }}"):]
    body = body.split("run: |\n", 1)[1]
    lines = []
    for line in body.split("\n"):
        if line.strip() and not line.startswith("          "):
            break
        lines.append(line[10:])
    return "\n".join(lines)


def _ci() -> str:
    return CI.read_text()


def _run_range(**env):
    """The range script, with a clean environment plus `env`."""
    return subprocess.run(["bash", str(RANGE_SCRIPT)], cwd=REPO,
                          capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin:/usr/local/bin", **env})


def _declared_triggers() -> "set[str]":
    """The event names under the workflow's `on:` key.

    Hand-parsed for the reason in the module docstring, and narrowly: the block
    runs from the `on:` line to the `jobs:` line, and a trigger is a key at one
    level of indentation inside it.
    """
    text = _ci()
    start = re.search(r"^on:$", text, re.M).end()
    # To the next TOP-LEVEL key, not to `jobs:` — `concurrency:` sits between
    # them and its own two-space keys were read as triggers the moment it was
    # added, which put `group` and `cancel-in-progress` into this set.
    rest = text[start:]
    end = re.search(r"^[a-z]", rest, re.M)
    block = rest[:end.start()] if end else rest
    return {m.group(1) for m in re.finditer(r"^  ([a-z_]+):", block, re.M)}


def _handled_events() -> "set[str]":
    """The event names the range script has a `case` arm for.

    The arms are `a|b)` alternations, so the split is on `|` as well.
    """
    body = RANGE_SCRIPT.read_text()
    arms = re.findall(r"^    ([a-z_|]+)\)", body, re.M)
    return {name for arm in arms for name in arm.split("|")}


class TestEveryTriggerHasAScanRange:
    """THE invariant of this change, and the one a reviewer should read first.

    A trigger added to the workflow without an arm in the range script is a CI
    run whose PII gate cannot determine what to scan. The script fails closed on
    an unknown event rather than falling back — so the failure is a red job
    rather than a silent pass — but a red job on every push is not a working CI
    either, and nothing but this test connects the two files.
    """

    def test_the_script_handles_every_event_the_workflow_declares(self):
        missing = _declared_triggers() - _handled_events()
        assert not missing, (
            f"ci.yml triggers on {sorted(missing)} and scripts/ci-pii-range.sh "
            "has no arm for it — add one there, deciding whether that event "
            "introduces commits, rather than letting it reach the `*)` refusal")

    def test_the_workflow_declares_the_four_triggers_W6_asked_for(self):
        assert _declared_triggers() == {"pull_request", "push", "schedule",
                                        "workflow_dispatch"}

    def test_an_unknown_event_is_refused_and_not_defaulted(self):
        result = _run_range(EVENT_NAME="deployment_status")
        assert result.returncode == 1
        assert "no PII-scan range is defined" in result.stderr

    def test_no_event_yields_a_range_beginning_with_the_separator(self):
        """`...HEAD` is what the inline expression produced on a non-PR event,
        and it is the shape this whole script exists to make impossible.

        Driven with the inputs each event really carries as well as with none:
        the empty-input pass alone proved only that a refusing script prints
        nothing, which is true of a script that can never produce a range."""
        sha = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=REPO,
                             capture_output=True, text=True).stdout.strip()
        supplied = {"pull_request": {"PR_BASE_SHA": sha},
                    "pull_request_target": {"PR_BASE_SHA": sha},
                    "push": {"PUSH_BEFORE_SHA": sha}}
        produced_one = False
        for event in sorted(_handled_events()):
            for extra in ({}, supplied.get(event, {})):
                result = _run_range(EVENT_NAME=event, **extra)
                assert not result.stdout.startswith("..."), (
                    f"{event} produced {result.stdout!r}")
                assert result.returncode in (0, 1, 3), (event, result.returncode)
                if result.returncode == 0:
                    produced_one = True
                    assert result.stdout.strip().endswith("...HEAD")
                    assert len(result.stdout.split("...")[0]) == 40
        assert produced_one, (
            "no event produced a range at all — this test would pass against a "
            "script that always refuses")


class TestTheRangeItselfIsRight:
    def test_a_pull_request_scans_from_its_base(self):
        base = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=REPO,
                              capture_output=True, text=True).stdout.strip()
        result = _run_range(EVENT_NAME="pull_request", PR_BASE_SHA=base)
        assert result.returncode == 0
        assert result.stdout.strip() == f"{base}...HEAD"

    @pytest.mark.parametrize("base", ["", "HEAD", "not-a-sha", "abc123"])
    def test_a_pull_request_without_a_usable_base_REFUSES(self, base):
        """The empty case is the live one: an unset workflow expression
        interpolates to the empty string, not to an error."""
        result = _run_range(EVENT_NAME="pull_request", PR_BASE_SHA=base)
        assert result.returncode == 1
        assert "refusing to guess a range" in result.stderr

    def test_a_push_scans_from_what_was_there_before(self):
        before = subprocess.run(["git", "rev-parse", "HEAD~2"], cwd=REPO,
                                capture_output=True, text=True).stdout.strip()
        result = _run_range(EVENT_NAME="push", PUSH_BEFORE_SHA=before)
        assert result.returncode == 0
        assert result.stdout.strip() == f"{before}...HEAD"

    def test_a_first_push_scans_the_whole_history_rather_than_nothing(self):
        """`before` is forty zeros when the branch did not exist. It is a valid
        sha SHAPE naming no commit, so a range built from it resolves to
        nothing — and every commit on the branch is new."""
        result = _run_range(EVENT_NAME="push", PUSH_BEFORE_SHA="0" * 40)
        assert result.returncode == 0
        root = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"],
                              cwd=REPO, capture_output=True, text=True
                              ).stdout.strip().splitlines()[-1]
        assert result.stdout.strip() == f"{root}...HEAD"

    def test_a_scheduled_run_has_nothing_to_scan_and_says_so_distinctly(self):
        """Exit 3, not 0 and not 1. A scheduled run introduces no commits, so
        scanning is neither possible nor a failure — and the workflow has to
        tell that apart from a range it could not determine."""
        assert _run_range(EVENT_NAME="schedule").returncode == 3
        assert _run_range(EVENT_NAME="workflow_dispatch").returncode == 3

    def test_being_run_with_no_event_at_all_is_refused(self):
        result = _run_range()
        assert result.returncode == 1
        assert "EVENT_NAME is unset" in result.stderr


class TestTheWorkflowUsesTheScriptRatherThanAnExpression:
    def test_the_scan_step_calls_the_range_script(self):
        assert "bash scripts/ci-pii-range.sh" in _ci()

    def test_the_base_sha_expression_is_only_ever_passed_as_an_input(self):
        """It may reach the script as an environment variable. It may not be
        interpolated into a range, which is what made it silently empty."""
        assert "base.sha }}...HEAD" not in _ci()
        assert "PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}" in _ci()

    def test_the_shared_scanner_is_still_what_runs(self):
        assert "bash scripts/pre-commit-pii-scan.sh" in _ci()
        assert "BLOCKED_EXTENSIONS=" not in _ci(), (
            "CI is re-implementing the file-type rules instead of calling the script")

    def test_the_nothing_to_scan_code_is_distinguished_from_a_failure(self, tmp_path):
        """Treating every non-zero alike would turn a scheduled run into a
        permanent red, and treating every non-zero as "nothing to scan" would
        turn an undeterminable range into a clean pass.

        RUN, rather than matched: the substring form passed against a `case`
        that mapped 3 to `exit 1` just as happily."""
        step = _extract_scan_step()
        stub = tmp_path / "scripts"
        stub.mkdir()
        (stub / "pre-commit-pii-scan.sh").write_text("echo SCANNED \"$1\"\n",
                                                     encoding="utf-8")
        outcomes = {}
        for code, out in ((0, "abc...HEAD"), (3, ""), (1, ""), (2, "")):
            (stub / "ci-pii-range.sh").write_text(
                f'printf "%s" "{out}"\nexit {code}\n', encoding="utf-8")
            r = subprocess.run(["bash", "-e", "-c", step], cwd=tmp_path,
                               capture_output=True, text=True)
            outcomes[code] = (r.returncode, r.stdout)
        assert outcomes[0][0] == 0 and "SCANNED abc...HEAD" in outcomes[0][1]
        assert outcomes[3][0] == 0, "a scheduled run must not be a permanent red"
        assert outcomes[1][0] != 0, "an undeterminable range must not pass"
        assert outcomes[2][0] != 0, "an unexpected code must not pass either"


class TestTheDevToolingIsLockedLikeTheRuntime:
    def test_nothing_is_installed_by_a_bare_pip_install(self):
        """Every `pip install` line, wherever it sits. Anchoring on `run: pip
        install` missed the shape the coverage step already uses — a multi-line
        `run: |` block, where an unpinned install would have been invisible."""
        # Comments are stripped first: this file DESCRIBES the unpinned
        # `pip install pytest` it replaced, and a sweep that cannot tell a
        # command from a note about one goes red on its own history.
        commands = "\n".join(line for line in _ci().split("\n")
                              if not line.lstrip().startswith("#"))
        bare = [m.group(0).strip() for m in
                re.finditer(r"pip install (?!-r )\S.*", commands)]
        assert not bare, f"unpinned installs in CI: {bare}"

    def test_every_python_job_installs_from_a_lock(self):
        installs = re.findall(r"pip install -r (\S+)", _ci())
        assert installs, "no lock is installed anywhere"
        assert set(installs) <= {"requirements.txt", "requirements-test.txt",
                                 "requirements-dev.txt"}, set(installs)

    def test_the_suite_is_never_run_beside_the_auditors_dependencies(self):
        """The lock split, pinned. Resolving pytest beside pip-audit landed
        `charset-normalizer` 3.5.1 over the 3.4.9 `requirements.txt` pins, and
        `parse_utils` refused to parse — correctly: a parser version the lock
        does not name is one the suite never ran against. Install ORDER would
        have hidden it; two locks make it impossible."""
        for job in ("  test:", "  test-windows:"):
            body = _ci()[_ci().index(job):].split("\n\n  ")[0]
            assert "requirements-test.txt" in body, job
            assert "requirements-dev.txt" not in body, (
                f"{job} installs the auditor's tree beside the runtime tree")

    def test_no_package_is_pinned_at_two_versions_across_the_installed_locks(self):
        """The structural half of the same guard: the two locks a test job
        installs must not disagree about any package, whatever the order."""
        def pins(name):
            return dict(re.findall(r"^([A-Za-z0-9._-]+)==([^\s\\]+)",
                                   (REPO / name).read_text(encoding="utf-8"), re.M))
        runtime, test = pins("requirements.txt"), pins("requirements-test.txt")
        clashes = {k: (runtime[k], test[k]) for k in set(runtime) & set(test)
                   if runtime[k] != test[k]}
        assert not clashes, clashes

    def test_the_lock_is_hash_pinned(self):
        text = DEV_LOCK.read_text(encoding="utf-8")
        assert "--hash=sha256:" in text
        pinned = re.findall(r"^([A-Za-z0-9._-]+)==", text, re.M)
        assert len(pinned) > 5, pinned

    @pytest.mark.parametrize("tool,where", [("pytest", TEST_IN), ("coverage", TEST_IN),
                                            ("ruff", DEV_IN), ("mypy", DEV_IN),
                                            ("pip-audit", DEV_IN)])
    def test_the_input_names_every_tool_ci_runs(self, tool, where):
        assert re.search(rf"^{re.escape(tool)}\b", where.read_text(encoding="utf-8"), re.M)

    @pytest.mark.parametrize("tool", ["ruff", "mypy"])
    def test_the_linters_are_version_pinned_in_the_input_too(self, tool):
        """A linter is the one dependency whose new release adds FINDINGS. The
        lock pins it for a reproducible install; the `.in` pins it so that
        REGENERATING the lock does not quietly adopt a new rule set."""
        assert re.search(rf"^{tool}==\d", DEV_IN.read_text(encoding="utf-8"), re.M)

    def test_the_lock_says_which_command_regenerates_it(self):
        assert "uv pip compile requirements-dev.in" in DEV_LOCK.read_text(encoding="utf-8")

    @pytest.mark.parametrize("lock", ["requirements-dev.txt", "requirements-test.txt"])
    def test_every_lock_was_resolved_universally(self, lock):
        assert "--universal" in (REPO / lock).read_text(encoding="utf-8")

    def test_the_lock_the_windows_job_installs_really_covers_windows(self):
        """`--universal` in the regenerate comment says what was ASKED for; a
        platform MARKER in the resolution is what shows it was granted. Scoped
        to the test lock because that is the one the windows job installs — the
        dev lock resolves to the same set on every platform and so carries no
        marker to find, which is a fact about its dependencies rather than
        evidence about the flag."""
        text = TEST_LOCK.read_text(encoding="utf-8")
        assert "platform_system" in text or "sys_platform" in text, (
            "requirements-test.txt carries no platform marker, so it was "
            "resolved for one platform only and the windows job would install "
            "a tree that does not fit it")


class TestCoverageStaysInsideThisRepository:
    def test_no_third_party_coverage_service_is_wired_up(self):
        """AGENTS.md §1: this repository's subject is real returns. A coverage
        service is a new destination for repository content AND a new credential
        in repository secrets, and neither is spent on a convenience."""
        text = _ci().lower()
        for service in ("codecov", "coveralls", "codeclimate", "sonarcloud"):
            assert service not in text, f"{service} upload added to CI"

    def test_coverage_is_produced_and_kept_as_an_artifact(self):
        assert "coverage run -m pytest" in _ci()
        assert "actions/upload-artifact" in _ci()

    def test_coverage_is_scoped_to_the_python_this_repo_ships(self):
        """Unscoped, the report measures pytest and the standard library and the
        number stops meaning anything."""
        pyproject = (REPO / "pyproject.toml").read_text()
        assert "[tool.coverage.run]" in pyproject
        assert 'source = ["agents", "evals"]' in pyproject

    def test_the_subprocess_blind_spot_is_stated_where_the_number_is_configured(self):
        """The report measures the pytest process only, and this suite drives
        the engine through subprocesses constantly — so `preflight.py` and every
        CLI arm read as unexercised when they are the most exercised code here.
        A reader who does not know that draws the opposite conclusion from the
        lowest rows, which is worse than having no report.

        Pinned because the caveat is the only thing standing between the number
        and that misreading, and a caveat nothing checks is a caveat that gets
        tidied away."""
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "SUBPROCESS" in pyproject
        assert "COVERAGE_PROCESS_START" in pyproject, (
            "name the mechanism that is absent, so a reader can tell this is a "
            "known limit rather than an oversight")

    def test_no_coverage_threshold_is_enforced(self):
        """Deliberate. A threshold picked today becomes the target; the honest
        use of this report is reading which BRANCHES are unexercised."""
        config = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "fail_under" not in config.replace("# No `fail_under`", "")


class TestWindowsIsFinallyExercised:
    def test_there_is_a_windows_job(self):
        assert "runs-on: windows-latest" in _ci()

    @pytest.mark.parametrize("module", WINDOWS_MODULES)
    def test_it_runs_the_modules_whose_windows_branches_were_unexercised(self, module):
        job = _ci()[_ci().index("test-windows:"):].split("\n  lint:")[0]
        assert module in job
        # A job that collects without running, or selects nothing, looks
        # identical to a passing one from outside.
        assert "--collect-only" not in job
        assert re.search(r"\s-k\s", job) is None
        assert "continue-on-error" not in job, (
            "a job allowed to fail is a job whose red nobody sees")

    def test_secure_fs_really_does_carry_a_windows_branch_to_exercise(self):
        """Guards the premise. If `secure_fs` ever stops branching on the
        platform, this job is running a POSIX module on Windows for nothing and
        the reason it exists should be re-read rather than inherited."""
        source = (REPO / "agents" / "lib" / "secure_fs.py").read_text()
        assert 'os.name == "nt"' in source


class TestTheScheduleIsRealAndWeekly:
    def test_a_cron_is_declared(self):
        assert re.search(r"- cron: \"[\d*/, ]+\"", _ci())

    def test_it_runs_weekly_rather_than_nightly(self):
        """A daily run on a repository that changes a few times a week reports
        the same verdict every day until nobody reads it."""
        cron = re.search(r"- cron: \"([^\"]+)\"", _ci()).group(1)
        minute, hour, dom, month, dow = cron.split()
        assert dom == "*" and month == "*"
        assert dow != "*", "a weekday field of * is a DAILY run"
        assert minute != "0", (
            "GitHub's scheduler is busiest on the hour and delays a run that "
            "asks for it; the offset is deliberate")


class TestTheEvalsStayOutOfCI:
    """The owner ruled Q2: V3's model-driven runs happen LOCALLY, on credentials
    already on the operator's machine. W6's text predates that ruling and asks
    for them on a schedule — superseded, and recorded here so the next reader of
    the backlog does not implement it.

    `tests/test_eval_run.py` pins the general form (only `ci.yml` may have a
    trigger). This pins the specific one: no job in `ci.yml` runs the model
    harness either, which the general test cannot see.
    """

    def test_no_job_dispatches_the_model_driven_runner(self):
        assert "evals/run.py" not in _ci()
        assert "evals.run" not in _ci()

    def test_no_model_credential_is_read_by_any_job(self):
        assert "ANTHROPIC_API_KEY" not in _ci()
        assert "secrets." not in _ci(), (
            "a secret in this workflow is readable by every job it ever gains")


def test_the_range_script_is_executable_and_runs_under_sh():
    """It is invoked as `bash scripts/...` in CI, but a contributor will run it
    directly. A script that is not executable fails in a way that reads like a
    missing file."""
    assert RANGE_SCRIPT.stat().st_mode & 0o111
    result = subprocess.run(["sh", str(RANGE_SCRIPT)], cwd=REPO,
                            capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin", "EVENT_NAME": "schedule"})
    assert result.returncode == 3, result.stderr


def test_this_module_is_not_selected_by_the_hook():
    """The docstring says so; this makes it true rather than believed. If the
    hook ever gains an arm for this module, that arm moves the cold-pass row and
    the claim in the docstring has to go."""
    hook = (REPO / ".claude" / "hooks" / "skill-invariants.sh").read_text(encoding="utf-8")
    # The MODULE LIST, not the file: the hook's MEASURED COSTS prose names this
    # module when recording which counts moved, and a bare substring check went
    # red on that sentence. What matters is whether the hook would RUN it.
    assert "=tests/test_ci_workflow.py" not in hook


# --------------------------------------------------------------------------
# The finding the windows job produced on its first run.
# --------------------------------------------------------------------------

def _unencoded_text_io(path: Path) -> "list[int]":
    """Line numbers where `path` reads or writes TEXT without saying in what.

    `open`/`read_text`/`write_text` default to `locale.getencoding()`, which is
    UTF-8 on the hosts this was developed on and **cp1252 on Windows**. This
    repository's files are full of em-dashes and section signs, so on Windows
    every one of these raises `UnicodeDecodeError: 'charmap' codec can't decode
    byte 0x8f` — which is how the first run of the windows job failed, in bulk,
    on tests that had nothing to do with Windows.

    `os.open` and `pdfplumber.open` are different functions that share a name,
    and a binary mode has no encoding to state.
    """
    import ast
    hits = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in ("open", "read_text", "write_text"):
            continue
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id in ("os", "pdfplumber")):
            continue
        if "encoding" in {kw.arg for kw in node.keywords}:
            continue
        if name == "open":
            mode = None
            # `open(path, mode)` for the builtin; `path.open(mode)` for a Path.
            index = 0 if isinstance(func, ast.Attribute) else 1
            if len(node.args) > index and isinstance(node.args[index], ast.Constant):
                mode = node.args[index].value
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            if mode and "b" in str(mode):
                continue
        hits.append(node.lineno)
    return hits


class TestTextIOSaysWhichEncoding:
    """AGENTS.md claims Windows support in 27 places, and until the windows job
    ran nothing tested it. What it found first was not an ACL difference: it was
    that the shipped engine reads `result.json`, `answers.md` and the tax-law
    cache with the platform's default encoding. On Windows that is cp1252, and a
    return carrying a `§` or an em-dash — which every template here does —
    raises rather than parsing.

    The repository has paid for this family of bug once already, in shell: the
    PII gate pins `LC_ALL=C` because one Latin-1 byte under a UTF-8 locale hid
    every match on its line. This is the same mistake in the other direction.
    """

    @pytest.mark.parametrize("area", ["agents", "evals"])
    def test_no_shipped_module_leaves_the_encoding_to_the_platform(self, area):
        offenders = {}
        for source in sorted((REPO / area).rglob("*.py")):
            lines = _unencoded_text_io(source)
            if lines:
                offenders[str(source.relative_to(REPO))] = lines
        assert not offenders, (
            "these read or write text without naming an encoding, so they use "
            f"cp1252 on Windows and raise on this repo's own content: {offenders}")

    @pytest.mark.parametrize("module", ["tests/test_secure_fs.py",
                                        "tests/test_host_guard.py"])
    def test_the_modules_the_windows_job_runs_are_clean_too(self, module):
        """Scoped to the two the windows job actually runs. The rest of the
        suite has the same call sites and is NOT swept here: it never runs on
        Windows, and a sweep of 869 call sites for a platform nothing tests them
        on is churn dressed as rigour. If the windows job ever widens, this
        parametrize list widens with it."""
        assert not _unencoded_text_io(REPO / module)
