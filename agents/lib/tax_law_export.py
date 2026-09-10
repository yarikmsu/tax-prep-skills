#!/usr/bin/env python3
"""A copy of the tax-law cache in a directory of its own, for the auditor to read.

WHY THIS EXISTS (V7 of the 09-05 backlog). `tax-law-freshness-auditor` is the
only agent here with network access, and AGENTS.md §1 names the shape that makes
dangerous: an agent that can both read `taxpayer_profile.json` and reach the
internet. Its definition says "read `tax_law_<year>.json` and nothing else in the
workspace", and that sentence is PROMPT-LEVEL — the category V6 exists to escape.
`Read` is measurably not directory-confined on this host, so nothing enforces it.

What CAN be enforced is what the agent is pointed at. This copies the cache into
a fresh directory holding nothing else, and the auditor is launched there with
the workspace never named. That is the V6 pattern rather than a new one: the
parent extracts, and hands over only what the sub-task needs.

THE COPY IS FAITHFUL, and the first draft of this module got that exactly wrong.
It FILTERED — dropping keys the schema does not define — and the auditor's job
includes reporting keys the schema does not define. Its item 6 is "nothing
statutory-fixed has leaked in"; its item 8 requires `state.type` as a positive
declaration. A cache falsely calling Colorado a no-income-tax state arrived at
the auditor as a clean generic block, because the filter had removed the very
thing the finding is made of. A sanitiser that resolves findings on behalf of
the reviewer it feeds is worse than no sanitiser.

So nothing is removed. Instead the export REFUSES when the cache holds anything
SSN- or credential-shaped, in a KEY or in a string, and copies verbatim
otherwise. Identity data never reaches the copy because there is no copy, which
is the fail-closed half of the same requirement and costs the auditor nothing.

**SSN- and credential-shape, not "identity-free"** — the distinction is the one
this repository already records about `generate_tax_pdf.py`: `secure_fs.redact`
catches the shapes the PII gate catches, and a taxpayer's NAME has neither an
SSN shape nor a credential shape. A name in a `note` field travels. Say what is
enforced, because the next reader will treat the copy as sanitised otherwise.

Two consequences of using the REDACTOR as a detector, and both are deliberate.
It has no `.pii-allowlist` — that file is read by the shell gate, not by
`secure_fs` — so a synthetic `000-00-0000` in a cache is refused like any other.
And its bare-digit rule is `[0-9]{9}` ANYWHERE rather than the gate's maximal
run of exactly nine, so a ten-digit number refuses too. Both err toward refusing,
which is the direction a fetched-from-`.gov` artifact can afford: this is not a
diff full of hashes and identifiers, it is rates and brackets, and the refusal
is a line the operator reads rather than a silent drop.

WHAT THIS DOES NOT DO is check freshness, validate the schema, or judge a source
host. `check_freshness`, `validate_cache` and the auditor's own items 3 and 4
answer those, and the auditor answers them better than a regex would.

**THIS ONLY ISOLATES ANYTHING IF `TAX_WORKSPACE` IS SET** (Y4). Unset, the
workspace IS the clone — and the auditor's definition tells it four times to read
`agents/lib/tax_law_fetcher.py` for the schema, so handing it the schema hands it
the directory holding `taxpayer_profile.json`. The copy is then a tidier input,
not a boundary. `docs/ops/season-open-runbook.md` states the precondition.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from agents.lib.secure_fs import (  # noqa: E402
    first_non_finite,
    is_reparse_point,
    redact,
    secure_path,
    write_secure,
)
from agents.lib.tax_law_fetcher import get_tax_law_path  # noqa: E402
from agents.lib.workspace import WorkspaceError, resolve_workspace  # noqa: E402


class ExportError(Exception):
    """The copy could not be made, or could not be made safe to hand over."""


def identifier_findings(node, dotted: str = "") -> "list[str]":
    """Paths where `redact` changes a KEY or a string, redacted for printing.

    KEYS as well as values, and that is the half the first draft missed. A cache
    is JSON: `{<an SSN>: "Jane"}` puts the value in the key position, where
    a value-only scan never looks — and the report itself then printed it.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{dotted}.{key}" if dotted else str(key)
            if isinstance(key, str) and redact(key) != key:
                found.append(f"{redact(path)} (in the key)")
            found.extend(identifier_findings(value, path))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(identifier_findings(item, f"{dotted}[{index}]"))
    elif isinstance(node, str) and redact(node) != node:
        # The PATH, never the value and never its redacted form: `[REDACTED]`
        # inside a URL still names the query parameter that carried it.
        found.append(redact(dotted))
    return found


def _resolve_dest(dest, root: Path, year: int) -> Path:
    """A directory of its own — fresh, empty, and not inside the workspace.

    `docs/ops/review-subagent-acceptance.md` records this trap in its own words
    after paying for it once: "One directory, cleared, one file, named." A reused
    directory leaves last year's cache beside this one and the auditor reports on
    whichever it opens. Nothing here CLEARS a directory the caller named, because
    the first draft's `mkdir(exist_ok=True)` plus a same-basename target
    overwrote the live `tax_law_<year>.json` when `--dest` was the workspace.
    """
    if dest is None:
        # 0700 at creation, unpredictable by name. Two exports of the same year
        # from two workspaces must not collide.
        return Path(tempfile.mkdtemp(prefix=f"tax-law-audit-{year}-"))
    text = str(dest).strip()
    if not text:
        # `Path("")` is `.` — it chmodded the current directory and printed
        # "point the auditor at your CWD".
        raise ExportError("--dest was empty; name a directory or omit it")
    chosen = Path(text).expanduser().resolve()
    workspace = root.resolve()
    if chosen == workspace or workspace in chosen.parents or chosen in workspace.parents:
        raise ExportError(
            "--dest is inside the workspace (or contains it). The copy would sit "
            "beside the taxpayer's files, which is what this command exists to "
            "avoid, and a same-named target would overwrite the live cache.")
    if chosen.exists() and any(chosen.iterdir()):
        raise ExportError(
            f"--dest {redact(chosen)} already holds files. Name a fresh directory: "
            "one that already has a cache in it gives the auditor two and no way "
            "to say which it read.")
    chosen.mkdir(parents=True, exist_ok=True)
    return chosen


def build_audit_copy(year: int, workspace=None, dest=None) -> Path:
    """Copy `tax_law_<year>.json` into a directory of its own, or refuse."""
    root = resolve_workspace(workspace)
    source = get_tax_law_path(year, str(root))
    if not source.is_file():
        raise ExportError(f"no cache at {redact(source)} — fetch it first")
    # `is_reparse_point`, not `is_symlink`: the latter is False for an NTFS
    # junction. Without it, a `tax_law_<year>.json` pointed at
    # `taxpayer_profile.json` would be copied by the one command whose job is
    # keeping that file away from a network-capable agent.
    if is_reparse_point(source):
        raise ExportError(
            f"{redact(source)} is a symlink or junction — refusing to read it")

    # ONE read, and the bytes that are validated are the bytes that are copied.
    # This is deliberately not `load_tax_law`, which is the shared read site: it
    # returns a parsed dict rather than the text, and it DELETES a cache whose
    # JSON will not parse. A read-only export must not remove the taxpayer's
    # file. The two guards it carries are called here directly, so the rules are
    # shared even though the read is not.
    try:
        text = source.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, ValueError) as e:
        raise ExportError(f"cannot read the cache: {redact(e)}") from e
    if not isinstance(data, dict):
        raise ExportError("the cache is not a JSON object")
    bad = first_non_finite(data)
    if bad is not None:
        # `json.loads` admits NaN and Infinity, and re-emitting them produces a
        # file a strict reader rejects — a clean-looking export the auditor
        # cannot open, or opens and reads `Infinity` as a rate.
        raise ExportError(
            f"the cache holds a non-finite number at {redact(bad)} — it is "
            "corrupt; re-run /tax-setup before auditing it")

    findings = identifier_findings(data)
    if findings:
        raise ExportError(
            "the cache holds SSN- or credential-shaped text and will NOT be "
            "copied, because the agent that reads the copy can reach the "
            "network:\n  " + "\n  ".join(findings)
            + "\nA cache is fetched from .gov sources and has no business "
              "holding one. Remove it and re-run /tax-setup.")

    target = _resolve_dest(dest, root, year) / source.name
    secure_path(target.parent)
    write_secure(target, text)
    return target


def _main(argv) -> int:
    parser = argparse.ArgumentParser(
        prog="tax_law_export.py",
        description="Copy tax_law_<YEAR>.json into a directory of its own and "
                    "print that directory. Point tax-law-freshness-auditor at "
                    "the printed path and do not name the workspace to it: the "
                    "isolation is what the agent can SEE, not what its prompt "
                    "says. Set TAX_WORKSPACE first, or the workspace is the "
                    "clone and there is nothing to isolate it from.")
    parser.add_argument("year", type=int)
    parser.add_argument("workspace", nargs="?", default=None)
    parser.add_argument("--dest", default=None,
                        help="a FRESH directory outside the workspace")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:  # argparse's own usage errors
        return int(e.code or 0)
    try:
        target = build_audit_copy(args.year, args.workspace, args.dest)
    except ExportError as e:
        # 2 is BLOCKED, the house meaning everywhere else here: capabilities,
        # tie_out, preflight, the host guard. The first draft used it for
        # "copied, with findings", which is the opposite.
        print(f"BLOCKED: {redact(e)}", file=sys.stderr)
        return 2
    except (WorkspaceError, OSError, ValueError) as e:
        # Named rather than a bare `except Exception`: a WorkspaceError that
        # escapes reaches the user as a traceback, which is a worse answer than
        # the line it replaced. Y4's registry requires this module to say so.
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1
    # NOT redacted, and this is the one place that is right — the same call
    # `workspace.py --path` makes, for the same reason: this line exists to be
    # pasted at the auditor, and `[REDACTED]` is not a directory.
    print(target.parent)
    # Same deliberate exception as the line above, and the same reason: a
    # redacted path is not a command anyone can run.
    print("Remove it when the audit is done: rm -rf", target.parent,
          file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(_main(sys.argv[1:]))
