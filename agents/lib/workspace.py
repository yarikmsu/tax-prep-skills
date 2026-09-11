#!/usr/bin/env python3
"""Where the taxpayer's files live — one answer, read in one place.

WHY THIS EXISTS (Y4 of the 09-05 backlog). Every CLI in `agents/lib/` took a
`--workspace`, and every skill passed `.`, so the workspace WAS the working
directory — which is the repository checkout, because that is where
`sh agents/lib/run.sh` resolves from. A development clone and a live return
therefore shared a directory, and everything that kept them apart was policing:
the `.gitignore` list, `install.sh`'s `IGNORE_PATTERNS`, the pre-commit PII scan.
Three mechanisms, each a backstop for the same one-directory decision. The
08-12 review accepted that as permanent. This is the variable that retires it.

`TAX_WORKSPACE` names the directory holding `result.json`, `documents/` and the
rest of AGENTS.md §6. Set it, and the checkout holds code while the return lives
somewhere the git tooling never looks. Leave it unset and every command behaves
exactly as it did — `.`, the caller's directory — because a toolkit that refuses
to run until an environment variable is set is a migration nobody asked for.

WHAT DOES **NOT** MOVE, and it is worth saying because getting this backwards
breaks every command: the working directory stays the CHECKOUT. `run.sh` is
invoked by a relative path from the skills and anchors the venv to its own
location, and `agents/lib/__init__.py` refuses to import outside that venv. So
the agent runs FROM the clone and points AT the workspace; the variable moves
the data, never the code.

PRECEDENCE is explicit `--workspace`, then `$TAX_WORKSPACE`, then `.`. Explicit
wins so a one-off command against another directory — last year's archive, a
scratch copy — needs no unsetting and no export.
"""

from __future__ import annotations

import atexit
import datetime
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from agents.lib.rollover import RolloverError, _validate_subpath  # noqa: E402
from agents.lib.secure_fs import redact, secure_path  # noqa: E402

# The name, spelled once. Every message that tells a user how to move their
# return out of the checkout reads it from here.
ENV_VAR = "TAX_WORKSPACE"

# The last resort, and the whole of the backwards compatibility: unset variable,
# no flag, and a command means the directory it was run from — which is what
# every skill's `--workspace .` said before this module existed.
FALLBACK = "."


class WorkspaceError(Exception):
    """The workspace could not be resolved to a usable directory.

    Its OWN class, and not `FileNotFoundError`. Raising the builtin was the
    first attempt and looked free — it is an `OSError`, and half the CLIs here
    already catch one, so half the exception tuples needed no edit. That is
    exactly what is wrong with it: the other half do NOT catch `OSError`, and
    widening them to make this work would have made every unrelated IO fault in
    those commands exit 1 with a one-line message instead of the traceback that
    says where it happened. Every `_main` names this class, so what each arm
    handles stays something a reader can see.
    """


def from_environment() -> "str | None":
    """`$TAX_WORKSPACE`, or None when it is unset or empty.

    An EMPTY value reads as unset rather than as the current directory. A
    variable exported from a script whose own lookup failed — `TAX_WORKSPACE=$(
    some-command-that-printed-nothing)` — otherwise resolves to `""`, which
    `Path` turns into `.`: the checkout, silently, which is the one outcome this
    module exists to prevent.
    """
    value = os.environ.get(ENV_VAR)
    return value if value and value.strip() else None


def resolve_workspace(explicit: "str | None" = None) -> Path:
    """The workspace directory, by the precedence above. Refuses a bad one.

    The refusal is here and not in twenty CLIs: `Path(...)` is happy to name a
    directory that does not exist, and every caller then failed differently —
    one printed its own "no workspace at", most opened a file and reported the
    file as missing, which sends a reader to look for `result.json` when what is
    wrong is the directory they are pointed at.
    """
    # GIVEN-AND-EMPTY is not the same as NOT GIVEN. `--workspace ""` is a
    # caller whose own lookup returned nothing, and falling back to the current
    # directory there writes a return into whatever directory the command
    # happened to run in — the one outcome this module exists to prevent, and a
    # rule `tax_calendar` and `retirement_dashboard` had each hand-rolled.
    if explicit is not None and not str(explicit).strip():
        raise WorkspaceError(
            "--workspace was given as an empty path. Pass the directory "
            f"holding result.json, or omit the flag to use ${ENV_VAR}.")
    raw = explicit if explicit else from_environment()
    where = "--workspace" if explicit else f"${ENV_VAR}"
    if not raw:
        raw, where = FALLBACK, "the current directory"
    path = Path(raw)
    if not path.exists():
        raise WorkspaceError(
            f"no workspace at {redact(path)} ({where}). Create it, or point "
            f"{ENV_VAR} at the directory holding result.json.")
    if not path.is_dir():
        raise WorkspaceError(
            f"{redact(path)} ({where}) is not a directory. {ENV_VAR} names the "
            "DIRECTORY holding result.json, not a file inside it.")
    return path


def resolve_under_workspace(path_arg: str, workspace: Path) -> Path:
    """A path ARGUMENT, resolved against the workspace and confined to it.

    `--result result.json` means the result in the taxpayer's workspace, never a
    file in whatever directory the command ran from. Those were the same
    directory until `$TAX_WORKSPACE` existed; now the skills run from the
    CHECKOUT and point at the workspace, so a bare filename resolved against the
    CWD would name a file in the git clone — and `_validate_subpath` would then
    refuse it for being outside the workspace, which is a confusing way to say
    "you are in the wrong directory". An ABSOLUTE path is taken as given, which
    is what keeps a one-off command against a file elsewhere possible.

    It lived in `scenario.py` and three planning modules imported it from there,
    while six others resolved their `--result` and `--profile` against the CWD
    and had no idea this rule existed. That split was invisible while the two
    directories were the same one.
    """
    p = Path(path_arg)
    if not p.is_absolute():
        p = Path(workspace) / p
    try:
        return _validate_subpath(p.resolve(), Path(workspace))
    except RolloverError as e:
        # RE-RAISED as this module's own error, and the reason is a real trap:
        # `rollover.py` run as a SCRIPT is `__main__`, so the `RolloverError`
        # its CLI catches is a different class object from the
        # `agents.lib.rollover.RolloverError` raised here. A containment refusal
        # came back as a traceback instead of the one line it used to print.
        # Every caller already handles `WorkspaceError`; none of them can be
        # sure which identity of `RolloverError` it is holding.
        raise WorkspaceError(str(e)) from None


# The lock a RUN holds, and the whole of V10's concurrency half.
#
# `secure_fs.atomic_paths` made each PUBLISH atomic, which is a different
# question: two /tax-compute runs against one workspace still interleave, each
# publishing a consistent artifact built from the other's half-finished inputs.
# The receipt V8 binds to its inputs is the sharpest case — the second run's
# `result.json` moves the digest the first run's receipt already vouched for,
# so a return that WAS validated reads as edited-since, and the remedy a reader
# is given (re-run the gates) is the very thing that caused it.
#
# REFUSE, not queue. A second run is a person or an agent doing the same work
# twice, and the useful answer is "something else is already running, here is
# what and since when" — not a command that appears to hang. Waiting would also
# make an ABANDONED lock into a deadlock, and an abandoned lock is the common
# case: a killed terminal, a crashed session.
_LOCK_NAME = ".tax-workspace.lock"

# The keys the lock file holds. Deliberately not the taxpayer's anything: a
# lock is a coordination artifact and carries no return data.
_LOCK_FIELDS = ("pid", "started", "command")


# WHICH CLIs take it, declared here rather than discovered by reading twenty
# `_main`s — and pinned by test in BOTH directions, so neither a new writer
# that forgets the lock nor a reader that acquires one can land quietly.
#
# The line is SHARED PIPELINE STATE. These seven publish a file the rest of the
# return reads back: the tax-law cache, the profile and result a rollover
# seeds, the parse ledger, the derived 1040 lines, the validation receipt, and
# the archive move that relocates all of them at once. Two runs writing any of
# those overwrite each other.
#
# DERIVED artifacts are deliberately NOT here. `pdf_render`, `filing_package`
# and `tax_calendar` each publish a file nothing in the pipeline reads back, so
# a concurrent run cannot corrupt them — it can only make them STALE, and V8's
# receipt already detects exactly that by binding each gate's verdict to the
# digests of its inputs. Locking them would refuse a person printing last
# week's summary while a compute ran, which is a gate firing on an operation
# that cannot go wrong.
#
# The PLANNING skills are not here for the same reason from the other side:
# `scenario`, `projection`, `withholding`, `loss_harvest`, `roth_ladder`,
# `retirement_dashboard`, `ptet` and `yoy_diff` write into their own
# directories under AGENTS.md §6 and share nothing. Two of them in parallel is
# a person exploring, not a race.
LOCKED_CLIS = (
    "tax_law_fetcher",
    "rollover",
    "parse_records",
    "return_builder",
    "tie_out",
    "receipt",
    "archive",
)


class WorkspaceBusy(WorkspaceError):
    """Another run holds this workspace.

    A subclass, so every CLI's existing `except WorkspaceError` prints it as
    one line and exits 1 without a new arm — and a caller that wants to tell
    "busy" from "unusable" still can.
    """


def _process_is_alive(pid: int) -> bool:
    """Is `pid` a live process on THIS machine?

    `os.kill(pid, 0)` is the POSIX spelling and raises on Windows for a process
    the caller cannot signal, so it is wrapped: on any answer that is not a
    clear "no such process", the lock is treated as HELD. That is the safe
    direction — refusing a run that could have proceeded costs a message, while
    reclaiming a lock whose holder is alive is the corruption this exists to
    prevent.

    A pid that has been RECYCLED reads as alive and the lock is not reclaimed.
    That is the same safe direction, and the remedy is the same one the refusal
    prints: delete the file.
    """
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _read_lock(path: Path) -> dict:
    """The holder's record, or an empty dict when it cannot be read.

    A lock file that is empty, truncated or not JSON is one a run was killed
    part-way through writing. It is a HELD lock with an unknown holder, which
    the caller reports as exactly that rather than reclaiming: a file whose
    contents nothing can vouch for is not evidence its writer is gone.
    """
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


@contextmanager
def workspace_lock(workspace, command: str, *, now=None):
    """Hold `workspace` for the duration of one run, or REFUSE.

    Acquired by every CLI that PUBLISHES into the workspace; read-only commands
    take none, because a gate that blocked `/tax-verify` while a compute ran
    would fire on a pair of operations that cannot corrupt each other — and a
    gate that fires on correct data is the one that gets worked around.

    `command` is what the refusal names, so the person who sees it knows which
    run to go and look at. It is a CLI name, never an argument list: an
    argument list carries paths, and a path carries the taxpayer's directory.

    The file is created with `O_CREAT | O_EXCL`, which is one atomic step on
    every filesystem this runs on, and secured owner-only immediately. An
    EXISTING file is examined rather than removed: a live holder REFUSES, and a
    dead one is reclaimed with the reclaim SAID in the caller's output, because
    a lock that silently disappears teaches nobody that a run died.

    The release only removes a lock this call still owns. Two runs a second
    apart can both decide a stale lock is reclaimable; the loser would
    otherwise delete the winner's lock on its way out and leave the workspace
    unlocked while a run is live.
    """
    root = Path(workspace)
    path = root / _LOCK_NAME
    started = (now or datetime.datetime.now)().isoformat(timespec="seconds")
    record = {"pid": os.getpid(), "started": started, "command": command}
    payload = json.dumps(record, indent=2) + "\n"

    reclaimed = None
    for attempt in (1, 2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            held = _read_lock(path)
            pid = held.get("pid")
            # RE-ENTRANT for the process that already holds it. A process
            # cannot race itself, and refusing here would fire on correct
            # data — which is the one failure mode that teaches a caller to
            # work around a gate. It is how this first landed: the
            # return-builder tests call `_main` twice in one interpreter to
            # prove the second run rewrites nothing, and the lock refused the
            # second call for concurrency that did not exist.
            #
            # The inner hold does NOT re-write the file and does NOT remove it
            # on the way out: the record it would compare against is the OUTER
            # one, so the `finally` below leaves it alone and the outer release
            # is the only one that unlinks.
            if pid == os.getpid() and not isinstance(pid, bool):
                yield path
                return
            if not isinstance(pid, int) or isinstance(pid, bool) \
                    or _process_is_alive(pid) or attempt == 2:
                raise WorkspaceBusy(_busy_message(held)) from None
            # STALE: the holder is gone. Remove exactly the file we read and
            # try once more; a second FileExistsError means someone else won
            # the same race, and the branch above refuses rather than looping.
            reclaimed = held
            try:
                path.unlink()
            except OSError:
                raise WorkspaceBusy(_busy_message(held)) from None
            continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            secure_path(path)
        except BaseException:
            # A lock nothing can read is worse than no lock: it refuses every
            # later run with "unknown holder" until a person deletes it.
            path.unlink(missing_ok=True)
            raise
        break

    if reclaimed is not None:
        print(f"NOTE: reclaimed a stale workspace lock left by "
              f"{reclaimed.get('command', 'an unknown command')!r} "
              f"(pid {reclaimed.get('pid')}, started "
              f"{reclaimed.get('started', 'unknown')}) — that run did not "
              f"finish, so check what it left behind.", file=sys.stderr)
    try:
        yield path
    finally:
        # OURS, by the record we wrote and not by the path. See the docstring.
        if _read_lock(path) == record:
            path.unlink(missing_ok=True)


def hold_workspace(workspace, command: str) -> Path:
    """Take the run lock for the REST OF THIS PROCESS, and return its path.

    The same mechanism as `workspace_lock`, with the release registered on
    `atexit` instead of a `with` block. That is what a CLI's `_main` needs: its
    body is fifty lines of branches and early returns, and wrapping all of it
    to hold a lock that is process-scoped anyway buys nothing but a re-indent
    in seven files — and a re-indent is where a `return` quietly changes which
    branch it leaves.

    `atexit` does not run on SIGKILL. Neither does a `finally`, and neither can
    anything else; that case is what the STALE reclaim in `workspace_lock`
    exists for, and why the reclaim prints rather than passing silently.

    Called TWICE in one process, the second call refuses against the first —
    correctly, since the first is still held. Every CLI here runs as its own
    process; a test driving two `_main`s in one interpreter should use
    `workspace_lock` directly.
    """
    manager = workspace_lock(workspace, command)
    path = manager.__enter__()
    atexit.register(manager.__exit__, None, None, None)
    return path


def _busy_message(held: dict) -> str:
    """What a refused run is told.

    Names the lock FILE and not its path. The reader is standing in their own
    workspace and knows where it is; the absolute path adds nothing and carries
    the directory name, which for an archived return is the taxpayer's own.
    """
    who = held.get("command")
    pid = held.get("pid")
    started = held.get("started")
    if who is None and pid is None:
        return (f"the workspace lock `{_LOCK_NAME}` exists but cannot be read, "
                "so this run cannot tell whether another is live — a run was "
                "killed while writing it. Check for a running command, then "
                "delete that file from the workspace.")
    return (f"another run holds this workspace: {who!r} (pid {pid}, started "
            f"{started}). Two runs publishing into one workspace overwrite "
            "each other's artifacts and invalidate the validation receipt. "
            f"Wait for it, or — if it is not running — delete `{_LOCK_NAME}` "
            "from the workspace.")


def add_workspace_argument(parser, *aliases: str, **kwargs) -> None:
    """Add the standard `--workspace` to an argparse parser.

    Named for the caller and not for this module: every CLI here already has a
    local `workspace`, so a bare `workspace.add_argument` either shadows it or
    forces an alias in twenty files.

    One call site per CLI, so the flag's help text says the same thing in all of
    them and the environment variable is documented wherever the flag is. It is
    never `required`: the default arrives from `resolve_workspace`, and argparse
    complaining about a missing flag would hide a `$TAX_WORKSPACE` that IS set.
    """
    kwargs.setdefault(
        "help", f"the taxpayer workspace directory (default: ${ENV_VAR}, "
                "else the current directory)")
    # `aliases` are the OLDER spellings two CLIs shipped for the same
    # directory — `--current` in the year-over-year diff, `--to` in the
    # rollover. They stay accepted rather than renamed, because a documented
    # command that starts erroring is worse than a second spelling, and they
    # land on the same `workspace` dest so the code has one name for it.
    parser.add_argument("--workspace", *aliases, default=None, **kwargs)


def _main(argv: list) -> int:
    """Print the resolved workspace and where it came from.

    Run with no arguments this is the one command that answers "which directory
    am I about to write a return into" without running a gate to find out.
    """
    # `--path` prints the DIRECTORY and nothing else, for the one caller that
    # is a shell rather than a person: `/tax-setup` builds its `mkdir` targets
    # from it, and a second explanatory line there becomes part of a path.
    bare = "--path" in argv
    argv = [a for a in argv if a != "--path"]
    explicit = argv[0] if argv else None
    if explicit in ("-h", "--help"):
        print(f"usage: workspace.py [--path] [DIR]\n\n"
              f"Print the workspace DIR resolves to, and where that answer came "
              f"from. Precedence: DIR, then ${ENV_VAR}, then the current "
              f"directory. `--path` prints the directory alone, for a shell.")
        return 0
    try:
        path = resolve_workspace(explicit)
    except WorkspaceError as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1
    if bare:
        # NOT redacted, and this is the one place that is right: a shell is
        # about to `mkdir` this, and `[REDACTED]/documents` is not a directory.
        # Everything else this module prints goes to a person.
        print(path.resolve())
        return 0
    source = ("the argument" if explicit else
              f"${ENV_VAR}" if from_environment() else
              f"the current directory (${ENV_VAR} is unset)")
    print(f"{redact(path.resolve())}\n  from {source}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(_main(sys.argv[1:]))
