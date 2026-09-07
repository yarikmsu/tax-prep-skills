#!/usr/bin/env python3
"""Archive current-year tax files into archive/{year}/ before /tax-setup wipes root.

Atomic: any mid-move failure rolls back. Sets chmod 600 on all moved files and
chmod 700 on every directory it creates or relocates (AGENTS.md §1).
"""

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.secure_fs import (  # noqa: E402
    is_reparse_point, redact, secure_path, year_or_shape,
)

# EVERY path this module names in an error is a RUNTIME path, and the CLI below
# prints them. An entry under documents/ — or under archive/{year}/documents/
# after the move — is a taxpayer's scanned document, and its NAME alone is the
# disclosure AGENTS.md §1 restricts those directories to prevent. The name is
# untrusted input besides: it is whatever the taxpayer saved the file as, up to
# and including an SSN, which §1 forbids emitting under any circumstance. So
# every message here goes through secure_fs.redact(), the exceptions raised
# underneath included — os.lstat's and shutil.move's own text carries the raw
# path, so redacting only the interpolated Path would leave it disclosed by the
# half of the message that came from the OS.


class ArchiveError(Exception):
    """Raised when /tax-archive cannot safely proceed."""


@dataclass
class ArchiveReport:
    year: int
    moved: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dry_run: bool = False


# Files that the toolkit creates per year and may want archived.
# parse_ledger.json belongs to the year (the tie-out record — /tax-amend's
# "what changed" baseline); reviews/ holds the persisted /tax-review
# workpapers that /tax-rollover surfaces next year.
EXPECTED_TOP_LEVEL_FILES = ("result.json", "taxpayer_profile.json", "answers.md",
                            "parse_ledger.json")
EXPECTED_TOP_LEVEL_DIRS = ("summaries", "documents", "reviews")


def _is_reparse_point_or_raise(path: Path) -> bool:
    """`is_reparse_point()` fails CLOSED: every OSError but FileNotFoundError
    propagates rather than certifying an uninspected path as a real
    file/directory. Translate that into this module's own error type here, in
    the ONE place every check below routes through — two of those checks (the
    archive/ and archive/{year} destination components) and the whole dry-run
    loop sit OUTSIDE archive_year's try block, so an untranslated OSError
    would escape as a bare traceback past the `except ArchiveError` in
    __main__ and in every caller.
    """
    try:
        return is_reparse_point(path)
    except OSError as e:
        raise ArchiveError(
            f"Could not determine whether {redact(path)} is a symlink or "
            f"junction: {redact(e)} — refusing to proceed rather than treating "
            f"a path that could not be inspected as a real "
            f"file/directory.") from e


def _refuse_symlink(src: Path) -> None:
    """A symlinked (or, on Windows, junctioned) runtime path is refused
    before it can be moved.

    shutil.move() relocates the link/junction itself, still pointing at its
    original target; the secure_path() calls that follow then resolve
    through it and change permissions on whatever that target is, workspace
    or not. is_reparse_point() covers both, unlike a bare is_symlink()
    check — see agents/lib/secure_fs.py's module docstring for why an NTFS
    junction needs its own check on Windows.
    """
    if _is_reparse_point_or_raise(src):
        raise ArchiveError(
            f"{redact(src.name)} is a symlink or junction, not a real "
            f"file/directory — refusing to archive it. Moving it would still "
            f"leave it resolving to its original target, and the permission "
            f"hardening that follows would change that target's permissions "
            f"instead of the workspace's.")


def _refuse_symlink_destination(path: Path) -> None:
    """archive/ and archive/{year} are destination path COMPONENTS, not
    entries being archived — a different failure mode from `_refuse_symlink`
    above. mkdir(..., exist_ok=True) accepts a symlinked (or junctioned)
    directory silently (it checks is_dir(), which resolves through either),
    and the secure_path() calls that follow would resolve through it too,
    changing permissions on whatever it points to. The moves that come
    after would then land the taxpayer's files there instead of in the
    workspace.
    """
    if _is_reparse_point_or_raise(path):
        raise ArchiveError(
            f"{redact(path)} is a symlink or junction, not a real directory — "
            f"refusing to use it as an archive destination. "
            f"mkdir/secure_path would resolve through it and act on "
            f"whatever it points to instead of the workspace.")


def _raise_walk_error(err: OSError) -> None:
    """os.walk() silently swallows a scandir() failure (permission denied, a
    transient NFS/I/O error on a nested directory) unless given `onerror` —
    the walk just yields fewer entries, so archive_year() would report
    success with part of the moved subtree unsecured and unvalidated for
    symlinks. Re-raising here routes the failure into archive_year()'s own
    except block, which already rolls back and raises ArchiveError.
    """
    raise err


def _refuse_and_secure_tree(top: Path) -> None:
    """Refuse every nested link, and restrict every entry BENEATH `top`.

    `top` itself is the caller's to secure — the two callers reach it by
    different routes (one has just moved it, one found it already in place) and
    both secure it before descending, so doing it here would be a second chmod
    on the same path rather than a guarantee.

    followlinks=False keeps os.walk from ever descending into a symlinked
    subdirectory's contents, but a chmod on the symlink ENTRY itself still
    resolves through it. A nested symlink is therefore refused the same way a
    top-level one is, rather than silently skipped — skipping would leave that
    one entry unsecured with nothing to show for it.
    """
    for dirpath, dirnames, filenames in os.walk(
        top, followlinks=False, onerror=_raise_walk_error
    ):
        current = Path(dirpath)
        # Every entry is refused BEFORE any of them is secured: a chmod
        # resolves through a link, so securing entry-by-entry as the walk found
        # them would act on an external target before the refusal for it came
        # up.
        for n in dirnames + filenames:
            _refuse_symlink(current / n)
        for n in dirnames + filenames:
            secure_path(current / n)


def _preview_refuse_tree(top: Path) -> None:
    """The dry run's half of the walk above: refuse, secure nothing.

    A nested reparse point is refused by the real run and rolls the whole
    archive back, so a preview that inspected only the top-level entries would
    report "will move documents/" for a tree the real run refuses — the user
    confirms a preview that then fails partway through, after five files have
    already moved and been rolled back. The preview exists to surface exactly
    this refusal, so it walks the same subtrees, with the same onerror so an
    unreadable directory is not silently skipped into a clean preview.

    The OSError translation is what the real run gets for free from its own
    except block: this runs OUTSIDE that try, so _raise_walk_error's re-raise
    would otherwise escape as a bare traceback.
    """
    try:
        for dirpath, dirnames, filenames in os.walk(
            top, followlinks=False, onerror=_raise_walk_error
        ):
            current = Path(dirpath)
            for n in dirnames + filenames:
                _refuse_symlink(current / n)
    except OSError as e:
        raise ArchiveError(
            f"Could not inspect {redact(top)} for nested symlinks "
            f"or junctions: {redact(e)} — refusing to report a "
            f"preview that skipped part of the tree.") from e


def _retained_entries(archive_dir: Path) -> list[Path]:
    """What is already sitting in archive/{year} — the tolerated /tax-amend
    `as_filed/` snapshot, and nothing else (the guard in archive_year refuses
    any other child before this is called).

    Sorted so the order a failure is reported in is the same on every
    filesystem. Missing directory reads as empty: this runs on the dry-run path
    too, where archive/{year} need not exist yet.
    """
    if not archive_dir.exists():
        return []
    return sorted(archive_dir.iterdir())


def _validate_year(year) -> int:
    if not isinstance(year, int):
        # Echoing back what was passed is the point of the message, and what was
        # passed is untrusted: a mistyped argument can be any string at all.
        raise ArchiveError(
            f"Year must be a 4-digit integer, got {redact(repr(year))}")
    if not (2000 <= year <= 2099):
        raise ArchiveError(f"Year {year} out of range (2000-2099)")
    return year


def archive_year(year, root: Path, dry_run: bool = False) -> ArchiveReport:
    """Move TY{year} files from root into root/archive/{year}/.

    Refuses if archive/{year}/ already exists with anything beyond a
    /tax-amend `as_filed/` snapshot (which it merges alongside). Refuses if
    root/result.json has meta.tax_year != year. Refuses if root/result.json
    missing.
    """
    year = _validate_year(year)
    root = Path(root).resolve()
    archive_dir = root / "archive" / str(year)

    # Both destination path components — root/archive and root/archive/{year}
    # — are checked before anything below even looks at them: the exists()
    # check just after this reads through a symlink same as mkdir/chmod
    # would, and a symlinked archive_dir with an empty external target would
    # pass that check's "no real contents" test and fall through to mkdir.
    for d in (archive_dir.parent, archive_dir):
        _refuse_symlink_destination(d)

    # A pre-existing archive/{year}/ normally means the year is already
    # archived — refuse. The ONE exception is a /tax-amend snapshot: it
    # writes archive/{year}/as_filed/ during filing season, and the
    # year-end archive must merge the final (amended) files in ALONGSIDE
    # that snapshot rather than refuse. Tolerate only when as_filed/ is the
    # sole child (nothing that would collide with the moved year files).
    if archive_dir.exists():
        children = {p.name for p in archive_dir.iterdir()}
        if children - {"as_filed"}:
            raise ArchiveError(
                f"{redact(archive_dir)} already exists and contains more than "
                f"the /tax-amend snapshot ({redact(sorted(children))}). Move or "
                f"delete it first.")

    result_path = root / "result.json"
    if not result_path.exists():
        raise ArchiveError("No result.json at root — nothing to archive.")

    try:
        meta_year = json.loads(result_path.read_text()).get("meta", {}).get("tax_year")
    except json.JSONDecodeError as e:
        raise ArchiveError(f"result.json is malformed: {redact(e)}")
    if meta_year != year:
        # Not `TY{meta_year}`: nothing type-checks `meta.tax_year` on the way
        # in, so this prints whatever the file holds there — the same leak the
        # projection and calendar year guards already route through
        # `year_or_shape`.
        raise ArchiveError(
            f"Root result.json is for {year_or_shape(meta_year)}, not "
            f"TY{year}. Pass the correct year."
        )

    report = ArchiveReport(year=year, dry_run=dry_run)
    if dry_run:
        for name in EXPECTED_TOP_LEVEL_FILES + (f"tax_law_{year}.json",) + EXPECTED_TOP_LEVEL_DIRS:
            src = root / name
            # Same ordering as the real-run loops below: exists() follows a
            # symlink or junction and reads False for a dangling one, so
            # checking it first would report a refused entry as merely
            # "skipped" instead of surfacing the refusal a real run would
            # raise.
            _refuse_symlink(src)
            # _refuse_symlink above already proved src is not itself a reparse
            # point, so is_dir() here cannot be resolving through one.
            if src.is_dir():
                _preview_refuse_tree(src)
            (report.moved if src.exists() else report.skipped).append(name)
        # The RETAINED /tax-amend snapshot is previewed too. It is the one
        # child this function tolerates rather than moves, and the real run
        # now refuses a link inside it and secures what it finds — so a
        # preview that skipped it would report a clean archive for a tree the
        # real run stops on, which is the gap the entry walk above exists to
        # close, one directory over.
        for retained in _retained_entries(archive_dir):
            _refuse_symlink(retained)
            if retained.is_dir():
                _preview_refuse_tree(retained)
        return report

    archive_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    moved_paths: list[tuple[Path, Path]] = []
    try:
        # 600 on the moved files still leaves a 755 directory listing their NAMES,
        # and under archive/ a name is the disclosure on its own. Both levels need
        # the chmod: mkdir applies `mode` to the leaf only (parents get the
        # default), and ignores it entirely for a directory that already exists.
        # Inside the try so a failure fails the way every other one here does —
        # ArchiveError, empty archive_dir removed, nothing moved. Documents are
        # not moved into a directory whose permissions could not be set.
        for d in (archive_dir.parent, archive_dir):
            secure_path(d)
        # The RETAINED entry — the tolerated /tax-amend as_filed/ snapshot —
        # gets the same treatment, and nothing else reaches it. The per-move
        # walk below covers only what THIS run moved, and the two secure_path
        # calls above stop at archive/{year} itself, so a snapshot already in
        # place kept whatever modes it was written with: 0755 directories
        # listing a taxpayer's as-filed document NAMES, and on Windows a
        # snapshot written before the ACL hardening whose "chmod" only ever
        # flipped the read-only DOS attribute. archive_year() then reported
        # success over it. Nothing has been moved in yet at this point, and the
        # guard above already refused any child but as_filed/, so what this
        # iterates is exactly the retained snapshot.
        for retained in _retained_entries(archive_dir):
            _refuse_symlink(retained)
            secure_path(retained)
            if retained.is_dir():
                _refuse_and_secure_tree(retained)
        # Track moved paths BEFORE chmod so a chmod failure still rolls the file back
        for name in EXPECTED_TOP_LEVEL_FILES + (f"tax_law_{year}.json",):
            src = root / name
            # is_reparse_point() is checked before exists(), not after:
            # exists() follows the link/junction and reports False for a
            # DANGLING one (e.g. documents/ pointing at a temporarily
            # unmounted volume), which would otherwise skip straight past
            # _refuse_symlink below, report the entry as merely "not
            # present", and leave the prior-year link sitting at the
            # workspace root — reconnecting the target later would then feed
            # those old files into the next year's workflow instead of this
            # one's archive.
            _refuse_symlink(src)
            if src.exists():
                dst = archive_dir / name
                shutil.move(str(src), str(dst))
                moved_paths.append((src, dst))
                report.moved.append(name)
                secure_path(dst)
            else:
                report.skipped.append(name)
        for name in EXPECTED_TOP_LEVEL_DIRS:
            src = root / name
            _refuse_symlink(src)
            if src.exists():
                dst = archive_dir / name
                shutil.move(str(src), str(dst))
                moved_paths.append((src, dst))
                report.moved.append(name)
                # A moved subtree arrives carrying whatever mode it had at the
                # root — 755 under a normal umask. `archive/{year}/documents/`
                # names a taxpayer's scanned documents, so the directories are
                # restricted alongside the files. Tightening to 700 keeps owner
                # traversal, so the walk in progress is unaffected.
                secure_path(dst)
                # A nested symlink raises, is caught below, and rolls the whole
                # archive back — see _refuse_and_secure_tree for why skipping
                # it instead would be its own silent gap.
                _refuse_and_secure_tree(dst)
            else:
                report.skipped.append(name)
    except Exception as e:
        # Roll back any partial moves
        for src, dst in reversed(moved_paths):
            if dst.exists():
                shutil.move(str(dst), str(src))
        if archive_dir.exists() and not any(archive_dir.iterdir()):
            archive_dir.rmdir()
        # shutil.move names BOTH endpoints in its own error text, and every one
        # of them is a document path — the raw half of the message, not the
        # interpolated one, is what discloses here.
        raise ArchiveError(f"Move failed, rolled back: {redact(e)}") from e

    return report


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 -m agents.lib.archive {year}", file=sys.stderr)
        sys.exit(2)
    try:
        year = int(sys.argv[1])
    except ValueError:
        print(f"Year must be a 4-digit integer, got {redact(repr(sys.argv[1]))}",
              file=sys.stderr)
        sys.exit(2)
    try:
        report = archive_year(year, root=Path.cwd())
    except ArchiveError as e:
        # Redacted again at the print site even though every message above
        # is already redacted: this is the LAST boundary before a taxpayer
        # path reaches a terminal, and a future message that forgets should
        # not be able to reach it through here.
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        sys.exit(1)
    print(f"Archived {len(report.moved)} items to archive/{year}/")
    for name in report.moved:
        print(f"  v {redact(name)}")
    for name in report.skipped:
        print(f"  - {redact(name)} (not present)")
