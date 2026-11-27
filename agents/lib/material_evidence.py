"""Record material filing outcomes and the workspace inputs they depend on."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from pathlib import Path

from agents.lib import receipt
from agents.lib.rollover import RolloverError, _validate_subpath
from agents.lib.secure_fs import atomic_write, redact
from agents.lib.workspace import (
    WorkspaceError,
    add_workspace_argument,
    hold_workspace,
    resolve_workspace,
)

FILENAME = receipt.MATERIAL_EVIDENCE_NAME
OUTCOMES = ("open", "computed", "reviewed", "not_applicable")
_FORM_PREFIX = "form."
W2_PREFIX = "w2."
SOURCE_PREFIX = "source."
_EXCLUDED_INPUTS = {FILENAME, receipt.RECEIPT_NAME, "answers.md"}
# Compared case-folded: on a case-insensitive volume (macOS, Windows)
# `ANSWERS.md` IS answers.md, and an exact match let it through.
_EXCLUDED_FOLDED = {name.casefold() for name in _EXCLUDED_INPUTS}
_ID = re.compile(r"[a-z][a-z0-9_.-]{0,95}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
# Ids the tie-out derives from a digest. A hex digest holds a run of nine
# decimal digits about one time in four, which `redact` reads as an
# identifier, so these ids skip that check. The writer refuses manual
# entries under the prefix.
_LEDGER_ID = re.compile(r"(?:source\.[0-9a-f]{64}|w2\.[0-9a-f]{64}\.[12])")


class MaterialEvidenceError(Exception):
    """The material outcome record cannot support final export."""


def _distinct_keys(pairs: list[tuple[str, object]]) -> dict:
    data = dict(pairs)
    if len(data) != len(pairs):
        raise MaterialEvidenceError(f"{FILENAME} contains duplicate keys")
    return data


def _label(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 160
            and value == value.strip() and value.isprintable() and redact(value) == value)


def _source_path(name: str) -> Path:
    if not isinstance(name, str) or redact(name) != name or not name.isprintable():
        raise MaterialEvidenceError("each evidence reference must be a safe workspace-relative path")
    path = Path(name)
    if (path.is_absolute() or path.anchor or "\\" in name or ":" in name or ".." in path.parts
            or name != path.as_posix() or not path.parts or name.casefold() in _EXCLUDED_FOLDED):
        raise MaterialEvidenceError("each evidence reference must name a canonical workspace input")
    return path


def _dependency(workspace: Path, name: str) -> Path:
    path = _source_path(name)
    try:
        resolved = _validate_subpath(workspace / path, workspace)
    except RolloverError as exc:
        raise MaterialEvidenceError("an evidence reference points outside the workspace") from exc
    if resolved.relative_to(workspace.resolve()).as_posix().casefold() in _EXCLUDED_FOLDED:
        raise MaterialEvidenceError("each evidence reference must name a canonical workspace input")
    return resolved


def read(workspace: Path) -> tuple[bytes | None, dict]:
    """Load the exact evidence bytes and validate the versioned record."""
    path = _validate_subpath(workspace / FILENAME, workspace)
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise MaterialEvidenceError(f"{FILENAME} must be a regular file")
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, {}
    try:
        data = json.loads(raw, object_pairs_hook=_distinct_keys)
    except ValueError as exc:
        raise MaterialEvidenceError(f"{FILENAME} is not readable JSON") from exc
    if (not isinstance(data, dict) or type(data.get("version")) is not int
            or data["version"] != 1 or not isinstance(data.get("items"), dict)):
        raise MaterialEvidenceError(f"{FILENAME} must be a version 1 material evidence record")
    _check_items(data["items"])
    return raw, data["items"]


def _check_items(items: dict) -> None:
    for item_id, item in items.items():
        if (not _ID.fullmatch(item_id)
                or (not _LEDGER_ID.fullmatch(item_id) and redact(item_id) != item_id)
                or not isinstance(item, dict)
                or not _label(item.get("label")) or item.get("outcome") not in OUTCOMES):
            raise MaterialEvidenceError(f"{FILENAME} contains an invalid material item")
        inputs = item.get("inputs")
        if (not isinstance(inputs, dict)
                or (item["outcome"] == "open") != (not inputs)):
            raise MaterialEvidenceError(f"{FILENAME} requires source files for each recorded outcome")
        for name, digest in inputs.items():
            _source_path(name)
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise MaterialEvidenceError(f"{FILENAME} contains an invalid source digest")


def _write(workspace: Path, items: dict) -> None:
    # The reader's checks, before anything reaches disk: a record its own
    # reader refuses would wedge every later gate on this workspace.
    _check_items(items)
    with atomic_write(workspace / FILENAME) as handle:
        json.dump({"version": 1, "items": items}, handle, indent=2, sort_keys=True)
        handle.write("\n")


def sync_required_forms(workspace: Path, required: dict[str, str]) -> None:
    """Register open forms from the capability gate without changing other items."""
    sync_group(workspace, _FORM_PREFIX,
               {key: {"label": label, "outcome": "open", "inputs": {}}
                for key, label in required.items()})


def sync_group(workspace: Path, prefix: str, replacements: dict) -> None:
    """Replace the records derived from one gate, preserving the other items."""
    _, items = read(workspace)
    updated = {key: item for key, item in items.items() if not key.startswith(prefix)}
    updated.update(replacements)
    if updated != items:
        _write(workspace, updated)


def open_items(workspace: Path, items: dict, required: dict[str, str]) -> list[str]:
    """Report open or stale items, including required forms absent from the file."""
    reasons = [f"Open material item: {label}." for label in required.values()]
    for item_id, item in sorted(items.items()):
        if item_id in required:
            continue
        label = item["label"]
        if item["outcome"] == "open":
            reasons.append(f"Open material item: {label}.")
            continue
        for name, previous in item["inputs"].items():
            try:
                path = _dependency(workspace, name)
                unchanged = path.is_file() and receipt._file_digest(path) == previous
            except (OSError, RolloverError, MaterialEvidenceError, receipt.ReceiptError):
                unchanged = False
            if not unchanged:
                reasons.append(f"Open material item: {label}. A source changed or is missing.")
                break
    return reasons


def record(workspace: Path, item_id: str, label: str, outcome: str,
           inputs: list[str]) -> None:
    """Record an outcome after working its evidence. The caller holds the workspace lock."""
    if not _ID.fullmatch(item_id) or redact(item_id) != item_id or not _label(label):
        raise MaterialEvidenceError("use a stable item id and a short label without taxpayer details")
    if item_id.startswith(_FORM_PREFIX):
        raise MaterialEvidenceError("required forms must be completed through their filing workflow")
    if item_id.startswith(W2_PREFIX):
        raise MaterialEvidenceError("W-2 box outcomes come from the parse ledger")
    if item_id.startswith(SOURCE_PREFIX):
        raise MaterialEvidenceError("source-document outcomes come from the parse ledger")
    if outcome not in OUTCOMES or (outcome == "open") != (not inputs):
        raise MaterialEvidenceError("record a closed outcome with its source files, or mark it open")
    _, items = read(workspace)
    digests = {}
    for name in inputs:
        path = _dependency(workspace, name)
        if not path.is_file():
            raise MaterialEvidenceError("each evidence reference must name an existing regular file")
        try:
            digests[name] = receipt._file_digest(path)
        except receipt.ReceiptError as exc:
            raise MaterialEvidenceError("an evidence source could not be read") from exc
    item = {"label": label, "outcome": outcome, "inputs": digests}
    if items.get(item_id) != item:
        items[item_id] = item
        _write(workspace, items)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agents.lib.material_evidence")
    add_workspace_argument(parser)
    parser.add_argument("--id", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--outcome", choices=OUTCOMES, required=True)
    parser.add_argument("--input", action="append", default=[],
                        help="workspace-relative evidence or input file (repeatable)")
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace).resolve()
        hold_workspace(workspace, "material_evidence")
        record(workspace, args.id, args.label, args.outcome, args.input)
    except (MaterialEvidenceError, WorkspaceError, RolloverError, receipt.ReceiptError, OSError) as exc:
        print(f"ERROR: {redact(exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
