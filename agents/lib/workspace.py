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

import os
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from agents.lib.rollover import RolloverError, _validate_subpath  # noqa: E402
from agents.lib.secure_fs import redact  # noqa: E402

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
