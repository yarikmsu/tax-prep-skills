#!/usr/bin/env python3
"""Archive current-year tax files into archive/{year}/ before /tax-setup wipes root.

Atomic: any mid-move failure rolls back. Sets chmod 600 on all moved files.
"""

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union


class ArchiveError(Exception):
    """Raised when /tax-archive cannot safely proceed."""


@dataclass
class ArchiveReport:
    year: int
    moved: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dry_run: bool = False


# Files that the toolkit creates per year and may want archived
EXPECTED_TOP_LEVEL_FILES = ("result.json", "taxpayer_profile.json", "answers.md")
EXPECTED_TOP_LEVEL_DIRS = ("summaries", "documents")


def _validate_year(year) -> int:
    if not isinstance(year, int):
        raise ArchiveError(f"Year must be a 4-digit integer, got {year!r}")
    if not (2000 <= year <= 2099):
        raise ArchiveError(f"Year {year} out of range (2000-2099)")
    return year


def archive_year(year, root: Path, dry_run: bool = False) -> ArchiveReport:
    """Move TY{year} files from root into root/archive/{year}/.

    Refuses if archive/{year}/ already exists. Refuses if root/result.json
    has meta.tax_year != year. Refuses if root/result.json missing.
    """
    year = _validate_year(year)
    root = Path(root).resolve()
    archive_dir = root / "archive" / str(year)

    if archive_dir.exists():
        raise ArchiveError(f"{archive_dir} already exists. Move or delete it first.")

    result_path = root / "result.json"
    if not result_path.exists():
        raise ArchiveError("No result.json at root — nothing to archive.")

    try:
        meta_year = json.loads(result_path.read_text()).get("meta", {}).get("tax_year")
    except json.JSONDecodeError as e:
        raise ArchiveError(f"result.json is malformed: {e}")
    if meta_year != year:
        raise ArchiveError(
            f"Root result.json is for TY{meta_year}, not TY{year}. Pass the correct year."
        )

    report = ArchiveReport(year=year, dry_run=dry_run)
    if dry_run:
        for name in EXPECTED_TOP_LEVEL_FILES + (f"tax_law_{year}.json",) + EXPECTED_TOP_LEVEL_DIRS:
            (report.moved if (root / name).exists() else report.skipped).append(name)
        return report

    archive_dir.mkdir(parents=True)
    moved_paths: list[tuple[Path, Path]] = []
    try:
        # Track moved paths BEFORE chmod so a chmod failure still rolls the file back
        for name in EXPECTED_TOP_LEVEL_FILES + (f"tax_law_{year}.json",):
            src = root / name
            if src.exists():
                dst = archive_dir / name
                shutil.move(str(src), str(dst))
                moved_paths.append((src, dst))
                report.moved.append(name)
                os.chmod(dst, 0o600)
            else:
                report.skipped.append(name)
        for name in EXPECTED_TOP_LEVEL_DIRS:
            src = root / name
            if src.exists():
                dst = archive_dir / name
                shutil.move(str(src), str(dst))
                moved_paths.append((src, dst))
                report.moved.append(name)
                for p in dst.rglob("*"):
                    if p.is_file():
                        os.chmod(p, 0o600)
            else:
                report.skipped.append(name)
    except Exception as e:
        # Roll back any partial moves
        for src, dst in reversed(moved_paths):
            if dst.exists():
                shutil.move(str(dst), str(src))
        if archive_dir.exists() and not any(archive_dir.iterdir()):
            archive_dir.rmdir()
        raise ArchiveError(f"Move failed, rolled back: {e}") from e

    return report


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python3 -m agents.lib.archive {year}", file=sys.stderr)
        sys.exit(2)
    try:
        year = int(sys.argv[1])
    except ValueError:
        print(f"Year must be a 4-digit integer, got {sys.argv[1]!r}", file=sys.stderr)
        sys.exit(2)
    try:
        report = archive_year(year, root=Path.cwd())
    except ArchiveError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Archived {len(report.moved)} items to archive/{year}/")
    for name in report.moved:
        print(f"  v {name}")
    for name in report.skipped:
        print(f"  - {name} (not present)")
