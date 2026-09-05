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
   Get-Acl/Set-Acl on Windows to build a PROTECTED DACL holding a single
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

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

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
_SSN_DELIMITED = re.compile(
    r"[0-9]{3}(-[0-9]{2}-|\.[0-9]{2}\.|/[0-9]{2}/|\s+[0-9]{2}\s+)[0-9]{4}")
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
        f"$acl = Get-Acl -LiteralPath {quoted_path}; "
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
        f"Set-Acl -LiteralPath {quoted_path} -AclObject $acl; "
        f"$applied = Get-Acl -LiteralPath {quoted_path}; "
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
        f"$owned = Get-Acl -LiteralPath {quoted_path}; "
        "$owned.SetOwner($identity); "
        f"Set-Acl -LiteralPath {quoted_path} -AclObject $owned; "
        f"$final = Get-Acl -LiteralPath {quoted_path}; "
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
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
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
        targets = sys.argv[1:]
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
        with open(f, "w") as fh:
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
