#!/usr/bin/env python3
"""The validation receipt: what the gates saw, bound to the inputs they saw it on.

V8 of `docs/roadmap/2026-09-05-staff-agent-review.md`. The capability gate
(/tax-compute Step 0.7) and the tie-out gate (Step 12.7) each PASS against the
workspace as it stood when they ran. Nothing then bound that pass to those
inputs: a document could arrive, a figure could be edited, the law could be
re-fetched, and /tax-generate would assemble a filing package on gates that
were never scored against what it is packaging.

This module records, per gate, the digest of every file that gate read, and
`verify` re-reads them. A file that changed, one that appeared, one that went
away, and an outcome that was not a pass are each named.

WHAT IT IS NOT. A hash receipt detects STALENESS, not tampering: anything that
can edit `result.json` can rewrite the receipt beside it. It is a guard against
the ordinary way a return goes wrong — the second 1099 that arrives on Thursday
— and it is not a signature against a hostile process, which is why nothing here
is called a signature.

THE INVALIDATION SET IS THE INPUT SET AND NOTHING WIDER, per gate. A gate is not
invalidated by a file it never read: `answers.md` is deliberately absent, because
/tax-review appends to it after the return is computed and a receipt that fires
on that teaches the agent to re-run gates it has no reason to re-run — the
gates-that-cry-wolf failure PR #69 already paid for. What each gate reads is
`GATE_INPUTS` below, and that list is the contract.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.rollover import RolloverError, _validate_subpath  # noqa: E402
from agents.lib.secure_fs import atomic_write, redact  # noqa: E402


class ReceiptError(Exception):
    """Raised when a receipt cannot be written or cannot be read."""


RECEIPT_NAME = "validation_receipt.json"
# The receipt FORMAT. A reader that does not know a version refuses rather than
# guessing at a shape — an unrecognised receipt is not a valid one.
RECEIPT_VERSION = 1

PASS = "PASS"
_GATES = ("capabilities", "tie_out")
# Every gate a filing package depends on. `verify` requires all of them, so a
# workspace where only one ran is refused with the other one named.
REQUIRED_GATES = _GATES


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_digest(path: Path) -> str:
    """A file's digest, read in chunks: a scanned return runs to tens of MB and
    the 100 MB source cap parse_utils enforces is the upper bound here too.

    An unreadable file is a `ReceiptError` and never a bare `OSError`: both
    gates call this INSIDE the handler that protects their verdict, and an
    exception class that handler does not know turns a FAILED hard gate into a
    traceback and exit 1 — which /tax-compute Step 12.7 documents as "the ledger
    is missing", sending the agent to fix a ledger that is fine.
    """
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError as e:
        raise ReceiptError(f"could not read {redact(path)}: {redact(e)}") from e
    return h.hexdigest()


def tree_digest(root: Path, pattern: str = "*.py") -> str:
    """A digest identifying one tree of files: each path, then its contents.

    The PATH is hashed as well as the bytes, because a MOVED file is a different
    tree even when every byte is accounted for — two skill trees whose reference
    files have been renamed are not the same instructions, and a step that links
    `references/step-5.md` in a tree where that file is now called something else
    is one an agent cannot follow.

    `pattern` goes to `rglob`, so a nested tree is covered by "*.md" and a flat
    one by the default. The path is taken RELATIVE to `root`, which for a flat
    glob is the bare filename — that is deliberate, and it is why `engine_digest`
    below still produces the digest every receipt in the wild was written with.
    """
    h = hashlib.sha256()
    for path in sorted(root.rglob(pattern)):
        h.update(str(path.relative_to(root)).encode())
        h.update(_file_digest(path).encode())
    return h.hexdigest()


def engine_digest() -> str:
    """A digest over `agents/lib/*.py` — the engine that produced the result.

    Not a hand-maintained version string. This repository has been bitten four
    times by a quantity kept in two places (skill-invariants.sh MEASURED COSTS
    exists because of it), and a version constant nobody remembers to bump reads
    fresh while describing an engine that has moved. The digest cannot go stale
    because it is not a copy of anything.

    Read from the INSTALLED engine — this file's own directory — never from the
    workspace, so a receipt written for a temp-directory workspace still records
    the engine that wrote it.
    """
    return tree_digest(Path(__file__).resolve().parent)


def result_schema_digest() -> str:
    """The digest of `result_template.json` — the shape `result.json` was
    written against. A renamed template field has no compile-time signal
    (AGENTS.md §8 says so and tells you to check the generator by hand); a
    receipt written against one shape and verified against another at least
    says which side moved."""
    template = (Path(__file__).resolve().parent.parent
                / "templates" / "result_template.json")
    if not template.is_file():
        raise ReceiptError(
            f"no result template at {redact(template)} — the engine directory "
            "is incomplete; run agents/lib/preflight.py")
    return _file_digest(template)


def _tax_year(workspace: Path) -> "int | None":
    """The year `result.json` declares, or None. The law file is named by it,
    and a workspace with no result yet (the capability gate runs first) has no
    year to name."""
    path = workspace / "result.json"
    if not path.is_file():
        return None
    try:
        meta = json.loads(path.read_text()).get("meta")
    except (OSError, ValueError):
        return None
    year = meta.get("tax_year") if isinstance(meta, dict) else None
    return year if isinstance(year, int) and not isinstance(year, bool) else None


def _documents(workspace: Path) -> "list[str]":
    """Every source document, by the same rule the tie-out gate's coverage check
    uses — so the two agree on what a document is, and a file that gate counts
    cannot be one this receipt ignores. Names as that gate lists them."""
    from agents.lib.tie_out import list_document_files
    return list(list_document_files(workspace))


def _entry_key(relname: str) -> str:
    """The map key for a workspace-relative path.

    A DIGEST of the name, not the name: a source document can be called
    `W2_<ssn>.pdf` — that is why `scan_workspace` refuses one — and this file is
    written to disk, where AGENTS.md §1 forbids a full identifier in any
    intermediate. The key stays collision-resistant and stable, and the redacted
    display name rides beside it for the messages.
    """
    return _sha256(relname.encode())


def gate_inputs(workspace: Path, gate: str, *,
                contents: "dict[str, bytes] | None" = None) -> dict:
    """{key: {name, digest}} for every file `gate` reads.

    A file that is absent is simply not in the map, and `verify` re-derives the
    map rather than re-hashing the recorded paths: that is what makes an ADDED
    document — the second 1099 arriving after the tie-out passed — a drift
    rather than an invisible one.

    `contents` supplies bytes the caller has ALREADY read, for a gate that
    parses a file and records it afterwards: re-reading there would hash a file
    the gate did not evaluate, and a profile edited in that window produced a
    PASS receipt for flags the gate never saw.
    """
    if gate not in _GATES:
        raise ReceiptError(f"unknown gate {gate!r}; expected one of {', '.join(_GATES)}")
    workspace = Path(workspace).resolve()
    contents = contents or {}
    if gate == "capabilities":
        # The capability gate reads the profile's flags and nothing else, and it
        # runs BEFORE result.json exists.
        names = ["taxpayer_profile.json"]
    else:
        names = ["parse_ledger.json", "result.json", "taxpayer_profile.json"]
        year = _tax_year(workspace)
        if year is not None:
            names.append(f"tax_law_{year}.json")
        names.extend(_documents(workspace))
    found = {}
    for name in names:
        if name in contents:
            found[_entry_key(name)] = {"name": redact(name),
                                       "digest": _sha256(contents[name])}
            continue
        path = workspace / name
        if not path.exists():
            continue
        # Containment is checked on the RESOLVED target and the key stays the
        # name as listed: a second document that is a symlink to the first
        # resolves to the same path, and keying on that collapsed two entries
        # into one — an added document invisible to the check that exists to see
        # exactly that.
        _validate_subpath(path, workspace)
        if path.is_file():
            found[_entry_key(name)] = {"name": redact(name),
                                       "digest": _file_digest(path)}
    return found


def _read(workspace: Path) -> dict:
    path = Path(workspace).resolve() / RECEIPT_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ReceiptError(
            f"{redact(path)} is not readable JSON: {redact(e)} — delete it and "
            "re-run the gates rather than editing it by hand") from e
    if not isinstance(data, dict):
        raise ReceiptError(f"{redact(path)} is not a receipt object")
    return data


def record(workspace, gate: str, outcome: str, *,
           inputs: "dict[str, str] | None" = None,
           now: "datetime | None" = None) -> Path:
    """Write `gate`'s outcome and the digests of what it read. Returns the path.

    `inputs` is the map the gate captured BEFORE it evaluated, and passing it is
    how the receipt describes the run that happened rather than the workspace as
    it stands a second later. A gate reads its inputs, computes a verdict, prints
    it and writes its report; hashing here instead would bind that verdict to a
    document that arrived during those steps — a PASS certifying a file the gate
    never saw, which is the exact failure this module exists to catch. Omitted,
    the map is read now, which is right for a caller that has not evaluated
    anything (the CLI's `record`).

    MERGES: the two gates run at opposite ends of /tax-compute, and the second
    must not erase the first. Each entry carries its own inputs, so the earlier
    gate's pass stays bound to the files IT saw — a profile edited between the
    two invalidates the capability gate and leaves the tie-out alone, which is
    the truth of what happened.
    """
    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise ReceiptError(f"no workspace at {redact(workspace)}")
    if gate not in _GATES:
        raise ReceiptError(f"unknown gate {gate!r}; expected one of {', '.join(_GATES)}")
    if not isinstance(outcome, str) or not outcome.strip():
        raise ReceiptError("an outcome is a non-empty string (PASS, or what failed)")
    data = _read(workspace)
    if data and data.get("receipt_version") != RECEIPT_VERSION:
        # A receipt from another format is not merged into: its entries mean
        # whatever that version meant. Start a new one; the gates are re-run
        # anyway, which is what produces this call.
        data = {}
    data["receipt_version"] = RECEIPT_VERSION
    gates = data.get("gates")
    if not isinstance(gates, dict):
        gates = {}
    data["gates"] = gates
    data["gates"][gate] = {
        "outcome": outcome,
        "at": (now or datetime.now()).replace(microsecond=0).isoformat(),
        "engine": engine_digest(),
        "result_schema": result_schema_digest(),
        "inputs": gate_inputs(workspace, gate) if inputs is None else dict(inputs),
    }
    return _write(workspace, data)


def _write(workspace: Path, data: dict) -> Path:
    """Temp-then-rename inside the workspace, then owner-only.

    The fourth hand-rolled copy of this dance in `agents/lib/` — V10 of the
    09-05 backlog is where they collapse into one writer in `secure_fs`; adding
    a fifth call site to that item is cheaper than shipping a receipt that can
    be half-written, which is the one state a reader cannot tell from a stale
    one.
    """
    path = workspace / RECEIPT_NAME
    # `secure_fs.atomic_write` is where the temp-then-rename lives now (V10),
    # secured before the first byte and removed on any failure. The receipt
    # names the taxpayer's own documents — `documents/W2_Acme.pdf` names an
    # employer before anyone opens it, AGENTS.md §1's listing rule — so an
    # unsecured moment is a disclosure, not an untidiness.
    try:
        with atomic_write(path) as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
    except OSError as e:
        raise ReceiptError(f"could not write {redact(path)}: {redact(e)}") from e
    return path


class VerifyReport:
    """Whether a filing package may be assembled from this workspace."""

    def __init__(self, valid: bool, reasons: "list[str]"):
        self.valid = valid
        self.reasons = reasons

    def to_dict(self) -> dict:
        return {"valid": self.valid, "reasons": self.reasons}


def verify(workspace, required: "tuple[str, ...]" = REQUIRED_GATES) -> VerifyReport:
    """Re-read what each gate read and report every way the receipt is stale.

    Every reason is stated, not just the first: an agent handed one drift at a
    time re-runs the gates once per file.
    """
    workspace = Path(workspace).resolve()
    data = _read(workspace)
    if not data:
        return VerifyReport(False, [
            f"no {RECEIPT_NAME} in this workspace — the capability gate "
            "(/tax-compute Step 0.7) and the tie-out gate (Step 12.7) write it, "
            "so a workspace without one has not run them"])
    if data.get("receipt_version") != RECEIPT_VERSION:
        return VerifyReport(False, [
            f"{RECEIPT_NAME} declares version {data.get('receipt_version')!r}, "
            f"and this engine writes version {RECEIPT_VERSION} — re-run the gates"])
    reasons = []
    gates = data.get("gates")
    if not isinstance(gates, dict):
        gates = {}
    engine = engine_digest()
    schema = result_schema_digest()
    for gate in required:
        entry = gates.get(gate)
        if not isinstance(entry, dict):
            reasons.append(f"the {gate} gate has not run in this workspace")
            continue
        if entry.get("outcome") != PASS:
            reasons.append(
                f"the {gate} gate recorded {entry.get('outcome')!r}, not {PASS}")
        if entry.get("engine") != engine:
            reasons.append(
                f"the {gate} gate ran on a different engine than this one — "
                "agents/lib/ changed since, so its verdict is about code that is "
                "no longer here")
        if entry.get("result_schema") != schema:
            reasons.append(
                f"the {gate} gate ran against a different result_template.json")
        recorded = entry.get("inputs")
        if not isinstance(recorded, dict):
            reasons.append(f"the {gate} gate's entry records no inputs")
            continue
        current = gate_inputs(workspace, gate)

        def _name(key):
            """The display name either side holds for a key. Already redacted
            when it was written; a receipt from a reader that lost it says so
            rather than printing a key nobody can act on."""
            for side in (recorded, current):
                entry = side.get(key)
                if isinstance(entry, dict) and entry.get("name"):
                    return entry["name"]
            return "a file this receipt no longer names"

        for key in sorted(set(recorded) | set(current), key=_name):
            if key not in current:
                reasons.append(f"{gate}: {_name(key)} is gone since that gate ran")
            elif key not in recorded:
                reasons.append(f"{gate}: {_name(key)} appeared after that gate ran")
            elif (recorded[key] or {}).get("digest") != current[key]["digest"]:
                reasons.append(f"{gate}: {_name(key)} changed since that gate ran")
    return VerifyReport(not reasons, reasons)


def covers_result(workspace, result_path, *, contents: "bytes | None" = None) -> str:
    """"" when the tie-out gate validated exactly this file, else why not.

    `filing_package --result` accepts any path inside the workspace, and the
    gates only ever validate `result.json`. Verifying the receipt while
    packaging some other file is a valid receipt on a return no gate has seen,
    so the packaged file is checked by NAME and by DIGEST — a copy of
    result.json under another name is still not the file the ledger tied out
    against, and saying so by name is cheaper than explaining a coincidence.
    """
    workspace = Path(workspace).resolve()
    result_path = Path(result_path).resolve()
    name = str(result_path.relative_to(workspace)) \
        if result_path.is_relative_to(workspace) else str(result_path)
    if name != "result.json":
        return (f"the package would be assembled from {redact(name)}, and the "
                "gates validate result.json — no gate has seen this file")
    gates = _read(workspace).get("gates")
    entry = gates.get("tie_out") if isinstance(gates, dict) else None
    inputs = entry.get("inputs") if isinstance(entry, dict) else None
    recorded = (inputs or {}).get(_entry_key("result.json"))
    digest = recorded.get("digest") if isinstance(recorded, dict) else None
    # `contents` is the bytes the caller has already parsed. Re-reading the path
    # here checks a file that may no longer be the one being packaged: a
    # recompute landing between the read and this call validated the REPLACEMENT
    # while the package was still being assembled from the older result in
    # memory.
    if contents is not None:
        current = _sha256(contents)
    elif result_path.is_file():
        current = _file_digest(result_path)
    else:
        current = None
    if digest is not None and current is not None and digest != current:
        return "result.json changed since the tie-out gate ran"
    return ""


_USAGE = """usage:
  receipt.py record --workspace DIR --gate {capabilities|tie_out} --outcome PASS
  receipt.py verify --workspace DIR [--json]

`verify` exits 0 when a filing package may be assembled, 2 when the receipt is
missing or stale (every reason printed), and 1 when it could not run at all."""


def _main(argv: list) -> int:
    import argparse
    if not argv:
        print(_USAGE)
        return 0
    parser = argparse.ArgumentParser(prog="agents.lib.receipt", usage=_USAGE)
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("record")
    write.add_argument("--workspace", required=True)
    write.add_argument("--gate", required=True, choices=list(_GATES))
    write.add_argument("--outcome", required=True)
    check = sub.add_parser("verify")
    check.add_argument("--workspace", required=True)
    check.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            path = record(args.workspace, args.gate, args.outcome)
            print(f"recorded {args.gate}={args.outcome} in {redact(path)}")
            return 0
        report = verify(args.workspace)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        elif report.valid:
            print("VALID: every gate passed on the inputs that are here now.")
        else:
            print("STALE RECEIPT — a filing package would not describe this "
                  "workspace:")
            for reason in report.reasons:
                print(f"  - {reason}")
            print("\nRe-run /tax-compute (its Step 0.7 and Step 12.7 gates "
                  "rewrite the receipt) before assembling the package.")
        return 0 if report.valid else 2
    except (ReceiptError, RolloverError, OSError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
