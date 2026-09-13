"""How `agents/lib/` is enumerated — once, for every sweep that does it.

Sweeps across the test suite walk the engine, and until W2 made it a directory
of packages every one of them could get away with its own spelling: the tree
was flat, so `glob` and `rglob` agreed, `p.stem` named a module and `p.name`
named a file. None of that survived the split, and each sweep found out
separately.

The failures were not loud. `secure_fs`'s roster excluded `__init__` by STEM —
one file while the tree was flat, one per PACKAGE after — and dropped the whole
federal engine out of the unredacted-path gate with its roster count going UP.
The host guard exempted `__init__.py` by NAME for the same reason and quietly
exempted four. A capital-gains sweep keyed by `p.name` reported
`projection/__init__.py` as `__init__.py`, which names nothing a reader can
open. Three sweeps, one question, three wrong answers.

So the question is answered here, and a sweep that needs it calls this rather
than re-deriving it. Three things it settles that a hand-rolled walk gets
wrong:

- `__pycache__` is EXCLUDED. Compiled bytecode is not source, and every engine
  run rewrites it.
- A path is spelled `.as_posix()`, never `str()`. `str(Path)` uses the OS
  separator, so a comparison against a literal written with a forward slash
  fails on Windows — a red test on a correct tree, on the platform whose first
  CI run found two shipped defects.
- A file's MODULE NAME is the package directory for an `__init__.py` and the
  stem otherwise. `projection/__init__.py` is `projection`; it is not
  `__init__`, and `__init__` is not a name any roster, CLI list or exemption
  set means.
"""

from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "agents" / "lib"

# The one file the host-interpreter guard lives in. Spelled as a PATH because
# that is what it is: `agents/lib/__init__.py` and nothing else, where the
# bare name `__init__.py` now matches five files.
GUARD = LIB / "__init__.py"


def engine_files(root: "Path | None" = None) -> "list[Path]":
    """Every Python source file under the engine, sorted, `__pycache__` out.

    `root` narrows to one subtree — pass `LIB / "projection"` for a sweep whose
    question is about one package. It defaults to the whole engine, because a
    sweep that means "the engine" and writes a narrower path is the narrowing
    this module exists to stop.
    """
    return sorted(p for p in (root or LIB).rglob("*.py")
                  if "__pycache__" not in p.parts)


def engine_relpath(path: Path) -> str:
    """`projection/render.py` — relative to the engine root, POSIX-spelled.

    What a reader needs in order to open the offender, and what a dict may be
    keyed by without two packages' initialisers colliding on `__init__.py`.
    """
    return path.relative_to(LIB).as_posix()


def engine_module_name(path: Path) -> str:
    """The name this file is ADDRESSED by — a roster entry, a CLI, an import.

    `agents/lib/projection/__init__.py` is `projection`. `agents/lib/tie_out.py`
    is `tie_out`. A file deeper inside a package has no single-segment name and
    gets its dotted path (`tax_computations.state.ny`), because that is the one
    spelling that is both unique and importable — `ny` is not, and a second
    state with a same-named module would silently replace the first.
    """
    if path == GUARD:
        # `agents/lib/__init__.py` is the package `agents.lib` ITSELF, not a
        # module under it, and it has no name in any roster. Raising rather
        # than returning `.` or `""`: every caller here means to exclude it,
        # and one that forgot should find out from a message naming the file
        # instead of from a dotted path that matches nothing.
        raise ValueError(
            "agents/lib/__init__.py is the engine package, not a module in it "
            "— exclude it by PATH (engine_tree.GUARD) before asking for a name")
    if path.name == "__init__.py":
        return path.parent.relative_to(LIB).as_posix().replace("/", ".")
    rel = path.relative_to(LIB).with_suffix("")
    return rel.as_posix().replace("/", ".")


def engine_packages() -> "set[str]":
    """Directory names under the engine that are REGULAR packages.

    `(d / "__init__.py").is_file()` and not merely `d.is_dir()`: a namespace
    package resolves from whatever happens to be on `sys.path`, and `agents/lib`
    holds none deliberately.
    """
    return {d.name for d in LIB.iterdir()
            if d.is_dir() and (d / "__init__.py").is_file()}


def engine_top_level_names() -> "set[str]":
    """Every name importable as `agents.lib.<name>`, module or package.

    The roster's own vocabulary: a flat `glob("*.py")` produces this set and a
    package silently is not in it, which is how the engine's largest module
    came to be missing from a list that read as complete.
    """
    names = {p.stem for p in LIB.glob("*.py") if p.stem != "__init__"}
    return names | engine_packages()
