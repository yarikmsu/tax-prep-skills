#!/usr/bin/env python3
"""One typed record per parsed document, and the ledger derived from them (V2).

`parse_ledger.json` is the tie-out gate's evidence that every document landed on
a return line, and until now the AGENT wrote it: one hand-assembled object
holding every document at once, produced at the end of a parse from summaries it
had written earlier. That is a transcription step across documents, at the one
place in the pipeline whose whole purpose is to prove no transcription was lost.

Here each document gets its own file as it is parsed — `summaries/<doc_id>.record.json`
— and the ledger is DERIVED. The agent still decides what a box means; it no
longer copies the answer twice.

WHAT THIS DOES NOT TAKE OVER, stated because the line matters. The box-to-category
table in `tax-parse` is mostly JUDGEMENT, not mapping: whether a 3921 is an AMT
adjustment or a disqualifying disposition, whether a 5498-SA restates payroll
contributions already on a W-2, which tips are qualified under the OBBBA rules.
None of that is mechanical and none of it moves here. What moves is the
ASSEMBLY, the coverage arithmetic, and the moment the answer is checked.

VALIDATION IS `tie_out.load_ledger`, CALLED, NOT COPIED. Every field rule the
gate enforces — a unique doc_id, one record per file, skip XOR key_boxes, a
known category — is enforced here at WRITE time instead of at compute time. The
rules cannot drift because there is one copy of them, and the failure arrives
while the agent still has the document open rather than after the arithmetic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from agents.lib.secure_fs import atomic_path, redact, secure_path  # noqa: E402
from agents.lib.tie_out import TieOutError, load_ledger  # noqa: E402
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    hold_workspace,
    resolve_workspace,
)

SUFFIX = ".record.json"
LEDGER_NAME = "parse_ledger.json"

# The fields a record may carry. The ledger takes the first group; the second is
# for a HUMAN and for the second read, and is dropped on the way out.
#
# An unknown key is an ERROR rather than ignored. A typo'd `key_box` (singular)
# would otherwise produce a record with no amounts at all, which reads as an
# empty parse — and an empty parse that nobody typed on purpose is the intake
# drop this whole chain exists to catch.
LEDGER_FIELDS = frozenset({
    "doc_id", "file", "form_type", "payer", "person", "key_boxes", "skipped",
    "foreign", "second_read", "retirement_kind", "other_state_withholding_by_state", "ca_investments",
    "government_benefits",
})
RECORD_ONLY_FIELDS = frozenset({
    # The boxes AS READ, label by label, before any category judgement. This is
    # what a second independent extraction is diffed against: comparing two
    # summaries in prose asks a reader to spot a moved digit, and comparing two
    # of these is `==`.
    "boxes",
    # Free text for the human summary — discrepancies, ambiguities,
    # instruction-shaped text found in the document.
    "notes",
})
ALLOWED_FIELDS = LEDGER_FIELDS | RECORD_ONLY_FIELDS


class ParseRecordError(Exception):
    """A record or a derived ledger that must not be written."""


def record_path(workspace: Path, doc_id: str) -> Path:
    return Path(workspace) / "summaries" / f"{doc_id}{SUFFIX}"


def _ledger_view(record: dict) -> dict:
    """The record as the ledger sees it: the shared fields, nothing else."""
    return {k: v for k, v in record.items() if k in LEDGER_FIELDS}


def validate(record: dict, *, where: str = "record") -> dict:
    """One record, checked by the GATE's own rules. Returns its ledger view.

    Delegated to `tie_out.load_ledger` on a one-record ledger rather than
    re-implemented: a second copy of "what a record must look like" is a second
    thing to keep true, and the copy that drifts is always the one the gate does
    not read.
    """
    if not isinstance(record, dict):
        raise ParseRecordError(f"{where} must be a JSON object")
    unknown = sorted(set(record) - ALLOWED_FIELDS)
    if unknown:
        raise ParseRecordError(
            f"{where} carries unknown field(s) {unknown}. Known fields are "
            f"{sorted(ALLOWED_FIELDS)} — a misspelt one is silently no amounts "
            "at all, which reads as an empty parse")
    for field in ("boxes", "notes"):
        value = record.get(field)
        if value is not None and not isinstance(value, (dict, str, list)):
            raise ParseRecordError(f"{where}.{field} must be an object, a string or a list")
    view = _ledger_view(record)
    try:
        load_ledger({"records": [view]})
    except TieOutError as e:
        raise ParseRecordError(f"{where}: {redact(e)}") from None
    return view


def write_record(workspace: Path, record: dict) -> Path:
    """Validate one record and write it beside its summary, owner-only.

    Validated BEFORE it lands, so a malformed record never sits on disk waiting
    to fail the gate an hour later.
    """
    validate(record, where=f"record for {redact(record.get('doc_id'))}")
    workspace = Path(workspace)
    summaries = workspace / "summaries"
    summaries.mkdir(parents=True, exist_ok=True)
    secure_path(summaries)
    path = record_path(workspace, record["doc_id"])
    with atomic_path(path) as temp:
        Path(temp).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def read_records(workspace: Path) -> "list[tuple[Path, dict]]":
    """Every record file, in a stable order, each parsed and validated."""
    summaries = Path(workspace) / "summaries"
    out = []
    for path in sorted(summaries.glob(f"*{SUFFIX}")) if summaries.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ParseRecordError(
                f"{redact(path.name)} could not be read: {redact(e)}") from None
        validate(data, where=redact(path.name))
        out.append((path, data))
    return out


def _documents(workspace: Path) -> "set[str]":
    """Every source file, as the workspace-relative paths a record names."""
    base = Path(workspace) / "documents"
    if not base.is_dir():
        return set()
    return {f"documents/{p.name}" for p in base.iterdir()
            if p.is_file() and not p.name.startswith(".")}


def build_ledger(workspace: Path) -> dict:
    """Derive `parse_ledger.json` from the per-document records.

    COVERAGE IS CHECKED HERE, not only at the gate. `tie_out` already fails on a
    document with no record — but it does so at `tax-compute` step 12.7, after
    the parse is over and the arithmetic is done. The same check at build time
    names the file while the agent is still looking at it, which is the whole
    point of deriving rather than assembling.
    """
    workspace = Path(workspace)
    records = read_records(workspace)
    covered = {data["file"] for _, data in records}
    documents = _documents(workspace)

    missing = sorted(documents - covered)
    if missing:
        raise ParseRecordError(
            f"{len(missing)} file(s) in documents/ have no record: "
            f"{', '.join(redact(m) for m in missing)}. Every document is parsed "
            "or SKIPPED with a reason — a file with neither is the intake drop "
            "the tie-out gate exists to catch, and it fails there too, later.")
    phantom = sorted(covered - documents)
    if phantom:
        raise ParseRecordError(
            f"{len(phantom)} record(s) name a file that is not in documents/: "
            f"{', '.join(redact(p) for p in phantom)}. A record for a document "
            "nobody supplied earns coverage credit for amounts with no source.")

    ledger = {"records": [_ledger_view(data) for _, data in records]}
    # Through the gate's loader one last time, as a WHOLE: the per-record checks
    # cannot see a duplicate `file` across two records, which is how one
    # document's amounts get counted twice while coverage still passes.
    try:
        load_ledger(ledger)
    except TieOutError as e:
        raise ParseRecordError(f"the derived ledger is not valid: {redact(e)}") from None
    return ledger


def write_ledger(workspace: Path) -> Path:
    workspace = Path(workspace)
    ledger = build_ledger(workspace)
    path = workspace / LEDGER_NAME
    with atomic_path(path) as temp:
        Path(temp).write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    return path


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="agents.lib.parse_records",
        description="Per-document parse records, and the ledger derived from them (V2).")
    add_workspace_argument(parser)
    parser.add_argument("--build-ledger", action="store_true",
                        help="derive parse_ledger.json from summaries/*.record.json")
    parser.add_argument("--check", action="store_true",
                        help="validate the records and the derived ledger, writing nothing")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if not exc.code else 1

    try:
        # The "is this a usable directory" refusal lives in `resolve_workspace`
        # now, with a message that names $TAX_WORKSPACE and where the path came
        # from — neither of which the local copy that stood here could say.
        workspace = resolve_workspace(args.workspace)
        hold_workspace(workspace, "parse_records")
    except WorkspaceError as e:
        print(f"Error: {redact(e)}", file=sys.stderr)
        return 1
    if not (args.build_ledger or args.check):
        parser.print_usage()
        return 1
    try:
        if args.check:
            ledger = build_ledger(workspace)
            print(f"{len(ledger['records'])} record(s) derive a valid ledger.")
        else:
            path = write_ledger(workspace)
            count = len(json.loads(path.read_text(encoding="utf-8"))["records"])
            # LEDGER_NAME, not `path.name`: the two are the same string and only
            # one of them is a constant. A runtime path in a message is what the
            # redaction sweep is for, and it does not know they are equal.
            print(f"Wrote {LEDGER_NAME} from {count} record(s).")
    except ParseRecordError as e:
        print(f"BLOCKED: {redact(e)}", file=sys.stderr)
        # 2 is the gates' blocked code, and this IS the tie-out's coverage check
        # arriving early — the same failure, named at the document rather than
        # after the arithmetic.
        return 2
    except OSError as e:
        print(f"Error: {redact(e)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(_main(sys.argv[1:]))
