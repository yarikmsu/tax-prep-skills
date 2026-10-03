"""Run a selected invariant pass; the shell owns only the cheap change boundary.

One CHECKS mapping supplies selection names, persisted tokens, and pytest paths.
A verdict (pytest 0/1) advances to the pass-start marker; no verdict preserves
both boundary and red memory. Red checks join only a nonempty new selection.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# Structural roster, also read literally by the selector. HOOKSDEPS has its own
# token because the subset is its own verdict, not a verdict for the whole suite.
CHECKS = {
    "PORTABILITY": ("portability", "tests/test_portability.py"),
    "CAPABILITIES": ("capabilities", "tests/test_capabilities.py"),
    "AGENTS": ("agents", "tests/test_claude_agents.py"),
    "HOOKS": ("hooks", "tests/test_claude_hooks.py"),
    "HOOKSDEPS": ("hooksdeps", None),
    "PREFLIGHT": ("preflight", "tests/test_preflight.py"),
    "SECUREFS": ("securefs", "tests/test_secure_fs.py"),
    "HOSTGUARD": ("hostguard", "tests/test_host_guard.py"),
}
HOOKS_DEPENDENTS = (
    "tests/test_claude_hooks.py::TestSkillInvariantsHook::test_the_watched_set_still_covers_what_the_modules_read",
    "tests/test_claude_hooks.py::TestSkillInvariantsHook::test_every_watched_source_path_reaches_its_module",
    "tests/test_claude_hooks.py::TestHookRegistration::test_only_the_registry_pair_is_deselected_and_both_ids_resolve",
    "tests/test_claude_hooks.py::TestSkillInvariantsHook::test_every_watched_module_that_reads_a_shared_helper_is_in_its_arm",
)
DESELECT = (
    "tests/test_portability.py::TestSkillLayout::test_all_expected_skills_present",
    "tests/test_portability.py::TestClaudeSymlinks::test_one_symlink_per_skill",
)
STAMP = Path(".claude/.skill-invariants-stamp")
_cancelled = False


class Cancelled(Exception):
    """A signal is not a test verdict."""


def _signal(signum, frame):
    global _cancelled
    _cancelled = True


def _command(args, *, check_cancel=True, **kwargs):
    # Like Bash, defer a trapped signal until the foreground command returns.
    # In particular, cleanup must not race a touch that could recreate a candidate.
    result = subprocess.run(args, **kwargs)
    if check_cancel and _cancelled:
        raise Cancelled
    return result


def select_checks(paths: bytes) -> set[str]:
    """An unavailable, truncated, or failing selector selects every check."""
    try:
        result = _command(
            [".venv/bin/python", str(Path(__file__).with_name("hook_selection.py"))],
            input=paths, capture_output=True)
        lines = result.stdout.replace(b"\0", b"").rstrip(b"\n").split(b"\n")
        if result.returncode or lines[0] != b"SELECTION1":
            return set(CHECKS)
        names = {line.decode("ascii") for line in lines[1:]}
        if not names <= CHECKS.keys():
            return set(CHECKS)
        return names
    except (OSError, UnicodeError):
        return set(CHECKS)


def read_red_checks() -> set[str]:
    """Match Bash command substitution: discard NUL, retain CR, split only LF."""
    try:
        lines = STAMP.read_bytes().replace(b"\0", b"").split(b"\n")[1:]
    except OSError:
        return set()
    return {name for name, (token, _) in CHECKS.items() if token.encode() in lines}


def pytest_paths(selected: set[str]) -> list[str]:
    paths = []
    for name, (_, path) in CHECKS.items():
        if name not in selected:
            continue
        if path is not None:
            paths.append(path)
        elif "HOOKS" not in selected:
            paths.extend(HOOKS_DEPENDENTS)
    return paths


def advance_stamp(version: str, selected: set[str], red: bool, marker: Path):
    """Revalidate immediately before writing, then replace on the same filesystem.

    The shell checks trust before a potentially long pytest pass; .claude or the
    stamp can become a symlink during it. Refusal costs only a repeated pass.
    As with the old shell, these checks narrow the race rather than claiming an
    openat/O_NOFOLLOW guarantee. A candidate is tracked until mv consumes it.
    """
    directory = STAMP.parent
    if not directory.is_dir() or directory.is_symlink() or STAMP.is_symlink():
        return
    candidate = None
    try:
        result = _command(["mktemp", str(STAMP) + ".XXXXXX"],
                          capture_output=True, check_cancel=False)
        if result.returncode:
            return
        candidate = Path(os.fsdecode(result.stdout.rstrip(b"\n")))
        if _cancelled:
            raise Cancelled
        body = version + "\n"
        if red:
            body += "".join(token + "\n" for name, (token, _) in CHECKS.items()
                            if name in selected)
        candidate.write_bytes(body.encode())
        if _command(["touch", "-r", str(marker), str(candidate)],
                    capture_output=True).returncode:
            return
        if _command(["mv", "-f", str(candidate), str(STAMP)],
                    capture_output=True).returncode == 0:
            candidate = None
    except OSError:
        pass  # Only repeat cost: a failed stamp write must not spoil a green pass.
    finally:
        if candidate is not None:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass


def budget_warning(seconds: int) -> str:
    try:
        settings = json.loads(Path(".claude/settings.json").read_bytes())
        limits = [hook.get("timeout")
                  for entry in settings.get("hooks", {}).get("PostToolUse", [])
                  for hook in entry.get("hooks", [])
                  if "skill-invariants.sh" in str(hook.get("command", ""))
                  and type(hook.get("timeout")) in (int, float)]
        timeout = min(limits)
        if isinstance(timeout, float) and math.isfinite(timeout) and timeout.is_integer():
            timeout = int(timeout)
        # jq prints a whole-valued JSON number as an integer, even for 1.0.
        # Nonfinite, fractional and nonpositive settings produce no warning.
        if str(timeout).isdigit() and timeout > 0 and seconds * 4 >= timeout * 3:
            return (f"Timing: the skill invariants' pytest pass took {seconds}s of\n"
                    f"the {timeout}s timeout settings.json registers for this hook — three\n"
                    "quarters or more. Past the timeout the hook is CANCELLED and a\n"
                    "cancelled check reads as a pass. Re-time a cold pass and see the\n"
                    "MEASURED COSTS block in .claude/hooks/skill-invariants.sh (AA9).")
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return ""


def run_pass(tool, force_full, writable, temp, version, display, relative, paths) -> int:
    if tool == "Bash":
        selected = set(CHECKS) if force_full else select_checks(paths)
    else:
        selected = select_checks(os.fsencode(relative) + b"\0")
        if not selected:
            return 0  # An unrelated edit does not consume other pending changes.
        if force_full:
            selected = set(CHECKS)
        elif paths:
            selected |= select_checks(paths)
    if not selected:
        return 0
    if not force_full:
        selected |= read_red_checks()
    args = [".venv/bin/python", "-m", "pytest", *pytest_paths(selected)]
    for node in DESELECT:
        args.extend(("--deselect", node))
    started = time.monotonic()
    try:
        result = _command([*args, "-q"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        status, output = result.returncode, result.stdout
    except OSError:
        status, output = 4, b"The workspace interpreter could not run pytest.\n"
    seconds = int(time.monotonic() - started)
    if status in (0, 1) and writable:
        advance_stamp(version, selected, status == 1, Path(temp) / "now")
    warning = budget_warning(seconds)
    if status == 0:
        if warning:
            print(warning, file=sys.stderr)
            return 1
        return 0
    if status == 1:
        headline = f"Skill invariants FAILED after {display}"
    else:
        headline = (f"Skill invariant checks could not RUN after {display}"
                    f" (pytest exit {status}) — the checks themselves are"
                    " broken, not necessarily the edit")
    print(headline, file=sys.stderr)
    # Preserve bytes/newlines (including CR) rather than normalizing pytest text.
    sys.stderr.flush()
    sys.stderr.buffer.write(b"\n".join(output.rstrip(b"\n").split(b"\n")[-30:]) + b"\n")
    if warning:
        print(warning, file=sys.stderr)
    return 2


def main() -> int:
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, _signal)
    temp = ""
    try:
        header = sys.stdin.buffer.read().split(b"\0", 7)
        if len(header) != 8:
            return 2
        tool, force, writable, temp, version, display, relative = map(os.fsdecode, header[:7])
        return run_pass(tool, force == "1", writable == "1", temp, version,
                        display, relative, header[7])
    except Cancelled:
        return 1
    finally:
        if temp:
            shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
