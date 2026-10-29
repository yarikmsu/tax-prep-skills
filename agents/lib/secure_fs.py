#!/usr/bin/env python3
"""Owner-only filesystem security for PII/financial runtime files and
directories, enforced the same way regardless of platform.

Two POSIX assumptions this codebase used to make silently break on Windows:

1. os.chmod(path, 0o600/0o700) only restricts access on POSIX. On Windows,
   Path.chmod() maps onto SetFileAttributes and only toggles the read-only
   DOS attribute — it does not touch the NTFS DACL an inherited ACE grants
   to every other local account, and os.fchmod does not exist there at all.
   secure_path()/secure_fd() replace every os.chmod(0o600/0o700) call site
   across archive.py, rollover.py, scenario.py, tie_out.py and
   tax_calendar.py with one function that shells out to PowerShell's
   GetAccessControl/SetAccessControl on Windows to build a PROTECTED DACL holding a single
   ACE — the current user, Full Control, nothing inherited and nothing
   left over from before — rather than incrementally patching whatever
   ACL the path already had.

2. pathlib.Path.is_symlink() (and os.path.islink()) is False for an NTFS
   junction: a junction carries a different reparse tag
   (IO_REPARSE_TAG_MOUNT_POINT) than a symlink (IO_REPARSE_TAG_SYMLINK),
   and — unlike a symlink on older Windows — needs no elevated privilege
   to create. Every symlink-refusal check in this codebase (archive.py's
   moved entries and archive/{year} destination, tie_out.py's summaries/)
   existed specifically to stop a runtime path from silently resolving
   outside the workspace; a junction defeats every one of those checks
   while a plain is_symlink() guards them. is_reparse_point() catches
   both.

   It is built on os.lstat() rather than Path.is_symlink() on POSIX too,
   and that is not a stylistic preference: Python 3.14's is_symlink()
   converts EVERY OSError to False, so access denied or a transient I/O
   error reads as "not a link" — a safety certificate for a path that was
   never inspected, handed to a caller that is about to follow it. CI runs
   3.14. Do not simplify it back to p.is_symlink(); nothing else in the
   module would catch the regression.

Command-line use: `python3 -m agents.lib.secure_fs PATH [PATH ...]`
secures each given path (skill instructions invoke this instead of a bare
`chmod`, which inherits the same Windows gap `os.chmod` had). With no
arguments it runs the self-test below instead. A PathRefused — a component
that is a link/junction or could not be inspected at all — STOPS the run
rather than moving to the next path, because the callers pass a tree
outermost-first and a refusal says the paths after it are not what they
appear to be. An ordinary failure does not stop it.
"""

# THIS MODULE MUST IMPORT ON AN INTERPRETER OLDER THAN THE PROJECT MINIMUM.
# It is the redactor `preflight.py` reaches for before it can report anything at
# all, and the failure preflight most often diagnoses IS an old interpreter: on a
# stock macOS `python3` is 3.9, where the PEP 604 annotation below (`list[str] |
# None`) is evaluated at def time and raises. Without this line `secure_fs`
# became unimportable exactly when it was needed, `_redact` fell back to
# withholding, and the verdict replaced all twenty-one module errors — including
# the single line naming the real cause — with "<withheld: the redactor is part
# of the missing engine>", under a banner claiming the engine was missing when
# only the interpreter was old. The engine modules this one reports ON may use
# any supported syntax; this one may not.
from __future__ import annotations

import contextlib
import errno
import math
import os
import re
import stat
import subprocess
import sys
import shutil
import tempfile
from pathlib import Path
from typing import NamedTuple

# Run as a SCRIPT PATH (`python3 agents/lib/secure_fs.py`) a module is not
# inside its package, so nothing imports `agents.lib` and the
# host-interpreter guard living there never runs. Every sibling already
# carries these two lines, incidentally, because it imports another sibling
# through the package; this is the one leaf that imports none — and the one
# module AGENTS.md §1 tells every agent to run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import agents.lib  # noqa: E402,F401 — the host-interpreter guard

# A runtime PATH is sensitive data here, not just a diagnostic string. AGENTS.md
# §1 restricts the runtime DIRECTORIES precisely because an owner-only file
# inside a listable one still discloses its NAME, and under documents/ and
# archive/ those names are a taxpayer's scanned documents. Every failure this
# module reports names the path it failed on, the skill layer invokes the CLI
# below directly, and tax-amend pipes `find archive/{year}/as_filed` into it —
# so an unredacted message writes those names into terminal scrollback and into
# whatever an agent forwards. The absolute case is an SSN in a filename, which
# §1 forbids emitting under any circumstance.
#
# These patterns MIRROR scripts/pre-commit-pii-scan.sh's PII_PATTERNS and its
# broad nine-digit sweep. They cannot share an implementation — that gate is
# bash and runs at commit time, this is a runtime library imported by parse
# sub-tasks and must stay dependency-free — so the duplication is deliberate and
# tested: tests/test_secure_fs.py drives the SSN spellings that gate blocks
# through this redactor and fails if one survives, and reads the PII_PATTERNS
# array out of that script so a family added THERE and not here fails too. A
# prose claim of parity is what let the AWS-secret family sit in the detector
# alone; the test is the claim now.
# The hyphen, dot and slash spellings, shared with `_ID_DELIMITED` below so a
# spelling the gate learns reaches the redactor and the masker together.
_SSN_DELIMS = r"(?:-[0-9]{2}-|\.[0-9]{2}\.|/[0-9]{2}/)"
_SSN_DELIMITED = re.compile(
    rf"[0-9]{{3}}(?:{_SSN_DELIMS}|\s+[0-9]{{2}}\s+)[0-9]{{4}}")
_SECRET = re.compile(
    r"(AKIA|ASIA)[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{36}"
    r"|github_pat_[A-Za-z0-9_]{40,}")
# The AWS SECRET access key has no prefix — 40 unremarkable base64 characters,
# indistinguishable from a hash — so the assignment keyword is what identifies
# it, exactly as in the gate. This family was DETECTED there and missing here
# for one round, which is the worse half of the pair: the gate blocks a path
# holding the credential and then prints it in the block message. A rule that
# is detected but not redacted is a disclosure channel, so parity between the
# two lists is now pinned by a test that reads PII_PATTERNS out of the shell
# script and fails on any member this module does not mirror.
_AWS_SECRET = re.compile(
    r"(?i:aws[_-]secret[_-]access[_-]key)[^A-Za-z0-9]*[A-Za-z0-9/+=]{40}")
# Deliberately broader than any token rule: nine digits ANYWHERE, including
# inside a longer run. Narrowing it would print an SSN embedded in a ten-digit
# account number verbatim, which is the detected-but-not-redacted class of bug
# that turns a safeguard into a disclosure channel. Over-redaction costs
# legibility; under-redaction discloses.
_NINE_DIGITS = re.compile(r"[0-9]{9,}")


def redact(text) -> str:
    """Mask PII-shaped substrings in a path or error message before printing.

    What it does NOT do is hide an ordinary filename: `documents/W2_Acme.pdf`
    still prints, because a message that cannot say which path failed is not a
    message. That is the same trade scripts/pre-commit-pii-scan.sh settled on,
    and the residual is stated here rather than left implied.
    """
    out = str(text)
    # ORDER IS LOAD-BEARING, and the rule is widest match first. Every one of
    # these patterns spans a region that a NARROWER one can match INSIDE, and a
    # value fragmented by an earlier substitution no longer matches the wider
    # form at all — `[REDACTED]` is not in any of their character classes — so
    # the wide rule silently stops firing and most of the credential prints.
    # Both instances are real and both were reported:
    #   _AWS_SECRET   — 40 arbitrary base64 characters, so a digit run inside it
    #                   matches _SSN_DELIMITED or _NINE_DIGITS.
    #   _SECRET       — `sk-[A-Za-z0-9_-]{20,}` accepts hyphens, so a token like
    #                   `sk-000-00-0000...` carries an SSN-SHAPED run inside it;
    #                   running _SSN_DELIMITED first left `sk-[REDACTED]` plus
    #                   twenty unmasked characters of the credential.
    # A credential rule can only be defeated this way, never the reverse: no SSN
    # spelling contains an AKIA/sk-/ghp_/github_pat_ prefix, so moving the two
    # credential families to the front cannot fragment an SSN.
    for pattern in (_AWS_SECRET, _SECRET, _SSN_DELIMITED, _NINE_DIGITS):
        out = pattern.sub("[REDACTED]", out)
    return out


# The SSN/ITIN shapes `mask_ssn` rewrites in EXTRACTED DOCUMENT TEXT. Not
# `redact`'s patterns, and deliberately so: `redact` is the error-message
# redactor — it substitutes `[REDACTED]`, which erases the last four digits the
# skills are told to keep, and its `_NINE_DIGITS` masks any nine-plus digit run,
# which on a W-2 or a bank statement is a routing number, an account number or
# an EIN typed without its hyphen. A masker that erases those breaks FBAR intake
# and the EIN the worker template says to record in full.
#
# Every pattern is digit-bounded on either side, which `_SSN_DELIMITED` is not:
# in a path the surrounding characters are the redactor's concern too, but in a
# page of extracted text the middle of a longer number is not an SSN, and a
# masker that fires there corrupts an amount the return is computed from — the
# over-statement direction AGENTS.md §4 makes a hard error.
#
# Only the hyphen, dot and slash spellings are masked UNLABELED. The
# whitespace spelling and the bare nine-digit run are masked only under an
# identifier label, because both are shapes a document prints for other
# things: nine bare digits is a routing number exactly as often as an SSN, and
# a form that prints cents in their own column extracts as `850 00` — followed
# by a four-digit figure (a year, a box number, the next amount) that is the
# whitespace spelling to the digit. A lookbehind for a comma or dollar sign
# protected only amounts of $1,000 or more; below that line the masker erased
# the box amount. Under a label the ambiguity is gone.
#
# A label is SSN, ITIN, "social security number", or a POSSESSIVE followed by
# TIN / "identification number" / "identifying number" — "RECIPIENT'S TIN" on
# every 1099, "Winner's TIN" on a W-2G, "Shareholder's identifying number" on a
# K-1. Three spellings the attachment forms print in their header are labels
# exactly as printed: "Your taxpayer identification number" (Forms 8995 and
# 8995-A) and "Your identifying number" ending their row, and "Identifying
# number" ending the row (Form 8801) or followed by "as shown" (Form 1116).
# Mid-sentence they are not: Form 8938's "Identifying number or other designation" is a security's,
# Form 8283's "Identifying number:" a partnership's, a CUSIP's heading "other
# identifying number". "your TIN" in prose is no label either, and Form
# 8995-A's column "(d) Taxpayer identification number" holds each business's
# EIN. The possessor is not enumerated on the taxpayer side, where a missed
# form family is an SSN in the transcript; it is enumerated on the PAYER side
# (payer, employer, filer, issuer, lender, trustee, creditor, corporation,
# partnership, estate, trust), where the number is an EIN and a miss masks an
# identifier the forms print hyphenated anyway. "TIN" with no possessive is not
# a label. Each label is word-bounded — `ITIN` sits inside "Awaiting" and
# account number right after both.
#
# The NOUN after "social security" is REQUIRED, and that is the whole of
# what keeps a W-2 readable. Boxes 3, 4 and 7 are "Social security wages",
# "Social security tax withheld" and "Social security tips" — bare labels
# accepted while the noun was optional, so the amount printed under box 3
# with its cents in their own column — a `850 00` cents column, then a
# year — was masked to XXX-XX-<year> and the wage the return is computed
# from was gone. The number
# those labels do sit over is an amount, never an identifier; the box that
# does carry the SSN says "Employee's social security NUMBER", as does every
# other form that prints one. Requiring the noun gives up a bare "Social
# Security" over a nine-digit run, which is the direction that costs
# nothing here: the hyphenated spelling that box would print is masked with
# no label at all.
#
# Two layouts. Same line: label, up to a short run of non-digits, the number.
# Label ABOVE the value, which is how a W-2 box and every 1099 print it: a line
# that carries a label with no identifier-shaped run after it (a W-2 prints
# "OMB No. 1545-0008" to the right of the box label, so "no digits" is the wrong
# test) is followed by a line whose every identifier-shaped run is masked. On a
# 1099 that value line is "PAYER'S TIN RECIPIENT'S TIN" side by side, so a
# payer EIN typed without its hyphen is masked there too — the safe direction,
# and the forms print it hyphenated.
#
# Text order is not the page. A PDF route also asks where each run sits
# (`identifier_spans` per row, decided in `parse_utils._governed_runs`) and
# masks what a label governs by position with `mask_runs`.
_ID_DELIMITED = re.compile(
    rf"(?<![0-9])[0-9]{{3}}{_SSN_DELIMS}([0-9]{{4}})(?![0-9])")
_PAYER_SIDE = (r"(?:payer|employer|filer|issuer|lender|trustee|creditor"
               r"|corporation|partnership|estate|trust)")
_ID_LABEL = (
    r"(?<![A-Za-z])(?:(?:SSN|ITIN)s?(?![A-Za-z])"
    r"|social security[^\S\n]+(?:numbers?|nos?\.?|#)(?![A-Za-z])"
    r"|individual taxpayer identification(?: number)?"
    # Never after "other" (a CUSIP heading) or a possessive, which the
    # possessive alternative below decides, payer side excluded.
    r"|(?<!other\s)(?<!other\s\s)(?<!['\u2019]s\s)identifying[^\S\n]+number(?=[^\S\n]*(?:$|as shown))"
    r"|your[^\S\n]+(?:taxpayer[^\S\n]+identification|identifying)[^\S\n]+number(?=[^\S\n]*$)"
    # Form 4506-C names the first taxpayer and spouse explicitly. A bare
    # business taxpayer identification heading still does not label an SSN.
    r"|(?:first|spouse['\u2019]s)[^\S\n]+taxpayer[^\S\n]+identification[^\S\n]+number(?![A-Za-z])"
    rf"|(?!{_PAYER_SIDE}['\u2019]?s[^\S\n])[A-Za-z]+['\u2019]?s[^\S\n]+"
    r"(?:TIN(?![A-Za-z])|identif(?:ication|ying)(?: number| no\.?)?))")
# Bare, or grouped 3-2-4 by horizontal whitespace of any kind: a cell pasted
# from a web table carries U+00A0 where the page had a space.
_ID_VALUE_RE = re.compile(
    r"(?<![0-9])[0-9]{3}[^\S\n]*[0-9]{2}[^\S\n]*([0-9]{4})(?![0-9])")
# A comb field, nine boxes of one digit each, is how the official fillable
# forms print an SSN, and a flattened one extracts as nine digits apart. Like
# the whitespace spelling it is masked only under a label: Form 1040 prints
# the refund routing number in a comb field too.
_ID_SPACED = re.compile(r"(?<![0-9])[0-9](?![0-9])(?:[^\S\n]+[0-9](?![0-9])){8,}")
_ID_LABEL_RE = re.compile(_ID_LABEL, re.IGNORECASE)
_ID_LABELED_SAME_LINE = re.compile(
    rf"({_ID_LABEL}[^0-9\n]{{0,24}}){_ID_VALUE_RE.pattern}", re.IGNORECASE)
_ID_ANY_VALUE = re.compile(
    rf"{_ID_DELIMITED.pattern}|{_ID_VALUE_RE.pattern}|{_ID_SPACED.pattern}"
    rf"|(?:XXX|\*\*\*)-(?:XX|\*\*)-(?:[0-9]{{4}}|XXXX)", re.IGNORECASE)


def _label_ends(text: str, start: int | None = None, end: int | None = None) -> bool:
    """A label line that already carries its value — in any spelling, or
    already masked, in either case — is a same-line layout, and the line
    under it is something else. The line is `text[start:end]`, by default its
    last line (a table cell can hold two); the text around it stays visible
    to the labels' lookbehinds. Only its LAST label decides: one scan, where
    a pattern tempered past every label took time quadratic in a long line."""
    end = len(text) if end is None else end
    start = text.rfind("\n", 0, end) + 1 if start is None else start
    last = None
    for last in _ID_LABEL_RE.finditer(text, start, end):
        pass
    return last is not None and not _ID_ANY_VALUE.search(text, last.end(), end)


# A label wrapped onto a second row hands it to the row after: Schedule B
# (Form 1116) and Form 1040-NR print the number under the continuation.
_LABEL_CONTINUES = re.compile(r"[^\S\n]*(?:on page 1 of your tax return|\(see instructions\))[^\S\n]*",
                              re.IGNORECASE)


def _mask_spaced(text: str) -> str:
    """Mask a run of spaced digits nine at a time. One row of comb fields is
    one run (Form 1040's four dependents). A run that is not whole nines has
    no boundary to trust, a short comb or a box number beside one, so none of
    its digits is kept."""
    def nines(match: re.Match) -> str:
        digits = re.findall(r"[0-9]", match[0])
        if len(digits) % 9:
            return "XXX-XX-XXXX"
        return " ".join(f"XXX-XX-{''.join(digits[i + 5:i + 9])}" for i in range(0, len(digits), 9))
    return _ID_SPACED.sub(nines, text)


def _mask_spaced_after_labels(text: str) -> str:
    """The same-line layout for the comb spelling: every run after the label,
    which a single-value pattern cannot reach past the first dependent."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if label := _ID_LABEL_RE.search(line):
            lines[i] = line[:label.end()] + _mask_spaced(line[label.end():])
    return "\n".join(lines)


def _mask_under_labels(text: str) -> str:
    """Mask every identifier-shaped run on a line whose PREVIOUS line carries
    an identifier label with no value after it — the label-above-value box of
    a W-2 or 1099. Runs on the RAW text, before the same-line masking, so a
    value the same-line pass has just rewritten still counts as present."""
    raw = text.split("\n")
    lines = list(raw)
    starts = [0]
    for line in raw:
        starts.append(starts[-1] + len(line) + 1)
    for i in range(1, len(raw)):
        above = i - 2 if i > 1 and _LABEL_CONTINUES.fullmatch(raw[i - 1]) else i - 1
        if _label_ends(text, starts[above], starts[above] + len(raw[above])):
            lines[i] = _mask_spaced(_ID_VALUE_RE.sub(r"XXX-XX-\1", raw[i]))
    return "\n".join(lines)


# An identifier already spelled so `mask_ssn` handles it without a label, or
# one that is no SSN: hyphenated or masked, or an EIN. A positional reader
# needs them to know a label's value is there, and, where the page masked one
# itself (a statement showing last fours), where that label's reach ends.
_ID_SPELLED = re.compile(
    rf"{_ID_DELIMITED.pattern}|(?:XXX|\*\*\*)-(?:XX|\*\*)-(?:[0-9]{{4}}|XXXX)"
    r"|(?<![0-9])[0-9]{2}-[0-9]{7}(?![0-9])", re.IGNORECASE)


def identifier_spans(row: str) -> tuple[list, list, list, list]:
    """Where one row of page text prints an identifier label, an
    identifier-shaped run still to mask, an identifier spelled another way
    (`_ID_SPELLED`), and a note continuing a label ("(see instructions)",
    printed under FTB 3506's SSN heading): `[(start, end, carries)]`,
    `[(start, end)]`, `[(start, end, masked)]` and `[(start, end)]`.
    `carries` says whether a value follows the label on its row, `masked`
    whether the page masked that identifier itself (`XXX-XX-1234`). A reader
    that knows where the row sits on the page decides which label governs
    which run; `mask_runs` masks them."""
    last = max((m.start() for m in _ID_ANY_VALUE.finditer(row)), default=-1)
    labels = [(m.start(), m.end(), last >= m.end()) for m in _ID_LABEL_RE.finditer(row)]
    values = sorted({(m.start(), m.end()) for pattern in (_ID_VALUE_RE, _ID_SPACED) for m in pattern.finditer(row)})
    return (labels, values, [(m.start(), m.end(), m[0][0] in "Xx*") for m in _ID_SPELLED.finditer(row)],
            [(m.start(), m.end()) for m in _LABEL_NOTE.finditer(row)])


_LABEL_NOTE = re.compile(r"\(see instructions\)", re.IGNORECASE)
_DIGIT_GROUPS = re.compile(r"[0-9]+(?:[^\S\n]+[0-9]+)*")


class Runs(NamedTuple):
    """Runs prepared once to mask many strings: each run's digits, their
    lengths longest first, and the spelling `mask_ssn` writes for them."""
    lengths: tuple
    spelling: dict


def prepare_runs(runs) -> Runs:
    """A comb row is one run (Form 1040's dependents) whose each nine is an
    SSN of its own, so each nine is a run too: a table can cut the row."""
    spelling: dict[str, str] = {}
    for raw in sorted(runs, key=len, reverse=True):
        digits = re.sub(r"[^0-9]", "", raw)
        spelling.setdefault(digits, _mask_spaced(_ID_VALUE_RE.sub(r"XXX-XX-\1", raw)))
        if len(digits) > 9 and len(digits) % 9 == 0:
            for i in range(0, len(digits), 9):
                spelling.setdefault(digits[i:i + 9], f"XXX-XX-{digits[i + 5:i + 9]}")
    return Runs(tuple(sorted({len(digits) for digits in spelling}, reverse=True)), spelling)


def _run_spans(text: str, runs: Runs) -> list:
    """Where the digits of `runs` print in `text`, however they are spaced:
    `(start, end, digits)`, each starting and ending on a group of digits'
    edge, the longest run first at each place. One pass over the text."""
    found = []
    for sequence in _DIGIT_GROUPS.finditer(text):
        groups = [(sequence.start() + m.start(), sequence.start() + m.end())
                  for m in re.finditer(r"[0-9]+", sequence[0])]
        digits = "".join(text[a:b] for a, b in groups)
        edges = [0]
        for a, b in groups:
            edges.append(edges[-1] + b - a)
        group_at = {edge: k for k, edge in enumerate(edges)}
        k = 0
        while k < len(groups):
            start = edges[k]
            stop = next((group for length in runs.lengths if (group := group_at.get(start + length)) is not None
                         and digits[start:start + length] in runs.spelling), None)
            if stop is None:
                k += 1
                continue
            found.append((groups[k][0], groups[stop - 1][1], digits[start:edges[stop]]))
            k = stop
    return found


def mask_runs(text: str, runs) -> str:
    """Mask each of `runs` (run texts, or `prepare_runs` of them),
    identifier-shaped text a layout reader found in an identifier label's
    box, wherever its digits occur in `text` however they are spaced, in the
    spelling `mask_ssn` writes."""
    runs = runs if isinstance(runs, Runs) else prepare_runs(runs)
    out, last = [], 0
    for start, end, digits in _run_spans(text, runs):
        out += [text[last:start], runs.spelling[digits]]
        last = end
    return "".join(out) + text[last:]


def mask_row_runs(cells: list, runs) -> list:
    """`mask_runs` across one table row, whose cells can split a run: Form
    1040's comb boxes come out of a table as "000 | 00 | 1234". Each digit is
    masked in place, a whole nine keeping its last four, so every cell keeps
    its own text around them."""
    runs = runs if isinstance(runs, Runs) else prepare_runs(runs)
    texts = [cell if isinstance(cell, str) else "" for cell in cells]
    chars = list(" ".join(texts))
    for start, end, _ in _run_spans("".join(chars), runs):
        positions = [i for i in range(start, end) if chars[i] in "0123456789"]
        whole = len(positions) % 9 == 0
        for n, i in enumerate(positions):
            if not (whole and n % 9 >= 5):
                chars[i] = "X"
    out, offset = [], 0
    for cell, text in zip(cells, texts):
        out.append("".join(chars[offset:offset + len(text)]) if isinstance(cell, str) else cell)
        offset += len(text) + 1
    return out


def mask_ssn(value):
    """Mask every SSN/ITIN shape in extracted document text to `XXX-XX-{last4}`.

    The extraction boundary is where this runs — every string
    `parse_utils.extract_pdf_text`, `extract_tables`, `extract_excel` and
    `extract_csv` return — because the parse worker is told to mask the SSN
    AFTER reading it, by which point the full number sits in the session
    transcript: the "intermediate file" AGENTS.md §1 forbids. Scope is
    identifiers only; employer names, addresses, EINs, routing and account
    numbers and every box amount pass through unchanged, here and in a PDF
    route's positional pass (`mask_runs`), which masks only what a page prints
    in an identifier label's box. A non-string (a
    `None` table cell, a numeric workbook cell) is returned as-is: an SSN
    typed as a number has neither delimiter nor label, and coercing every cell
    to text to look for one would change what the worker reads. A cell whose
    COLUMN carries the label is `mask_table`'s case below — it is the one place
    the shape is unambiguous, and the only place a number is masked.
    """
    # Nine bare digits is the shortest maskable shape; every shorter cell
    # (most of a CSV) skips both regex passes.
    if not isinstance(value, str) or len(value) < 9:
        return value
    out = _mask_under_labels(value)
    out = _ID_DELIMITED.sub(r"XXX-XX-\1", out)
    out = _mask_spaced_after_labels(out)
    return _ID_LABELED_SAME_LINE.sub(r"\1XXX-XX-\2", out)


def first_non_finite(node, _path: tuple = ()) -> "str | None":
    """The dotted path of the first non-finite float in a parsed JSON tree,
    or None. `json.loads` admits the NaN/Infinity literals and reads `1e400`
    as inf; both pass `isinstance(x, (int, float))` and defeat every
    comparison (each NaN comparison is False), so every runtime JSON reader
    refuses them at its boundary through this one walk. The path is joined
    only on a hit; a clean pass allocates nothing per node.
    """
    if isinstance(node, float) and not math.isfinite(node):
        return ".".join(_path) if _path else "(top level)"
    if isinstance(node, dict):
        for k, v in node.items():
            found = first_non_finite(v, _path + (str(k),))
            if found is not None:
                return found
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found = first_non_finite(v, _path[:-1] + (f"{_path[-1]}[{i}]",) if _path
                                     else (f"[{i}]",))
            if found is not None:
                return found
    return None


# A TABLE has a layout free text does not: the label in one CELL and the value
# in another. `mask_ssn` sees one cell at a time, so it cannot see the label at
# all — a CSV exported with an `SSN` column, a workbook header row naming one,
# a PDF table whose left column is the label all reach it as a bare nine-digit
# run with nothing beside it, and the bare spelling is masked only under a
# label. That is the designated safe reader handing back the number it exists
# to withhold.
#
# So the two text layouts are re-stated with a cell boundary where the line
# break was: a label cell governs the cell to its RIGHT (the same-line layout)
# and its own COLUMN from that row down (the label-above-value layout, which is
# what a header row is). A label cell is one that carries a label and no value
# of its own — `_label_ends`, the same rule the text uses, which is why a cell
# that reads "SSN:" and then the hyphenated number governs nothing after it.
def _is_id_label_cell(cell) -> bool:
    """True when this cell is an identifier LABEL carrying no value itself."""
    return isinstance(cell, str) and _label_ends(cell)


def _mask_id_value(cell):
    """Mask every identifier-shaped run in a cell a label GOVERNS.

    An integer is masked here and nowhere else. `mask_ssn` leaves a numeric
    cell alone because an SSN typed as a number carries neither delimiter nor
    label — under a label it carries one, and a workbook that stores the nine
    bare digits in its SSN column is exactly the case that rationale excluded.
    The masked value comes back as a string, because it is an identifier and
    not a number; a governed cell that holds no identifier shape keeps its own
    type, so an amount in the column below a label is returned untouched.
    """
    if isinstance(cell, bool):
        return cell
    if isinstance(cell, int):
        text = str(cell)
    elif isinstance(cell, str):
        text = cell
    else:
        return cell
    masked = _mask_spaced(_ID_VALUE_RE.sub(r"XXX-XX-\1",
                                           _ID_DELIMITED.sub(r"XXX-XX-\1", text)))
    return masked if masked != text else cell


def mask_table(rows):
    """`mask_ssn` over every cell, plus the labels only the TABLE can see.

    Rows of cells in, rows of cells out — every extraction route that returns
    a table (`extract_csv`, `extract_excel`, `extract_tables`) goes through
    this rather than through `mask_ssn` cell by cell. A row that is not a
    sequence is masked as a value, so a malformed table cannot slip past
    untouched.
    """
    masked = []
    governed: set = set()
    for row in rows:
        if not isinstance(row, (list, tuple)):
            masked.append(mask_ssn(row))
            continue
        out = [mask_ssn(cell) for cell in row]
        # The columns a PREVIOUS row's label governs, before this row's own
        # labels are read: a header governs what is under it, never itself.
        for column in governed:
            if column < len(out):
                out[column] = _mask_id_value(out[column])
        for column, cell in enumerate(row):
            if _is_id_label_cell(cell):
                governed.add(column)
                if column + 1 < len(out):
                    out[column + 1] = _mask_id_value(out[column + 1])
        masked.append(out)
    return masked


def names_an_identifier(name: str) -> bool:
    """True when a FILENAME carries an SSN or ITIN in any spelling.

    Wider than `mask_ssn`, and deliberately. In a page of text nine bare
    digits is a routing number as often as an SSN, which is why that spelling
    is masked only under a label; a filename has no label and no line above it,
    and `scripts/pre-commit-pii-scan.sh` runs its widened set against a
    PATHNAME "before any exemption" for the same reason. A file named after an
    identifier discloses it whatever it holds, so the caller's answer is to
    refuse the file and say to rename it — not to hand back a masked name,
    which is a path that opens nothing and which two different documents can
    collide on.
    """
    return bool(_ID_DELIMITED.search(name) or _ID_VALUE_RE.search(name)
                or _ID_SPACED.search(name))

# The window a bare integer may be PRINTED in. Not a validity check — every
# caller has its own accepted range and applies it after this — but the bound
# on what a refusal is allowed to disclose. Deliberately wider than any
# caller's range (tax_calendar accepts 2000-2099): narrower than a caller and a
# legitimate mismatch loses the one fact the message exists to convey, wider
# and the window stops meaning "a year".
_MIN_PRINTABLE_YEAR = 1900
_MAX_PRINTABLE_YEAR = 2199


def year_or_shape(value) -> str:
    """A year field rendered for an error message: the YEAR when the value is
    one, otherwise its SHAPE and nothing whatever of its content.

    `taxpayer_profile.json` and `projection_inputs.json` are hand-edited, so a
    misplaced value — a name, an address, an entire nested object — reaches a
    year comparison exactly as readily as a wrong year does, and every one of
    those refusals is printed to the terminal. `redact` above is not the
    backstop for it: by design it masks SSN and credential SHAPES, and a
    taxpayer's name has neither (AGENTS.md #1's never-display-a-sensitive-value
    rule). So the value prints only once it is known to be a year, and anything
    else is described the way `projection._canonical_status` describes an
    unrecognized filing status: domain and length, never content.

    INTEGER-ness is not year-ness, and the first version of this tested the
    wrong one of the two. `isinstance(value, int)` was taken as proof the value
    "cannot carry anything sensitive" — but the field is hand-edited, and an
    account or policy number lands in it as an int exactly as a name lands in
    it as a str. `redact` covers only the nine-digit tail of that (an SSN
    typed into the field is masked by `_NINE_DIGITS`); an eight-digit account
    number is shorter than every pattern it knows and printed in full. So the
    window above, not the type, is what earns the print.

    bool before int, because `isinstance(True, int)` is True and "TYTrue" is
    the one shape that would read like a year while not being one.
    """
    if isinstance(value, bool):
        return "a bool"
    if isinstance(value, int):
        if _MIN_PRINTABLE_YEAR <= value <= _MAX_PRINTABLE_YEAR:
            return f"TY{value}"
        return f"an int ({len(str(abs(value)))} digits)"
    if isinstance(value, str):
        return f"a string ({len(value)} chars)"
    return f"a {type(value).__name__}"


def _without_the_raw_path(e: OSError) -> OSError:
    """Rebuild an OSError with its operand path dropped and its text redacted.

    The CLASS is preserved: parse_utils' and archive.py's tests distinguish a
    PermissionError from the rest, and collapsing every inspection failure to a
    bare OSError would erase which one a caller is looking at.

    The path is dropped by CONSTRUCTION rather than by clearing `.filename` on
    the original. OSError.__str__ tests that member for NULL, not for None, so
    assigning None to it renders as a literal `: None` beside the message —
    an object still claiming a filename, now a wrong one. Rebuilding from
    (errno, strerror) never had one to begin with. `winerror` does not survive
    the rebuild; nothing here reads it.
    """
    detail = redact(e.strerror) if e.strerror else redact(str(e))
    try:
        if e.errno is None:
            return type(e)(detail)
        return type(e)(e.errno, detail)
    except Exception:
        # A subclass whose __init__ takes something else entirely. Nothing
        # os.lstat raises is one, but the base class is the single answer that
        # cannot itself fail, and losing the class is a far smaller loss than
        # letting the unredacted original through.
        return OSError(detail)


class PathRefused(OSError):
    """The path could not be certified as a real path inside the workspace.

    Distinct from every other OSError secure_path can raise (a missing file, a
    permission error on the chmod) because it says something about the TREE,
    not about one operation on it: a component is a symlink or junction, or
    could not be inspected at all. Every path beneath that component is equally
    uncertain, which is why the CLI stops on this one and only this one.

    A subclass of OSError, so the library callers that catch OSError and
    translate to their own error type keep working unchanged.
    """


def _normcase(part: str) -> str:
    """Fold the spellings the platform treats as ONE path — comparison only.

    Windows resolves paths case-insensitively and accepts either separator, so
    `c:/Work/archive` and `C:\\Work\\archive` name the same directory. A raw
    string comparison sees two different strings, concludes the path is not
    under the current directory, and drops to inspecting only its leaf — the
    ancestor check below turned off by nothing more than a spelling, on the one
    platform whose junctions it exists for. POSIX is case-SENSITIVE and treats a
    backslash as an ordinary filename character, so this is identity there.

    The fold is applied to the containment DECISION and never to a component
    handed to lstat or chmod: every inspected path keeps the spelling the caller
    wrote. Keyed on os.name rather than delegating to os.path.normcase (whose
    body this matches on Windows) so the branch is reachable in a test that
    simulates the platform — os.path is bound to posixpath at import and does
    not move when os.name is patched.
    """
    return part.replace("/", "\\").lower() if os.name == "nt" else part


def _split_path(path_str: str) -> list[str]:
    """Split on every separator the PLATFORM honours, keeping parts as written.

    Keyed on os.name for the same reason as _normcase: os.altsep is the more
    obvious test and is exactly equivalent on both real platforms, but it does
    not move under a simulated one, so the Windows branch would be unreachable
    from CI.
    """
    if os.name == "nt":
        return re.split(r"[\\/]+", path_str)
    return path_str.split(os.sep)


def _parts_below(path_str: str, cwd: str) -> list[str] | None:
    """`path_str`'s named parts BELOW `cwd`, or None if it does not lie under it.

    Compared part-by-part under _normcase rather than as one string prefix, and
    the parts are what makes it safe: `.lower()` is not length-preserving for
    every Unicode character, so a normalized prefix's length cannot be used to
    slice the original string. `.` parts are dropped from both sides — they name
    no component and `/a/./b` is `/a/b` on every platform — while `..` is KEPT
    and accumulated as written, because the kernel resolves it only after
    traversing what precedes it, which is the component being inspected.
    """
    mine = [p for p in _split_path(path_str) if p and p != os.curdir]
    theirs = [p for p in _split_path(cwd) if p and p != os.curdir]
    if len(mine) < len(theirs):
        return None
    if any(_normcase(a) != _normcase(b) for a, b in zip(mine, theirs)):
        return None
    return mine[len(theirs):]


def _named_components(path_str: str) -> list[str]:
    """Every path prefix secure_path() must inspect, OUTERMOST first.

    os.lstat() declines to follow only the FINAL component; every ancestor is
    resolved before it is even reached. A leaf-only check therefore certifies
    `archive/{year}/as_filed` as a real directory while `archive` is a symlink
    or junction, and the chmod / Set-Acl that follows lands on whatever
    `archive` points at — outside the workspace, past every gitignore and PII
    rule scoped to it. /tax-amend reaches that directly: it passes `archive`,
    `archive/{year}` and `archive/{year}/as_filed` to the CLI in one call, and
    pipes `find archive/{year}/as_filed` into a second one, where every path
    `find` yields has a real leaf and a linked ancestor.

    SCOPE is the components the CALLER NAMED, never the ones above them. A
    relative path — what every skill passes, run from the workspace root — is
    every component, checked from the current directory down. An absolute path
    is treated the same way when it lies under the current directory — a
    question decided under _normcase, so a Windows caller cannot switch the
    walk off by writing `c:/Work/...` where the process reports `C:\\Work` —
    otherwise only its leaf is checked, because this module cannot know where
    that tree's trust boundary is and a walk to the filesystem root would
    refuse correct data: macOS puts every temporary directory under `/var`,
    which IS a symlink, so an anchor-down walk refuses to secure a perfectly
    legitimate workspace — and a gate that fires on correct data teaches the
    caller to route around it. The library callers that pass absolute paths
    resolve their root first (archive.py), which leaves no reparse point above
    it, and check the components they append themselves.

    Built with os.path string operations rather than pathlib on purpose. The
    Windows branch below is exercised by patching os.name to "nt", and os.name
    is a SHARED singleton — pathlib re-reads it and starts handing back
    WindowsPath, which raises UnsupportedOperation the moment one is joined on
    a POSIX host. os.path is bound to posixpath at import and does not move.
    That immobility is also why the two platform DECISIONS above (_normcase and
    _split_path) read os.name themselves instead of os.path.normcase and
    os.altsep: those are equivalent on both real platforms and inert under a
    simulated one, which would leave the Windows half of this walk untested.
    """
    base = ""
    parts = _split_path(path_str)
    if os.path.isabs(path_str):
        try:
            cwd = os.getcwd()
        except OSError:
            return [path_str]
        # _parts_below rather than os.path.relpath(): relpath NORMALIZES, and
        # collapsing `link/../x` to `x` would drop the very component being
        # inspected. Each part is accumulated as WRITTEN.
        below = _parts_below(path_str, cwd)
        if below is None:
            return [path_str]
        base, parts = cwd, below
    components, current = [], base
    for part in parts:
        if not part or part == os.curdir:
            continue
        current = os.path.join(current, part) if current else part
        components.append(current)
    # A path that IS the current directory has no named components; check it.
    return components or [path_str]


def secure_path(path) -> None:
    """Restrict an existing file or directory to the owner only.

    POSIX: 0o700 for a directory, 0o600 for a file (matches every existing
    call site's split), via os.chmod. Windows has no equivalent
    read/write/execute bit split, so both get the same "owner: full
    control, everyone else: nothing" treatment there — the security
    property this codebase actually needs is "no other local account can
    read or list this," not a bit-for-bit chmod translation.

    RESIDUAL, stated rather than implied: the component inspection below and
    the chmod that follows it are two syscalls, and os.chmod follows symlinks
    with no portable way to ask it not to. A local attacker who can write into
    the workspace could swap a component between the two and be acted on. That
    is not closed here, and the reason is scope rather than oversight — closing
    it needs an O_NOFOLLOW openat chain per component with no Windows
    equivalent, against a threat (another local account already writing inside
    a single-user tax workspace) that has already defeated the property this
    module exists to establish. secure_fd() is the descriptor-based path for
    callers that just created the file and can avoid the race entirely; every
    library writer here uses it.
    """
    path_str = os.fspath(path)
    # Refuse to act THROUGH a symlink or NTFS junction, at ANY component. Every
    # library caller already refuses a linked leaf before reaching here, but the
    # skill-facing CLI has no such gate of its own: /tax-setup's shell guard is
    # `[ -L summaries ]`, which is FALSE for a junction — the exact case
    # is_reparse_point() was added for — so without this check a junctioned
    # summaries/ would have the protected DACL (or the chmod) applied to
    # whatever it points at, and /tax-parse would then write the taxpayer's
    # summaries outside the workspace. See _named_components for why an
    # ancestor is as reachable as the leaf, and for the scope.
    components = _named_components(path_str)
    for component in components:
        try:
            linked = is_reparse_point(component)
        except OSError as e:
            # is_reparse_point() fails CLOSED — every OSError but
            # FileNotFoundError propagates rather than certifying an
            # uninspected path as safe — and lstat's own message carries the
            # RAW path, so the failure is translated here rather than allowed
            # to escape the module boundary unredacted.
            raise PathRefused(
                f"Could not determine whether {redact(component)} is a symlink "
                f"or junction: {redact(e)} — refusing to change the permissions "
                f"of {redact(path_str)} rather than act on a path that could not "
                f"be inspected.") from None
        if not linked:
            continue
        if component == components[-1]:
            raise PathRefused(
                f"{redact(path_str)} is a symlink or junction — refusing to change "
                f"its permissions, which would act on whatever it points to instead "
                f"of on the workspace path.")
        raise PathRefused(
            f"{redact(path_str)} lies under {redact(component)}, which is a symlink "
            f"or junction — refusing to change its permissions, which would act on "
            f"whatever that component points to instead of on the workspace path.")
    if os.name == "nt":
        _secure_path_windows(path_str)
    else:
        # os.chmod's own OSError stringifies with the RAW filename in it, and
        # archive.py/tie_out.py embed that text verbatim in the ArchiveError /
        # TieOutError they print. Redacting only at this module's print site
        # would leave every library path leaking, so the guarantee is made at
        # the module BOUNDARY instead: no OSError leaves secure_fs carrying an
        # unredacted path. errno is dropped, which no caller reads — all of them
        # catch OSError and translate to their own error type.
        try:
            os.chmod(path_str, 0o700 if os.path.isdir(path_str) else 0o600)
        except OSError as e:
            raise OSError(
                f"Could not restrict {redact(path_str)} to the owner only: "
                f"{redact(e)}") from None



# THE ONE WRITER (V10). Five modules had hand-rolled a temp-then-rename and two
# more wrote in PLACE — `scenario.py` and `tie_out.py` opened the real target
# with O_TRUNC, so an interrupted write left a truncated scenario or a truncated
# tie-out report, and the second is the hard gate's own record, the artifact V8
# binds a filing package to.
#
# Every property the copies had earned separately is kept here, once:
#   - created at 0600 BEFORE the first byte, not written-then-chmod'd, with
#     `secure_fd` on the descriptor so Windows gets its ACL at the same moment;
#   - a unique temp name in the TARGET's directory, so the rename is on one
#     filesystem and two runs cannot collide on it;
#   - `os.replace`, which is atomic on POSIX and carries the mode across;
#   - the temp removed on ANY failure, so a half-written file never survives
#     under a name something else will read.
@contextlib.contextmanager
def atomic_path(target):
    """A secured temp path beside `target`, renamed onto it on a clean exit.

    For a writer that takes a PATH and opens it itself — reportlab is the case
    that needs this shape. Yields the temp path; on any exception it is removed
    and nothing replaces the target.
    """
    target = Path(target)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent),
                               prefix=f".{target.name}.", suffix=".tmp")
    try:
        # INSIDE the cleanup scope: securing the descriptor can fail — Windows
        # ACL enforcement shells out, and that can fail like anything else — and
        # a failure there used to leave the temp on disk to accumulate over
        # retries. The descriptor is closed either way; the file is removed by
        # the handler below.
        try:
            secure_fd(fd, tmp)
        finally:
            os.close(fd)
        yield Path(tmp)
        # ...and again after the caller has written it: a writer that recreated
        # the path (reportlab replaces the file it was handed) would otherwise
        # publish whatever the umask gave it.
        secure_path(Path(tmp))
        os.replace(tmp, str(target))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class PublishedButNotSecured(OSError):
    """Every target was published; restricting one of them then failed.

    A DISTINCT type because the two outcomes need different words. Everything
    else `atomic_paths` raises means the targets are untouched, and the caller
    says so; this one means the content landed and the mode did not, which a
    caller reporting "no changes applied" would be lying about — the operator
    re-runs the apply, or re-derives from a workspace they believe is unchanged.
    Measured, not imagined: it was that message, over a fully published pair.
    """


class HalfApplied(OSError):
    """A publish failed AND the restore of an earlier target failed with it.

    The state the transaction exists to prevent, arrived at anyway, and the one
    thing worse than reaching it is reaching it silently. The restore's own error
    is chained; the caller must say the workspace is half-applied rather than
    that nothing happened.
    """


def _secure_temp_beside(target: Path) -> Path:
    """A fresh 0600 temp in `target`'s directory, secured before the first byte.

    One home for the prologue both writers share — a file created at the umask
    default holds its contents before anything tightens it, and on Windows has no
    ACL until the call returns. Securing the DESCRIPTOR is what closes that, and
    doing it in two places is how the one-writer premise comes apart.
    """
    fd, tmp = tempfile.mkstemp(dir=str(target.parent),
                               prefix=f".{target.name}.", suffix=".tmp")
    try:
        secure_fd(fd, tmp)
    finally:
        os.close(fd)
    return Path(tmp)


@contextlib.contextmanager
def atomic_paths(*targets):
    """Secured temp paths beside each target, published TOGETHER on a clean exit.

    The shape `atomic_path` cannot express: files that must land as one change or
    not at all. `rollover.apply_changes` is the case — a half-applied pair is a
    workspace whose profile says one thing and whose return says another, with
    nothing to tell a reader which.

    Yields one temp path per target, in order. On a clean exit each is renamed
    onto its target, in order. **If a later rename fails, the earlier targets are
    restored** by renaming back a hardlink taken before the first rename. The
    hardlink is not a crash defence — see the residual — it is there because the
    restore is then ONE RENAME rather than a re-write of remembered bytes, which
    costs nothing for a file of any size and cannot half-succeed. The version
    this replaces restored with `write_bytes`, which can.

    THE RESIDUAL, because POSIX has no multi-file atomic rename and pretending
    otherwise is worse than naming it: between the first rename and the last
    there is a window in which the workspace is half-published. An EXCEPTION in
    that window is handled — that is the restore — and a restore that ITSELF
    fails raises `HalfApplied` rather than passing for "nothing happened". A
    CRASH is not handled: a power cut or a SIGKILL in the window leaves the pair
    half-applied and no handler runs. Nothing in user space closes that, and the
    honest mitigation is not to be in the window: a caller that publishes only
    the documents it actually changed is usually publishing ONE, and one rename
    has no window at all.

    Nor is there an `fsync` here or in `atomic_path`: the rename is ordered
    against the writes on the filesystems this runs on, but a crash immediately
    after a publish can still leave a short file on one that does not. That is
    the same bound every other writer in this module has.
    """
    targets = [Path(t) for t in targets]
    if not targets:
        raise ValueError("atomic_paths needs at least one target")
    # Two names for one file publish twice onto it and the second silently wins,
    # which is a lost write wearing the shape of a completed transaction.
    resolved = [os.path.realpath(t) for t in targets]
    if len(set(resolved)) != len(resolved):
        raise ValueError(
            f"atomic_paths was given the same target twice: "
            f"{redact(sorted({p for p in resolved if resolved.count(p) > 1}))}")

    temps: list = [None] * len(targets)
    backups: list = [None] * len(targets)
    try:
        for i, target in enumerate(targets):
            temps[i] = _secure_temp_beside(target)
        yield tuple(temps)

        # A BACKUP per target that already exists, taken before the first rename
        # so a failure part-way through has something atomic to restore from. Its
        # NAME is the temp's plus a suffix — already unique, already in the right
        # directory — rather than a second `mkstemp` whose reserve-then-unlink
        # would open a window for something else to take the name.
        for i, target in enumerate(targets):
            if not target.exists():
                continue
            backup = Path(str(temps[i]) + ".bak")
            try:
                os.link(str(target), backup)
                backups[i] = backup
            except OSError as e:
                # NARROW: only "this filesystem cannot hardlink". EEXIST would
                # mean something took the name, and answering that with a copy
                # overwrites it. EINVAL/ENOSYS are how Windows reports it on
                # FAT/exFAT, which is a workspace on a USB stick.
                if e.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.EXDEV,
                                   errno.EMLINK, errno.EINVAL, errno.ENOSYS):
                    raise
                # Registered BEFORE the copy, and written through a secured
                # descriptor rather than `copy2`: that opens at the umask default
                # and only reaches 0600 when `copystat` finishes, so a failure
                # anywhere in it used to orphan a world-readable — sometimes
                # partial — copy of `taxpayer_profile.json` that nothing removed.
                backups[i] = backup
                with open(str(target), "rb") as src:
                    fd = os.open(str(backup),
                                 os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    try:
                        secure_fd(fd, str(backup))
                        with os.fdopen(fd, "wb") as dst:
                            fd = None
                            shutil.copyfileobj(src, dst)
                    finally:
                        if fd is not None:
                            os.close(fd)

        published = 0
        try:
            for i, (temp, target) in enumerate(zip(temps, targets)):
                secure_path(temp)
                os.replace(str(temp), str(target))
                # DROPPED from the cleanup list as it is consumed: the rename
                # frees the temp NAME, so the unlink below would either be a
                # harmless miss or, once that name is re-issued, somebody else's
                # file.
                temps[i] = None
                published += 1
        except BaseException as publish_error:
            failed = []
            for i in range(published):
                try:
                    if backups[i] is None:
                        os.unlink(str(targets[i]))
                    else:
                        os.replace(str(backups[i]), str(targets[i]))
                        backups[i] = None
                except OSError as restore_error:
                    failed.append(f"{redact(targets[i])}: {redact(restore_error)}")
            if failed:
                raise HalfApplied(
                    "publish failed and the rollback did not complete — the "
                    "workspace is HALF APPLIED and must not be trusted: "
                    + "; ".join(failed)) from publish_error
            raise

        # WINDOWS ONLY, and inside the guarded region on purpose. On POSIX
        # `os.replace` carries the temp's 0600 onto the name it lands on, so the
        # target is already owner-only and this loop only masks the call that
        # made it so. On Windows the ACL follows the NAME, so it is load-bearing
        # — and a failure there means content published at a mode §1 forbids,
        # which is a distinct outcome from "nothing happened" and says so.
        if os.name == "nt":  # pragma: no cover - POSIX CI
            for target in targets:
                try:
                    secure_path(target)
                except OSError as e:
                    raise PublishedButNotSecured(
                        f"published, but could not restrict {redact(target)}: "
                        f"{redact(e)}") from e
    finally:
        for path in [*temps, *backups]:
            if path is not None:
                with contextlib.suppress(OSError):
                    os.unlink(str(path))


@contextlib.contextmanager
def atomic_write(target, *, binary: bool = False, encoding: str = "utf-8"):
    """An open file object whose contents replace `target` on a clean exit."""
    with atomic_path(target) as tmp:
        if binary:
            with open(tmp, "wb") as handle:
                yield handle
        else:
            with open(tmp, "w", encoding=encoding, newline="") as handle:
                yield handle


def write_secure(target, data) -> Path:
    """Replace `target` with `data` (str or bytes), atomically and owner-only."""
    with atomic_write(target, binary=isinstance(data, (bytes, bytearray))) as handle:
        handle.write(data)
    return Path(target)

def secure_fd(fd: int, path) -> None:
    """Restrict a just-opened file descriptor's file to owner-only access.

    Prefers fchmod where the platform has it — immune to the path being
    replaced between open() and the chmod call, unlike a path-based call.
    Windows has neither fchmod nor an fd-based ACL equivalent exposed
    here, so it falls back to the path-based secure_path(): the same
    narrow open-to-secure race the pre-existing os.fchmod-missing
    fallback already carried on that platform, now closing with a real
    protected DACL instead of a near-no-op chmod.
    """
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)
    else:
        secure_path(path)


# FILE_ATTRIBUTE_REPARSE_POINT does NOT mean "this path redirects somewhere".
# Windows hangs a whole family of filter-driver features off the same bit:
# OneDrive Files On-Demand placeholders (IO_REPARSE_TAG_CLOUD*), Data
# Deduplication (DEDUP), the Windows Overlay Filter that compresses installed
# application files (WOF), container image layers (WCI). Every one of those is
# an ORDINARY file whose contents the filesystem produces on demand at the path
# you asked for. Refusing on the bare bit therefore refused normal documents on
# any OneDrive-backed workspace — secure_path, parse_utils and archive_year all
# reject a reparse point outright, so parse/archive/setup would stop with
# "symlink or junction" on a file that is neither.
#
# The tag is what carries the meaning, and Windows encodes the distinction in
# the tag itself: bit 0x20000000 is the NAME SURROGATE flag, documented as "the
# reparse point is a surrogate for another named entity". That is exactly the
# property this module cares about — the name you inspected is not the object
# you would open — and it is set on symlinks (0xA000000C), junctions /
# mount points (0xA0000003), WSL's LX symlinks (0xA000001D) and
# GLOBAL_REPARSE (0xA0000019) alike, INCLUDING tags that do not exist yet.
# Testing the flag rather than an allowlist of known tags is what keeps this
# fail-closed against a redirecting tag added after this was written. It is
# equally what keeps the benign families out of the refusal without having to
# enumerate them: the cloud, dedup, overlay, container and projected-filesystem
# tags all leave bit 29 clear, as does IO_REPARSE_TAG_NFS — which represents an
# NFS special file (socket, fifo, device), not a name substitution.
_NAME_SURROGATE = 0x20000000
# APPEXECLINK (a Store app execution alias) redirects to another executable
# without setting the surrogate flag. Nothing in a tax workspace is one, so
# naming it costs nothing and refusing it keeps "the leaf is what it says it is"
# true. stat exposes IO_REPARSE_TAG_* only on Windows — Modules/_stat.c guards
# them with MS_WINDOWS — so the values are spelled out for the simulated-platform
# tests, and getattr prefers the platform's own definition where there is one.
_REDIRECTING_REPARSE_TAGS = frozenset((
    getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
    getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
    getattr(stat, "IO_REPARSE_TAG_APPEXECLINK", 0x8000001B),
))


def _reparse_tag_redirects(tag: int) -> bool:
    """True when a Windows reparse tag makes the name stand for another object.

    A tag of 0 means the attribute said "reparse point" and the tag could not be
    read — os.stat_result.st_reparse_tag is Windows-and-3.8+, and an older or
    stubbed stat result simply has no such member. That is an UNINSPECTED path,
    not a certified-benign one, so it refuses: the whole point of this check is
    that a caller is about to follow the path.
    """
    if not tag:
        return True
    return bool(tag & _NAME_SURROGATE) or tag in _REDIRECTING_REPARSE_TAGS


def is_reparse_point(path) -> bool:
    """True for a POSIX symlink, or a REDIRECTING Windows reparse point — a
    symlink OR an NTFS junction. See the module docstring for why a symlink-only
    check (Path.is_symlink()) is not enough on Windows, and
    _reparse_tag_redirects above for why the reparse ATTRIBUTE alone is too
    much: it is also set on ordinary OneDrive/dedup/compressed files, which this
    must not refuse.

    Fails CLOSED on an inspection error: only FileNotFoundError — the path
    genuinely does not exist, so it cannot be a reparse point — is treated as
    False. Every other OSError (a transient I/O error, access denied on a
    junctioned target) is NOT converted to False; certifying an uninspected
    path as "safe" here would let a caller's is_dir()/mkdir()/exists() follow
    it right past the check that exists to stop that.

    The propagated error is REBUILT without the raw path. os.lstat's OSError
    carries the operand in `.filename`, and this is a public entry point of the
    module, not an internal helper: parse_utils._validate_file_path,
    parse_utils.scan_workspace and tax_law_fetcher.load_tax_law all call it
    OUTSIDE any translating try, and the first two run under a CLI that catches
    nothing, so an unaccessible document prints its own name in a traceback —
    a taxpayer's scanned-document name, and an SSN in the absolute case
    (AGENTS.md §1). secure_path() already promised at its own chmod that no
    OSError leaves this module carrying an unredacted path; making the promise
    here instead is what makes it true of every caller rather than of one.
    """
    # Handed to os.lstat as GIVEN. It accepts str and Path alike, so the wrap
    # in Path() this used to do bought nothing and cost the module its one
    # remaining pathlib hazard: Path() dispatches to WindowsPath/PosixPath on
    # os.name AT CONSTRUCTION TIME, so on a process whose os.name does not
    # match the filesystem underneath it — every test that simulates Windows —
    # a plain string arrived here as a WindowsPath and reached lstat rendered
    # with backslashes, which no POSIX filesystem has. The path was then
    # reported ABSENT, is_reparse_point returned False, and the caller was
    # certified safe by a lookup that never found anything. Do not reintroduce
    # the wrap; nothing below needs a Path.
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as e:
        raise _without_the_raw_path(e) from None
    if stat.S_ISLNK(st.st_mode):
        return True
    if os.name != "nt":
        return False
    if not getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    return _reparse_tag_redirects(getattr(st, "st_reparse_tag", 0))


def _powershell_quote(s: str) -> str:
    """Single-quote a string for a PowerShell literal. Single quotes are
    the only special character inside a single-quoted PowerShell string
    (no variable interpolation, no other escapes to worry about) —
    doubled per PowerShell's own escaping rule."""
    return "'" + s.replace("'", "''") + "'"


def _secure_path_windows(path: str) -> None:
    # SetAccessRuleProtection($true, $false) blocks inheritance and does NOT
    # copy the inherited rules it is blocking — the explicit RemoveAccessRule
    # loop then clears whatever explicit rules the path already had (a prior
    # owner, a share-level "Everyone" grant, anything left over from a move
    # out of a shared location), so the AddAccessRule call below is adding
    # the ONLY rule left on the object, not layering onto survivors the way
    # `icacls /grant:r` would have.
    #
    # A DIRECTORY's single ACE must be INHERITABLE
    # (ContainerInherit|ObjectInherit); the 3-argument FileSystemAccessRule
    # constructor means InheritanceFlags.None, which breaks both directions at
    # once. Downward: a protected DACL with no inheritable ACE strips the
    # inherited ACEs off entries that already live inside the directory, and a
    # summaries/*.md whose only ACEs were inherited is left with an EMPTY DACL
    # — its owner locked out of their own parse summaries. Forward: anything
    # created inside afterwards inherits nothing and falls back to the creating
    # token's default DACL, which normally grants SYSTEM and Administrators —
    # so "owner-only" would hold for the directory and for nothing written into
    # it, where POSIX 0700 denies traversal and therefore covers every child.
    # A FILE takes the 3-argument form: inheritance flags on a leaf are invalid
    # and throw. Changing the DACL alone is not sufficient: an object's owner
    # can replace its DACL later, so the protected owner-only ACL must also take
    # ownership away from any prior identity and verify that the change stuck.
    #
    # The DACL and the owner are written by SEPARATE Set-Acl calls, restriction
    # first. Set-Acl persists every modified section in one SetSecurityInfo, so
    # folding SetOwner into the same object makes the two all-or-nothing: on a
    # path owned by another identity where this process holds WRITE_DAC but not
    # WRITE_OWNER — exactly the "left over from a move out of a shared location"
    # case above — the combined call throws and NO restriction is applied at
    # all. Split, the restriction lands and the ownership failure is reported
    # against it. The order matters in the other direction too: taking ownership
    # first and then failing to restrict would leave the path owned but wide
    # open, whereas restricting first and then failing to take ownership leaves
    # it closed to everyone but a prior owner who could still revert it.
    #
    # Verification re-reads what LANDED, and checks the DACL rather than only
    # the owner: a descriptor can come back with the owner set correctly and the
    # access rules unchanged (a filter driver, a redirected or network path),
    # and an owner-only check would return success while every other local
    # account still reads the file.
    quoted_path = _powershell_quote(path)
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"$item = Get-Item -LiteralPath {quoted_path} -Force; "
        "$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User; "
        "$acl = $item.GetAccessControl(); "
        "$acl.SetAccessRuleProtection($true, $false); "
        "foreach ($rule in @($acl.Access)) { [void]$acl.RemoveAccessRule($rule) }; "
        "if ($item.PSIsContainer) { "
        "$rule = New-Object System.Security.AccessControl.FileSystemAccessRule("
        "$identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow') "
        "} else { "
        "$rule = New-Object System.Security.AccessControl.FileSystemAccessRule("
        "$identity, 'FullControl', 'Allow') "
        "}; "
        "$acl.AddAccessRule($rule); "
        "$item.SetAccessControl($acl); $item.Refresh(); "
        "$applied = $item.GetAccessControl(); "
        "if (-not $applied.AreAccessRulesProtected) { "
        "throw 'DACL did not take: inherited grants from the parent still apply' "
        "}; "
        "$explicit = @($applied.GetAccessRules($true, $false, "
        "[System.Security.Principal.SecurityIdentifier])); "
        "if ($explicit.Count -ne 1) { "
        "throw \"DACL did not take: expected 1 access rule, found $($explicit.Count)\" "
        "}; "
        "if ($explicit[0].IdentityReference.Value -ne $identity.Value "
        "-or $explicit[0].AccessControlType -ne 'Allow' "
        "-or -not $explicit[0].FileSystemRights.HasFlag("
        "[System.Security.AccessControl.FileSystemRights]::FullControl)) { "
        "throw 'DACL did not take: the single rule is not Allow/FullControl for this identity' "
        "}; "
        "if ($item.PSIsContainer -and "
        "\"$($explicit[0].InheritanceFlags)\" -ne 'ContainerInherit, ObjectInherit') { "
        "throw 'DACL did not take: the directory rule is not inheritable, so entries "
        "created inside it would not be owner-only' "
        "}; "
        "$owned = $item.GetAccessControl(); "
        "$owned.SetOwner($identity); "
        "$item.SetAccessControl($owned); $item.Refresh(); "
        "$final = $item.GetAccessControl(); "
        "$actualOwner = $final.GetOwner("
        "[System.Security.Principal.SecurityIdentifier]); "
        "if ($null -eq $actualOwner) { "
        "throw 'Owner-only DACL is applied, but the object owner could not be read back' "
        "}; "
        "if ($actualOwner.Value -ne $identity.Value) { "
        "throw 'Owner-only DACL is applied, but ownership could not be transferred to "
        "the current identity, which leaves a prior owner able to replace it' "
        "}"
    )
    try:
        result = subprocess.run(
            # `-ExecutionPolicy Bypass` because a restrictive policy is one of
            # the two things that can stop this script; the OTHER one is why the
            # cmdlets above are gone. `Get-Acl`/`Set-Acl` live in
            # `Microsoft.PowerShell.Security`, and on a stock windows-latest
            # runner autoloading it fails outright —
            # `CouldNotAutoloadMatchingModule`, "the module could not be
            # loaded" — which took out EVERY call: on that host nothing this
            # toolkit writes could be made owner-only, which is AGENTS.md §1 not
            # holding on a whole supported platform. Bypassing the policy alone
            # did NOT fix it, measured on the runner.
            #
            # So the ACL is read and written through `GetAccessControl()` /
            # `SetAccessControl()` on the `FileSystemInfo` this script already
            # holds. Those are System.IO METHODS, present in the .NET Framework
            # that Windows PowerShell runs on, and they need no module to be
            # discovered, autoloaded, or permitted. The security semantics are
            # identical — the same `FileSecurity` object, the same protected
            # DACL — the DEPENDENCY is what changes.
            #
            # The bypass widens nothing and stays: the policy governs SCRIPTS,
            # and the argument here is a string this module builds itself, so
            # bypassing it for a command the caller already fully controls
            # grants no capability the process lacked. `-NoProfile` still keeps
            # a user profile out of it, which is the setting that would matter.
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise OSError(
            f"Could not run PowerShell to secure {redact(path)}: {redact(e)}"
        ) from None
    if result.returncode != 0:
        # PowerShell quotes the offending path back in its own error text, so
        # the DETAIL is a second copy of the thing being protected, not just a
        # reason — it is redacted on the same terms as the path itself.
        detail = redact(result.stderr.strip() or result.stdout.strip())
        raise OSError(
            f"Could not restrict {redact(path)} to the current user only: {detail}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # A RELATIVE target means a file in the WORKSPACE. Every skill spells
        # these as bare names — `secure_fs taxpayer_profile.json` — and that was
        # the same directory the command ran from until $TAX_WORKSPACE existed
        # (Y4). Left alone, the documented call would have chmod'ed a stale copy
        # in the checkout, or failed, while the real profile kept whatever mode
        # it was created with: the one file AGENTS.md §1 is most explicit about.
        # Imported here rather than at module scope — `workspace` imports this
        # module, so a top-level import back would be a cycle.
        from agents.lib.workspace import WorkspaceError, resolve_workspace
        try:
            root = resolve_workspace()
        except WorkspaceError as e:
            print(f"Could not resolve the workspace: {redact(e)}", file=sys.stderr)
            sys.exit(1)
        targets = [t if os.path.isabs(t) else str(root / t) for t in sys.argv[1:]]
        exit_code = 0
        for i, target in enumerate(targets):
            try:
                secure_path(target)
            except PathRefused as e:
                # STOP, rather than carry on down the list. The callers pass a
                # tree outermost-first — /tax-amend passes `archive`,
                # `archive/{year}` and `archive/{year}/as_filed` in one call —
                # so a REFUSAL says the paths after it are not what they appear
                # to be. Continuing reported the refusal and then secured the
                # descendants THROUGH the refused component. The count is
                # printed, never the remaining names: those are a taxpayer's
                # document names.
                print(f"Could not secure {redact(target)}: {redact(e)}",
                      file=sys.stderr)
                remaining = len(targets) - i - 1
                if remaining:
                    print(f"Stopped: {remaining} later path(s) not attempted — "
                          f"a refusal here means the paths under it would be "
                          f"secured through whatever it points to.",
                          file=sys.stderr)
                sys.exit(1)
            except OSError as e:
                # An ordinary failure — the file is gone, the chmod was denied —
                # says nothing about the OTHER targets, and stopping there would
                # leave siblings at their default mode over an unrelated error.
                # A glob that matched nothing is the common case: `summaries/*.md
                # result.json` passes the unexpanded pattern through, and
                # `result.json` still has to be secured.
                # Both halves are redacted, not just the target: on POSIX the
                # raw filename also arrives inside the exception text.
                print(f"Could not secure {redact(target)}: {redact(e)}",
                      file=sys.stderr)
                exit_code = 1
        sys.exit(exit_code)

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, "secret.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("x")
        secure_path(f)

        d = os.path.join(td, "sub")
        os.mkdir(d)
        secure_path(d)

        f2 = os.path.join(td, "secret2.txt")
        fd = os.open(f2, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            secure_fd(fh.fileno(), f2)
            fh.write("x")

        assert not is_reparse_point(f), "plain file misdetected as a reparse point"

        if os.name != "nt":
            assert oct(os.stat(f).st_mode & 0o777) == "0o600", "file mode not tightened"
            assert oct(os.stat(d).st_mode & 0o777) == "0o700", "dir mode not tightened"
            assert oct(os.stat(f2).st_mode & 0o777) == "0o600", "fd-secured file mode not tightened"
            link = os.path.join(td, "link")
            os.symlink(f, link)
            assert is_reparse_point(link), "symlink not detected as a reparse point"

    # year_or_shape: a real year prints, everything else is shape-only. The
    # negative half is the load-bearing one — a regression that starts echoing
    # the value looks identical from the positive case alone.
    assert year_or_shape(2025) == "TY2025"
    assert year_or_shape(True) == "a bool"
    assert year_or_shape(87654321) == "an int (8 digits)", \
        "an out-of-window integer is printed in full"
    for hidden in ("Jane Q Taxpayer", {"name": "Jane"}, ["2025"], 2025.0, None):
        shape = year_or_shape(hidden)
        assert "Jane" not in shape and "2025" not in shape, \
            f"year_or_shape leaked content of {type(hidden).__name__}: {shape}"

    print("secure_fs self-test OK")
