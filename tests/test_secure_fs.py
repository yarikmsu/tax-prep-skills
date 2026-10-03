#!/usr/bin/env python3
"""Tests for agents/lib/secure_fs.py — owner-only permissions, POSIX and Windows.

The Windows branch can't be exercised on the real filesystem from a macOS/Linux
CI runner (there's no PowerShell/NTFS here), so those tests monkeypatch os.name
to "nt" and stub subprocess.run to verify the *script* secure_path builds, not
its effect on a real ACL — the same boundary the codebase already accepts by
not having Windows CI.
"""

import os
import errno
import shutil
import subprocess
import sys
import re
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agents.lib.secure_fs import (
    HalfApplied, atomic_paths, is_reparse_point, redact, secure_fd,
    secure_path, year_or_shape, _MIN_PRINTABLE_YEAR, _MAX_PRINTABLE_YEAR,
)
from tests.engine_tree import GUARD as ENGINE_GUARD, engine_files, engine_relpath

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")

# Hook input declarations: (priority, path patterns). Lower priority wins.
# Parsed as literals by the selector; this module is never imported by it.
WATCHES = (
    (10, ('.agents/skills', '.agents/skills/*', '.claude/skills', '.claude/skills/*')),
    (60, ('scripts/who_it_fits.py',)),
    (130, ('scripts/pre-commit-pii-scan.sh',)),
    (140, ('agents/lib/capabilities.py', 'agents/lib/rollover.py')),
    (150, ('agents/lib/tax_law_fetcher.py',)),
    (155, ('agents/lib/tie_out.py',)),
    (160, ('agents/lib/ptet.py',)),
    (170, ('agents/lib/tax_computations', 'agents/lib/tax_computations/*')),
    (190, ('agents/lib/secure_fs.py',)),
    (200, ('agents', 'scripts')),
    (210, ('agents/lib',)),
    (230, ('agents/lib/*.sh',)),
    (240, ('agents/lib/*',)),
    (250, ('tests/conftest.py',)),
    (270, ('tests/test_secure_fs.py',)),
    (340, ('tests/engine_tree.py',)),
    (370, ('agents/install.sh',)),
    (460, ('agents/*.py', 'agents/*.sh', 'scripts/*.py', 'scripts/*.sh')),
)

# POSIX MODE BITS, and only those. Each of these reads back a `0o600`/`0o700`
# that NTFS does not have, creates a symlink Windows needs a privilege for,
# or depends on a case-sensitive filesystem. Skipping them on Windows is not
# a narrowing of the windows job until it passes: what secure_fs actually
# does there — set a restrictive DACL — is asserted by
# `TestTheWindowsAclIsActuallyRestrictive`, which runs ONLY on Windows and
# is the reason the job is worth having.
posix_only = pytest.mark.skipif(
    os.name == "nt",
    reason="asserts POSIX mode bits, a symlink, or a case-sensitive path")


SENSITIVE_SKILL_SECURITY_CALLS = {
    "tax-setup": (
        'mkdir -p "$d" && sh agents/lib/run.sh -m agents.lib.secure_fs "$d" || exit 1',
        "-m agents.lib.secure_fs taxpayer_profile.json",
        "-m agents.lib.secure_fs result.json answers.md",
    ),
    "tax-parse": (
        "-m agents.lib.secure_fs 'summaries/{group_name}.md'",
        # answers.md is in the sweep because Step 5.4 rewrites it by hand; the
        # sweep listed only what the sub-tasks and the library had produced.
        "-m agents.lib.secure_fs summaries/*.md result.json answers.md",
        # parse_ledger.json is NOT in this list any more, and its absence is the
        # point: V2 stopped the agent writing that file. `parse_records` derives
        # it from the per-document records and secures it as it writes, which is
        # AGENTS.md §1's "`agents/lib/` writers call it internally". The skill
        # must therefore name the BUILDER — a skill that still called secure_fs
        # on a file it no longer writes would pass this test while doing nothing.
        # That the builder really does secure it is
        # `TestTheLedgerIsDerivedAndNotTranscribed::test_the_ledger_is_owner_only`.
        "-m agents.lib.parse_records --build-ledger",
    ),
    "tax-amend": (
        "-m agents.lib.secure_fs archive archive/{year} "
        "archive/{year}/as_filed || exit 1",
        "xargs -0 sh agents/lib/run.sh -m agents.lib.secure_fs || exit 1",
        # Three separate answers.md writes (Steps 1, 2.5 and 5) had no
        # securing step between them, while the archive copies two lines above
        # did.
        "-m agents.lib.secure_fs answers.md || exit 1",
    ),
    "tax-generate": (
        "-m agents.lib.secure_fs Tax_Return_*_Summary.pdf",
        "-m agents.lib.secure_fs Tax_Return_{year}_Filing_Package.pdf",
    ),
    "tax-review": (
        "-m agents.lib.secure_fs answers.md",
        "mkdir -p reviews && sh agents/lib/run.sh -m agents.lib.secure_fs reviews",
        "-m agents.lib.secure_fs reviews/{year}-review.md",
    ),
    "tax-project": (
        "-m agents.lib.secure_fs projection_inputs.json",
        "mkdir -p projections && sh agents/lib/run.sh -m agents.lib.secure_fs projections",
        "-m agents.lib.secure_fs projections/TY{year}_asof_{MM-DD}.md",
        "-m agents.lib.secure_fs answers.md",
    ),
    "tax-withholding": (
        "-m agents.lib.secure_fs projection_inputs.json",
        "mkdir -p withholding && sh agents/lib/run.sh -m agents.lib.secure_fs withholding",
        "-m agents.lib.secure_fs withholding/TY{year}_asof_{MM-DD}.md",
        "-m agents.lib.secure_fs answers.md",
    ),
    "tax-fbar": (
        "-m agents.lib.secure_fs taxpayer_profile.json answers.md",
        "-m agents.lib.secure_fs FBAR_{year}_Preparation.md",
        "-m agents.lib.secure_fs result.json",
    ),
    "tax-parse-crypto": (
        "-m agents.lib.secure_fs 'summaries/crypto_<exchange>.json'",
        "-m agents.lib.secure_fs crypto_transactions.json",
        "-m agents.lib.secure_fs result.json taxpayer_profile.json crypto_transactions.json",
        "-m agents.lib.secure_fs parse_ledger.json",
    ),
    "tax-scenario": (
        "-m agents.lib.secure_fs scenarios/{name}.md",
        "-m agents.lib.secure_fs answers.md",
    ),
    "tax-harvest": (
        "-m agents.lib.secure_fs holdings.json",
        "-m agents.lib.secure_fs scenarios/{name}.md",
        # The audit-note append had only the blanket "restrict every manually
        # written file" line at the bottom of the skill's Security section
        # covering it. A rule three screens away from the step it governs is
        # the shape of instruction that gets followed unevenly, which is the
        # entire reason this pin is per-CALL rather than per-skill.
        "-m agents.lib.secure_fs answers.md",
    ),
    # The three skills below wrote taxpayer data with NO securing step anywhere
    # in them — not a weak one, none — while SECURITY.md claimed every
    # documented toolkit write went through secure_fs. An in-place edit does
    # preserve the mode, which is why it went unnoticed; an editor that writes
    # a replacement and renames it over the target does not, and owner-only is
    # not inherited from the file being overwritten.
    "tax-extend": (
        "-m agents.lib.secure_fs taxpayer_profile.json answers.md || exit 1",
    ),
    "tax-verify": (
        "-m agents.lib.secure_fs answers.md || exit 1",
    ),
    "tax-rollover": (
        "-m agents.lib.secure_fs answers.md || exit 1",
    ),
    "tax-roth-convert": (
        "-m agents.lib.secure_fs scenarios/{name}.md",
        "-m agents.lib.secure_fs answers.md",
    ),
    # Lives in references/finalize.md, not SKILL.md — which is exactly why the
    # check below reads a skill's references/ too. tax-compute writes the two
    # most sensitive artifacts in the toolkit from a progressive-disclosure file
    # and had no securing step at all while a SKILL.md-only scan reported green.
    "tax-compute": (
        "-m agents.lib.secure_fs result.json answers.md || exit 1",
        # Step 0.7's blocked branch writes both files and can END the run —
        # the user declines to proceed, or the re-run still blocks — so
        # finalize.md's securing step is not reached on the path where a
        # taxpayer's acknowledgment has just been written down.
        "-m agents.lib.secure_fs answers.md taxpayer_profile.json || exit 1",
    ),
}

# The other half of the pin above: a skill that writes nothing sensitive needs
# no securing call, and saying WHICH ones those are is what makes the absence
# of an entry a decision instead of an oversight. Every tax-* skill must be in
# one collection or the other, so a new one cannot be added without answering
# the question. SECURITY.md's "every documented toolkit write of financial or
# identity data is passed through agents.lib.secure_fs" is exactly this claim,
# and it was false for four skills at once while every existing per-call
# assertion here reported green.
SKILLS_THAT_WRITE_NOTHING_SENSITIVE = {
    # Advisory only — states outright that it writes no artifact.
    "tax-ptet": "read-only dashboard; writes no artifact",
    "tax-retirement": "read-only dashboard; writes no artifact",
    # Both write through a library entry point, and agents/lib/ writers call
    # secure_fs internally (AGENTS.md §1). The skill itself writes nothing by
    # hand, so a skill-level call would restrict a path it never created.
    "tax-calendar": "tax_calendar.write_calendar secures the .ics it writes",
    "tax-archive": "archive.archive_year secures what it moves",
    # Advisory, and the only skill with no workspace at all: it asks for two
    # figures off a filed return, requests no identity data by instruction, and
    # writes no file. There is nothing on disk for a securing call to restrict.
    "tax-safe-harbor": "engine-free advisory; no workspace and no artifact",
}

# A line that mentions chmod is a VIOLATION unless it is prose explaining why
# chmod is the wrong tool. The two readings are not separable by backticks —
# the one bare-chmod instruction that shipped ("`chmod -R 600` the files") was
# inside them — so the discriminator is the explanatory phrasing itself, kept
# line-local: a justification one line above does not license the invocation.
_CHMOD_EXPLANATORY_MARKERS = (
    "not a bare",
    "bare `chmod`",
    "only flips",
    "cross-platform helper",
)


def _bare_chmod_offenders(text):
    """Lines that INVOKE chmod rather than explain why it is the wrong tool.

    Deliberately matches the bare word, not `chmod 600`/`chmod 700`: the real
    offender was `chmod -R 600`, and `chmod 0600`, `chmod u=rw,go=`,
    `xargs chmod 600` and a markdown list item are all the same defect wearing
    different syntax. A gate that only recognises the two forms someone thought
    of cannot fail on the one they didn't.
    """
    offenders = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if "chmod" not in line:
            continue
        if any(marker in line for marker in _CHMOD_EXPLANATORY_MARKERS):
            continue
        offenders.append(f"{lineno}: {line.strip()}")
    return offenders


def _skill_documents(skill_dir):
    """SKILL.md plus every references/*.md. A skill is a DIRECTORY: scanning
    only the body misses the progressive-disclosure files where the larger
    skills do their actual writing."""
    docs = [skill_dir / "SKILL.md"]
    docs.extend(sorted((skill_dir / "references").glob("*.md")))
    return [d for d in docs if d.is_file()]


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.mark.parametrize(
    ("skill_name", "required_calls"),
    SENSITIVE_SKILL_SECURITY_CALLS.items(),
)
def test_sensitive_skill_writes_use_cross_platform_security(skill_name, required_calls):
    """A documented direct write must not regress to a POSIX-only chmod that
    leaves an inherited Windows DACL readable by other local identities."""
    skill_dir = Path(REPO_ROOT, ".agents", "skills", skill_name)
    text = "\n".join(d.read_text(encoding="utf-8") for d in _skill_documents(skill_dir))
    launcher = "sh agents/lib/run.sh"
    for required_call in required_calls:
        if required_call.startswith("-m agents.lib.secure_fs"):
            assert f"{launcher} {required_call}" in text, (
                f"{skill_name} does not route {required_call} through the "
                f"launcher — a bare interpreter there runs the HOST python on "
                f"a Windows venv and leaves the path world-listable")
            continue
        # Every pin is checked to route through the LAUNCHER, not merely to
        # name the module. Narrowing them to `-m agents.lib.secure_fs` alone
        # made this module — which exists to pin the Windows-safe securing path
        # — unable to tell `sh agents/lib/run.sh -m …` from a bare
        # `python3 -m …`, which is the regression it is here to catch. The
        # interpreter half is also covered by
        # test_preflight.py::test_no_skill_names_an_interpreter_itself; this is
        # the per-call half, and neither alone is the invariant.
        #
        # The pins themselves start at `-m agents.lib.secure_fs`, deliberately
        # excluding whatever precedes it: the skills name `sh agents/lib/run.sh`
        # and the launcher resolves the interpreter one layer down, as
        # `${TAXPY:-python3}`, so a Windows venv's `python.exe` is reached.
        # Pinning a literal interpreter here made every invocation-form change
        # break twenty assertions about permissions, which are not what changed.
        assert required_call in text, f"{skill_name} does not secure {required_call}"


def test_every_tax_skill_is_classified_as_writing_or_not_writing_pii():
    """The per-call pin above can only fail on a skill someone listed.

    /tax-extend, /tax-verify and /tax-rollover each wrote `answers.md` (and
    /tax-extend the profile too) with no securing step anywhere in the file,
    and none of the three was in the table, so the table was green. A missing
    ENTRY is invisible in a way a missing CALL is not — which is why the
    classification is total: every tax-* skill is either pinned to the calls it
    must make, or named here with the reason it needs none.
    """
    skill_root = Path(REPO_ROOT, ".agents", "skills")
    shipped = {d.name for d in skill_root.glob("tax-*") if d.is_dir()}
    classified = set(SENSITIVE_SKILL_SECURITY_CALLS) | set(
        SKILLS_THAT_WRITE_NOTHING_SENSITIVE)
    assert not shipped - classified, (
        "tax skill(s) neither pinned to a secure_fs call nor declared free of "
        "sensitive writes: " + ", ".join(sorted(shipped - classified)))
    assert not classified - shipped, (
        "classified skill(s) that no longer exist: "
        + ", ".join(sorted(classified - shipped)))
    assert not (set(SENSITIVE_SKILL_SECURITY_CALLS)
                & set(SKILLS_THAT_WRITE_NOTHING_SENSITIVE)), \
        "a skill cannot both require and not require a securing call"


def test_a_skill_declared_free_of_sensitive_writes_makes_no_securing_call():
    """The read-only list is a CLAIM, and the claim is checkable.

    A skill that turns out to need `secure_fs` after all must move to the pin
    above rather than sit in the exemption with a call nobody asserts, where a
    later edit could drop the call and nothing would notice.
    """
    for skill_name in sorted(SKILLS_THAT_WRITE_NOTHING_SENSITIVE):
        skill_dir = Path(REPO_ROOT, ".agents", "skills", skill_name)
        for doc in _skill_documents(skill_dir):
            for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
                assert "-m agents.lib.secure_fs" not in line, (
                    f"{skill_name} is declared free of sensitive writes but "
                    f"invokes secure_fs at {doc.name}:{lineno} — move it to "
                    f"SENSITIVE_SKILL_SECURITY_CALLS")


def test_tax_skills_have_no_bare_permission_commands():
    """Library prose may explain chmod's portability gap, but no tax skill may
    execute chmod directly for a taxpayer runtime artifact — in SKILL.md or in
    the references/ files it defers its steps to.

    Scoped to tax-* on purpose: /codex-loop's `chmod 600 "$STATE_FILE"` is
    contributor tooling writing review-round state under .git/, not a taxpayer
    artifact, and test_portability pins it deliberately.
    """
    skill_root = Path(REPO_ROOT, ".agents", "skills")
    offenders = []
    for skill_dir in sorted(skill_root.glob("tax-*")):
        for doc in _skill_documents(skill_dir):
            rel = doc.relative_to(skill_root)
            offenders.extend(f"{rel}:{o}" for o in _bare_chmod_offenders(doc.read_text(encoding="utf-8")))
    assert not offenders, "bare POSIX-only permission command(s): " + "; ".join(offenders)


def test_bare_chmod_detector_catches_every_shipped_and_plausible_form():
    """The gate above is only worth having if it can FAIL on live data.

    Its first version matched a line starting with `chmod 600`/`chmod 700` or
    containing `&& chmod 600/700`, and passed green while
    `.agents/skills/tax-amend/SKILL.md` carried "`chmod -R 600` the files
    (`700` the dirs)." — the single real offender in the tree. Each string
    below must be flagged, and each real prose line must not be.
    """
    must_flag = (
        "`chmod -R 600` the files (`700` the dirs).",
        "chmod 600 result.json",
        "  chmod 700 summaries",
        "chmod 0600 answers.md",
        "chmod u=rw,go= taxpayer_profile.json",
        "mkdir -p reviews; chmod 700 reviews",
        "find archive -type f | xargs chmod 600",
        "- `chmod -R 700` everything under `archive/`",
    )
    for line in must_flag:
        assert _bare_chmod_offenders(line), f"detector misses a bare chmod: {line!r}"

    must_not_flag = (
        "every copy — `secure_fs`, not a bare `chmod -R`: under a POSIX-compatible shell",
        "on Windows `chmod` only flips the read-only DOS attribute and leaves the whole",
        "Step 5. Uses the same cross-platform helper as Step 3, not a bare `chmod`, for the",
        "bare `chmod` follows it to whatever it points at (possibly outside the",
        "-m agents.lib.secure_fs result.json answers.md || exit 1",
    )
    for line in must_not_flag:
        assert not _bare_chmod_offenders(line), f"detector misreads prose as a command: {line!r}"


@posix_only
def test_secure_path_file_posix(tmp_path):
    f = tmp_path / "secret.json"
    f.write_text("{}", encoding="utf-8")
    os.chmod(f, 0o644)
    secure_path(f)
    assert oct(os.stat(f).st_mode & 0o777) == "0o600"


@posix_only
def test_secure_path_dir_posix(tmp_path):
    d = tmp_path / "scenarios"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    secure_path(d)
    assert oct(os.stat(d).st_mode & 0o777) == "0o700"


@posix_only
def test_secure_fd_prefers_fchmod_on_posix(tmp_path):
    f = tmp_path / "result.json"
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        secure_fd(fd, f)
    finally:
        os.close(fd)
    assert oct(os.stat(f).st_mode & 0o777) == "0o600"


@posix_only
def test_secure_fd_falls_back_to_secure_path_without_fchmod(tmp_path, monkeypatch):
    """Windows has no os.fchmod — simulate that by hiding the attribute and
    confirm secure_fd degrades to the path-based (ACL-backed) call rather
    than crashing on a missing function."""
    f = tmp_path / "result.json"
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    monkeypatch.delattr("agents.lib.secure_fs.os.fchmod", raising=False)
    try:
        secure_fd(fd, f)
    finally:
        os.close(fd)
    assert oct(os.stat(f).st_mode & 0o777) == "0o600"


def test_is_reparse_point_true_for_symlink(tmp_path):
    target = tmp_path / "real.txt"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    assert is_reparse_point(link)


def test_is_reparse_point_false_for_plain_file(tmp_path):
    f = tmp_path / "plain.txt"
    f.write_text("x", encoding="utf-8")
    assert not is_reparse_point(f)


def test_is_reparse_point_false_for_plain_directory(tmp_path):
    d = tmp_path / "plain_dir"
    d.mkdir()
    assert not is_reparse_point(d)


def test_is_reparse_point_detects_windows_junction_via_file_attributes(tmp_path, monkeypatch):
    """A junction is a Windows reparse point that is_symlink() cannot see —
    it carries FILE_ATTRIBUTE_REPARSE_POINT but a different reparse tag than
    a symlink. Simulated here since junctions don't exist on POSIX."""
    p = tmp_path / "junction_like"
    p.write_text("x", encoding="utf-8")

    # is_reparse_point() reads st_mode (for stat.S_ISLNK) before it reads
    # st_file_attributes, so the fake lstat result must carry a REAL st_mode —
    # os is a singleton module, and patching os.lstat replaces it for the whole
    # function. Only the attributes this test cares about are added on top.
    real_lstat = os.lstat

    def fake_lstat(path):
        real = real_lstat(path)

        class _Wrapped:
            st_mode = real.st_mode
            st_file_attributes = 0x400  # stat.FILE_ATTRIBUTE_REPARSE_POINT
            st_reparse_tag = IO_REPARSE_TAG_MOUNT_POINT

        return _Wrapped()

    monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")
    monkeypatch.setattr("agents.lib.secure_fs.os.lstat", fake_lstat)
    assert is_reparse_point(p)


FILE_ATTRIBUTE_REPARSE_POINT = 0x400
# Values from Microsoft's reparse-tag registry. `stat` exposes IO_REPARSE_TAG_*
# only on Windows, so they are spelled out rather than imported — which is also
# what lets these run on the POSIX CI box.
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003   # junction
IO_REPARSE_TAG_SYMLINK = 0xA000000C
IO_REPARSE_TAG_LX_SYMLINK = 0xA000001D    # WSL — surrogate, not in any list here
IO_REPARSE_TAG_APPEXECLINK = 0x8000001B   # redirects WITHOUT the surrogate flag
IO_REPARSE_TAG_CLOUD = 0x9000001A         # OneDrive Files On-Demand
IO_REPARSE_TAG_CLOUD_9 = 0x9000901A       # …one of its sixteen sibling tags
IO_REPARSE_TAG_DEDUP = 0x80000013         # Data Deduplication
IO_REPARSE_TAG_WOF = 0x80000017           # compressed application files


def _lstat_reporting(tag, monkeypatch, attributes=FILE_ATTRIBUTE_REPARSE_POINT):
    """Patch os.lstat to report a Windows reparse point carrying `tag`."""
    real_lstat = os.lstat

    def fake_lstat(path):
        real = real_lstat(path)

        class _Wrapped:
            st_mode = real.st_mode
            st_file_attributes = attributes
            st_reparse_tag = tag

        return _Wrapped()

    monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")
    monkeypatch.setattr("agents.lib.secure_fs.os.lstat", fake_lstat)


class TestOnlyARedirectingReparseTagIsRefused:
    """FILE_ATTRIBUTE_REPARSE_POINT does not mean "this path redirects".

    Windows hangs a family of filter-driver features off the same bit —
    OneDrive Files On-Demand, Data Deduplication, the Windows Overlay Filter
    that compresses installed application files, container image layers. Each is
    an ORDINARY file whose contents the filesystem produces on demand at the
    path you asked for. Refusing on the bare bit refused normal documents on any
    OneDrive-backed workspace, and secure_path, parse_utils and archive_year all
    treat a reparse point as fatal, so /tax-parse, /tax-archive and /tax-setup
    would stop with "symlink or junction" on a file that is neither.

    The refusal is therefore keyed on the TAG. It is not an allowlist of the two
    tags known to redirect: Windows marks every name-substituting tag with the
    0x20000000 NAME SURROGATE flag, so testing the flag refuses redirecting tags
    that did not exist when this was written — the fail-closed direction — while
    letting the benign families through.
    """

    @pytest.mark.parametrize("tag", [
        IO_REPARSE_TAG_MOUNT_POINT,
        IO_REPARSE_TAG_SYMLINK,
        IO_REPARSE_TAG_LX_SYMLINK,
        IO_REPARSE_TAG_APPEXECLINK,
    ])
    def test_a_redirecting_tag_is_refused(self, tag, tmp_path, monkeypatch):
        p = tmp_path / "redirects"
        p.write_text("x", encoding="utf-8")
        _lstat_reporting(tag, monkeypatch)
        assert is_reparse_point(p), f"tag {tag:#x} redirects and must be refused"

    @pytest.mark.parametrize("tag", [
        IO_REPARSE_TAG_CLOUD,
        IO_REPARSE_TAG_CLOUD_9,
        IO_REPARSE_TAG_DEDUP,
        IO_REPARSE_TAG_WOF,
    ])
    def test_a_non_redirecting_tag_is_an_ordinary_file(self, tag, tmp_path, monkeypatch):
        p = tmp_path / "W2_Acme.pdf"
        p.write_text("x", encoding="utf-8")
        _lstat_reporting(tag, monkeypatch)
        assert not is_reparse_point(p), (
            f"tag {tag:#x} names an ordinary file the filesystem hydrates in "
            "place — refusing it blocks the parse/archive flows on a normal "
            "OneDrive-backed workspace")

    def test_an_unreadable_tag_still_fails_closed(self, tmp_path, monkeypatch):
        """st_reparse_tag is Windows-and-3.8+; a stat result without it leaves
        the tag at 0. The attribute said "reparse point" and the tag could not
        be read, which is an UNINSPECTED path, not a certified-benign one."""
        p = tmp_path / "unknown"
        p.write_text("x", encoding="utf-8")
        _lstat_reporting(0, monkeypatch)
        assert is_reparse_point(p)

    def test_the_tag_is_only_consulted_when_the_attribute_is_set(
            self, tmp_path, monkeypatch):
        """A stale tag member on a file with no reparse attribute means nothing.
        The attribute gates the question; the tag answers it."""
        p = tmp_path / "plain"
        p.write_text("x", encoding="utf-8")
        _lstat_reporting(IO_REPARSE_TAG_SYMLINK, monkeypatch, attributes=0)
        assert not is_reparse_point(p)

    def test_secure_path_secures_a_cloud_backed_file_instead_of_refusing_it(
            self, tmp_path, monkeypatch):
        """The end-to-end consequence, which is the reported one: an ordinary
        document in a OneDrive workspace must get its owner-only ACL, not a
        PathRefused that stops the whole run."""
        p = tmp_path / "W2_Acme.pdf"
        p.write_text("x", encoding="utf-8")
        _lstat_reporting(IO_REPARSE_TAG_CLOUD, monkeypatch)
        calls = []
        monkeypatch.setattr(
            "agents.lib.secure_fs.subprocess.run",
            lambda cmd, **kwargs: calls.append(cmd) or _FakeCompletedProcess(
                returncode=0),
        )
        secure_path(p)
        assert calls, "the file was never secured"

    def test_secure_path_still_refuses_a_junction(self, tmp_path, monkeypatch):
        """The paired assertion that keeps the fix above narrow."""
        p = tmp_path / "archive"
        p.write_text("x", encoding="utf-8")
        _lstat_reporting(IO_REPARSE_TAG_MOUNT_POINT, monkeypatch)
        monkeypatch.setattr(
            "agents.lib.secure_fs.subprocess.run",
            lambda cmd, **kwargs: pytest.fail("secured a junction"),
        )
        with pytest.raises(OSError, match="symlink or junction"):
            secure_path(p)


@posix_only
def test_is_reparse_point_ignores_reparse_attribute_on_posix(tmp_path, monkeypatch):
    """The same st_file_attributes bit must be ignored off Windows — POSIX
    os.stat_result has no such attribute at all, and os.name gates the check
    before it's ever read."""
    p = tmp_path / "plain2.txt"
    p.write_text("x", encoding="utf-8")
    real_lstat = os.lstat

    def fake_lstat(path):
        real = real_lstat(path)

        class _Wrapped:
            st_mode = real.st_mode
            st_file_attributes = 0x400

        return _Wrapped()

    monkeypatch.setattr("agents.lib.secure_fs.os.lstat", fake_lstat)
    assert not is_reparse_point(p)


def test_is_reparse_point_false_on_windows_file_not_found(tmp_path, monkeypatch):
    """A genuinely absent path cannot be a reparse point — the only OSError
    is_reparse_point is allowed to swallow."""
    monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")

    def raising_lstat(path):
        raise FileNotFoundError("simulated missing path")

    monkeypatch.setattr("agents.lib.secure_fs.os.lstat", raising_lstat)
    p = tmp_path / "missing.txt"
    assert not is_reparse_point(p)


def test_is_reparse_point_false_on_posix_file_not_found(tmp_path, monkeypatch):
    """A missing POSIX path gets the same narrow exception as Windows."""

    def raising_lstat(path):
        raise FileNotFoundError("simulated missing path")

    monkeypatch.setattr("agents.lib.secure_fs.os.lstat", raising_lstat)
    assert not is_reparse_point(tmp_path / "missing-posix.txt")


def test_is_reparse_point_fails_closed_on_posix_lstat_errors(tmp_path, monkeypatch):
    """Path.is_symlink() swallows OSError on Python 3.14, so POSIX must use
    lstat directly and propagate every inspection error except not-found."""

    def raising_lstat(path):
        raise PermissionError("simulated access denied")

    monkeypatch.setattr("agents.lib.secure_fs.os.lstat", raising_lstat)
    with pytest.raises(PermissionError):
        is_reparse_point(tmp_path / "unreadable-posix.txt")


def test_is_reparse_point_fails_closed_on_other_windows_lstat_errors(tmp_path, monkeypatch):
    """A transient or access-denied lstat failure must NOT be certified as
    "not a reparse point" — that would let a caller's exists()/mkdir() follow
    an uninspected junction straight past the check meant to stop it."""
    monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")

    def raising_lstat(path):
        raise PermissionError("simulated access denied")

    monkeypatch.setattr("agents.lib.secure_fs.os.lstat", raising_lstat)
    p = tmp_path / "unreadable.txt"
    with pytest.raises(PermissionError):
        is_reparse_point(p)


def test_secure_path_windows_builds_protected_dacl_with_single_identity(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr("agents.lib.secure_fs.subprocess.run", fake_run)
    f = tmp_path / "result.json"
    f.write_text("{}", encoding="utf-8")
    secure_path(f)
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "powershell.exe"
    script = cmd[-1]
    # Protects from inheritance without copying existing (inherited) rules...
    assert "SetAccessRuleProtection($true, $false)" in script
    # ...then clears every remaining EXPLICIT rule the path already had...
    assert "RemoveAccessRule($rule)" in script
    # ...before adding exactly one rule, for the actual running identity
    # (not a spoofable %USERNAME% string) rather than layering onto whatever
    # survived, which is what a plain icacls /grant:r would have done.
    assert "WindowsIdentity]::GetCurrent().User" in script
    assert "AddAccessRule($rule)" in script
    # The WRITE, whatever spells it. `Set-Acl` was a cmdlet from
    # `Microsoft.PowerShell.Security`, which a stock windows-latest runner
    # could not autoload at all; it is `SetAccessControl` on the FileSystemInfo
    # now, which is a System.IO method needing no module.
    assert "SetAccessControl" in script
    # The DACL is written FIRST and by its own Set-Acl, then ownership by a
    # second one. Set-Acl persists every modified section in a single call, so
    # a combined object makes the two all-or-nothing: on a path where the
    # process holds WRITE_DAC but not WRITE_OWNER the call throws and NO
    # restriction lands, where split it lands and only the ownership transfer
    # is reported as failed. Restriction-before-ownership, not the reverse:
    # taking ownership first and then failing to restrict leaves the path
    # owned and wide open.
    assert script.index("SetAccessControl") < script.index("$owned.SetOwner($identity)")
    assert script.count("SetAccessControl") >= 2, (
        "the DACL and the owner are not separate writes — folded into one they\n"
        "become all-or-nothing, and on a path owned by another identity where\n"
        "this process holds WRITE_DAC but not WRITE_OWNER no restriction lands")
    # Verification re-reads what LANDED, and covers the DACL — not just the
    # owner. A descriptor can come back owned correctly with its access rules
    # untouched (a filter driver, a redirected or network path), and an
    # owner-only check would call that success while every other local account
    # still reads the file.
    assert "$applied.AreAccessRulesProtected" in script
    assert "$explicit.Count -ne 1" in script
    assert "$explicit[0].IdentityReference.Value -ne $identity.Value" in script
    assert "FileSystemRights]::FullControl" in script
    assert "[System.Security.Principal.SecurityIdentifier]" in script
    assert "$final.GetOwner(" in script
    assert "$actualOwner.Value -ne $identity.Value" in script
    # A GetOwner() that cannot translate returns $null; reporting that as
    # "owner does not match" would misdescribe the condition.
    assert "$null -eq $actualOwner" in script
    assert script.index("$applied.AreAccessRulesProtected") < script.index("$final.GetOwner(")
    assert str(f) in script


def test_secure_path_windows_quotes_single_quotes_in_path(tmp_path, monkeypatch):
    """A path containing a literal single quote must not break out of the
    PowerShell single-quoted string literal it's embedded in."""
    monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")
    calls = []
    monkeypatch.setattr(
        "agents.lib.secure_fs.subprocess.run",
        lambda cmd, **kwargs: calls.append(cmd) or _FakeCompletedProcess(returncode=0),
    )
    secure_path("C:\\taxpayer's docs\\result.json")
    script = calls[0][-1]
    assert "C:\\taxpayer''s docs\\result.json" in script


def test_secure_path_windows_raises_on_acl_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")
    monkeypatch.setattr(
        "agents.lib.secure_fs.subprocess.run",
        lambda cmd, **kwargs: _FakeCompletedProcess(returncode=1, stderr="Access is denied."),
    )
    f = tmp_path / "result.json"
    f.write_text("{}", encoding="utf-8")
    with pytest.raises(OSError, match="Access is denied"):
        secure_path(f)


def test_secure_path_windows_raises_on_subprocess_error(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")

    def raising_run(cmd, **kwargs):
        raise FileNotFoundError("powershell.exe not found")

    monkeypatch.setattr("agents.lib.secure_fs.subprocess.run", raising_run)
    f = tmp_path / "result.json"
    f.write_text("{}", encoding="utf-8")
    with pytest.raises(OSError, match="PowerShell"):
        secure_path(f)


def test_secure_path_windows_raises_on_subprocess_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")

    def timing_out_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

    monkeypatch.setattr("agents.lib.secure_fs.subprocess.run", timing_out_run)
    f = tmp_path / "result.json"
    f.write_text("{}", encoding="utf-8")
    with pytest.raises(OSError):
        secure_path(f)


@posix_only
def test_cli_secures_each_given_path(tmp_path):
    f1 = tmp_path / "a.json"
    f1.write_text("{}", encoding="utf-8")
    d1 = tmp_path / "sub"
    d1.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "agents.lib.secure_fs", str(f1), str(d1)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert oct(os.stat(f1).st_mode & 0o777) == "0o600"
    assert oct(os.stat(d1).st_mode & 0o777) == "0o700"


def test_cli_exits_nonzero_and_reports_failure(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    result = subprocess.run(
        [sys.executable, "-m", "agents.lib.secure_fs", str(missing)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Could not secure" in result.stderr


@posix_only
def test_secure_path_refuses_a_symlink(tmp_path):
    """secure_path must not act THROUGH a link. The library callers each
    refuse one before reaching here, but the skill-facing CLI has no gate of
    its own — /tax-setup's shell guard is `[ -L summaries ]`, which is false
    for an NTFS junction, the exact case is_reparse_point() exists for."""
    external = tmp_path / "external"
    external.mkdir(mode=0o755)
    os.chmod(external, 0o755)
    link = tmp_path / "summaries"
    link.symlink_to(external)
    with pytest.raises(OSError, match="symlink or junction"):
        secure_path(link)
    assert oct(os.stat(external).st_mode & 0o777) == "0o755", \
        "secure_path followed the link and re-permissioned an external directory"


def test_cli_refuses_a_symlinked_path(tmp_path):
    """The refusal reaches the skill layer: a non-zero exit is what
    /tax-setup's block treats as a refusal to proceed."""
    external = tmp_path / "external"
    external.mkdir()
    link = tmp_path / "summaries"
    link.symlink_to(external)
    result = subprocess.run(
        [sys.executable, "-m", "agents.lib.secure_fs", str(link)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "symlink or junction" in result.stderr


def test_secure_path_windows_directory_ace_is_inheritable(tmp_path, monkeypatch):
    """A directory's single ACE must carry ContainerInherit|ObjectInherit. The
    3-argument FileSystemAccessRule constructor means InheritanceFlags.None,
    which breaks both directions: entries already inside lose the inherited
    ACEs that were their only grant, and anything created afterwards inherits
    nothing and falls back to the token default DACL (SYSTEM + Administrators)
    — "owner-only" for the directory and for nothing written into it, where
    POSIX 0700 denies traversal and so covers every child."""
    monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")
    calls = []
    monkeypatch.setattr(
        "agents.lib.secure_fs.subprocess.run",
        lambda cmd, **kwargs: calls.append(cmd) or _FakeCompletedProcess(returncode=0),
    )
    d = tmp_path / "summaries"
    d.mkdir()
    secure_path(d)
    script = calls[0][-1]
    assert "PSIsContainer" in script, "the script does not branch on container vs leaf"
    assert "'ContainerInherit,ObjectInherit', 'None', 'Allow'" in script, \
        "a directory's ACE is not inheritable — children are left uncovered"
    # A leaf still takes the 3-argument form; inheritance flags on a file throw.
    assert "$identity, 'FullControl', 'Allow'" in script


class TestFailureMessagesRedactTheirTarget:
    """A runtime PATH is sensitive data, not just a diagnostic string.

    AGENTS.md §1 restricts the runtime DIRECTORIES precisely because an
    owner-only file inside a listable one still discloses its NAME, and forbids
    emitting a full SSN under any circumstance. Every failure this module
    reports names the path it failed on; the skill layer invokes the CLI
    directly and `tax-amend` pipes `find archive/{year}/as_filed` into it, so an
    unredacted message writes a taxpayer's document names — or an SSN in a
    filename — into terminal scrollback and into whatever an agent forwards.
    """

    # Every SSN spelling scripts/pre-commit-pii-scan.sh blocks. The two
    # implementations cannot share code (that gate is bash, this is a
    # dependency-free runtime library), so the duplication is pinned here: a
    # form the commit-time gate refuses must not survive the runtime redactor.
    SSN_FORMS = (
        "000-00-0000",
        "000.00.0000",
        "000/00/0000",
        "000 00 0000",
        "000000000",
    )

    @pytest.mark.parametrize("ssn", SSN_FORMS)
    def test_every_gate_blocked_ssn_form_is_redacted(self, ssn):
        assert ssn not in redact(f"documents/W2_{ssn}.pdf")
        assert "[REDACTED]" in redact(f"documents/W2_{ssn}.pdf")

    def test_an_ssn_inside_a_longer_digit_run_is_still_redacted(self):
        """Deliberately broader than any token rule: narrowing to exactly-nine
        digits would print an SSN embedded in a ten-digit account number."""
        assert "000000000" not in redact("statement_0000000000.pdf")

    def test_a_credential_shaped_filename_is_redacted(self):
        assert "AKIA" not in redact("keys/AKIAAAAAAAAAAAAAAAAA.txt")

    def test_an_ordinary_filename_still_prints(self):
        """The counterweight: a message that cannot say WHICH path failed is
        not a message. Redaction must not mangle the ordinary case."""
        assert redact("summaries/w2_acme.md") == "summaries/w2_acme.md"

    def test_cli_redacts_both_the_target_and_the_exception(self, tmp_path):
        """On POSIX the raw filename arrives inside os.chmod's own OSError
        text, so redacting only the target would still disclose it."""
        target = tmp_path / "W2_000-00-0000.pdf"
        result = subprocess.run(
            [sys.executable, "-m", "agents.lib.secure_fs", str(target)],
            capture_output=True, text=True, cwd=REPO_ROOT)
        assert result.returncode == 1
        assert "000-00-0000" not in result.stderr
        assert "[REDACTED]" in result.stderr

    @posix_only  # there is no chmod on the nt branch; its own arm is stubbed below
    def test_secure_path_raises_a_redacted_message_on_a_chmod_failure(
            self, tmp_path, monkeypatch):
        """The guarantee is made at the module BOUNDARY: archive.py and
        tie_out.py embed this text verbatim in the errors they print, so
        redacting only at the CLI would leave every library path leaking."""
        f = tmp_path / "result_000-00-0000.json"
        f.write_text("{}", encoding="utf-8")

        def denied(path, mode):
            raise PermissionError(13, "Permission denied", str(path))

        monkeypatch.setattr("agents.lib.secure_fs.os.chmod", denied)
        with pytest.raises(OSError) as excinfo:
            secure_path(f)
        assert "000-00-0000" not in str(excinfo.value)

    def test_secure_path_raises_a_redacted_message_on_a_reparse_point(self, tmp_path):
        real = tmp_path / "real.json"
        real.write_text("{}", encoding="utf-8")
        link = tmp_path / "answers_000-00-0000.md"
        link.symlink_to(real)
        with pytest.raises(OSError) as excinfo:
            secure_path(link)
        assert "000-00-0000" not in str(excinfo.value)

    def test_windows_failure_redacts_the_path_and_the_powershell_detail(
            self, tmp_path, monkeypatch):
        """PowerShell quotes the offending path back in its own error text, so
        the DETAIL is a second copy of the thing being protected."""
        monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")
        f = tmp_path / "profile.json"
        f.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(
            "agents.lib.secure_fs.subprocess.run",
            lambda cmd, **kw: _FakeCompletedProcess(
                returncode=1,
                stderr="Set-Acl : Access denied on 'C:\\ws\\W2_000-00-0000.pdf'"))
        with pytest.raises(OSError) as excinfo:
            secure_path(f)
        assert "000-00-0000" not in str(excinfo.value)


# --- Every component, not just the leaf --------------------------------------

class TestAncestorComponentsAreInspected:
    """os.lstat() declines to follow only the FINAL component.

    A leaf-only guard therefore certifies `archive/{year}/as_filed` as a real
    directory while `archive` is a symlink or junction, and the chmod that
    follows lands on whatever `archive` points at — outside the workspace, past
    every gitignore and PII rule scoped to it. /tax-amend reaches this
    directly: it passes `archive`, `archive/{year}` and `archive/{year}/as_filed`
    in one CLI call, and pipes `find archive/{year}/as_filed` into a second,
    where every path `find` yields has a real leaf under a linked ancestor.
    """

    @staticmethod
    def _linked_workspace(tmp_path):
        """ws/archive -> ../outside, with a real file two levels below it."""
        outside = tmp_path / "outside" / "2026" / "as_filed"
        outside.mkdir(parents=True)
        doc = outside / "W2.pdf"
        doc.write_text("x", encoding="utf-8")
        doc.chmod(0o644)
        (outside.parent.parent).chmod(0o755)
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "archive").symlink_to(tmp_path / "outside")
        return ws, doc

    @posix_only
    def test_a_real_leaf_under_a_linked_ancestor_is_refused(
            self, tmp_path, monkeypatch):
        ws, doc = self._linked_workspace(tmp_path)
        monkeypatch.chdir(ws)
        with pytest.raises(OSError) as excinfo:
            secure_path("archive/2026/as_filed/W2.pdf")
        assert "symlink or junction" in str(excinfo.value)
        assert oct(doc.stat().st_mode & 0o777) == "0o644", \
            "the external file was chmod'd through the linked ancestor"

    def test_a_linked_ancestor_is_named_in_the_refusal(self, tmp_path, monkeypatch):
        """Naming only the leaf would send the user looking at a path that is
        exactly what it appears to be."""
        ws, _ = self._linked_workspace(tmp_path)
        monkeypatch.chdir(ws)
        with pytest.raises(OSError) as excinfo:
            secure_path("archive/2026/as_filed")
        assert "lies under" in str(excinfo.value)
        assert "archive" in str(excinfo.value)

    @posix_only
    def test_a_dotdot_component_is_not_normalized_away(self, tmp_path, monkeypatch):
        """os.path.relpath()/normpath() collapse `link/../x` to `x`, dropping
        the very component being inspected — the kernel resolves a `..` only
        after traversing what precedes it."""
        ws, _ = self._linked_workspace(tmp_path)
        # `archive/../real` resolves to tmp_path/real — OUTSIDE the workspace,
        # because the kernel expands `archive` before it reads the `..`. The
        # directory is real, so a leaf-only check finds nothing wrong with it
        # and chmods a path two levels outside the workspace.
        escaped = tmp_path / "real"
        escaped.mkdir()
        escaped.chmod(0o755)
        monkeypatch.chdir(ws)
        with pytest.raises(OSError, match="lies under"):
            secure_path("archive/../real")
        assert oct(escaped.stat().st_mode & 0o777) == "0o755"

    @posix_only
    def test_an_absolute_path_under_the_current_directory_is_walked(
            self, tmp_path, monkeypatch):
        ws, doc = self._linked_workspace(tmp_path)
        monkeypatch.chdir(ws)
        with pytest.raises(OSError):
            secure_path(str(ws / "archive" / "2026" / "as_filed" / "W2.pdf"))
        assert oct(doc.stat().st_mode & 0o777) == "0o644"

    @posix_only
    def test_an_absolute_path_outside_the_current_directory_keeps_working(
            self, tmp_path, monkeypatch):
        """The documented scope, and the reason for it. macOS puts every
        temporary directory under `/var`, which IS a symlink, so walking an
        absolute path to the filesystem root would refuse to secure a perfectly
        legitimate workspace — and a gate that fires on correct data teaches
        the caller to route around it. Outside the current directory this
        module cannot know where the trust boundary is, so it checks the leaf,
        which is what the library callers (whose roots are resolve()d) rely on.
        """
        linked_parent = tmp_path / "elsewhere"
        linked_parent.mkdir()
        (tmp_path / "via_link").symlink_to(linked_parent)
        target = linked_parent / "result.json"
        target.write_text("{}", encoding="utf-8")
        # cwd is a sibling, so the absolute path below is not under it — the
        # `/var` case, reproduced without depending on the host's own layout.
        elsewhere_cwd = tmp_path / "cwd"
        elsewhere_cwd.mkdir()
        monkeypatch.chdir(elsewhere_cwd)
        secure_path(str(tmp_path / "via_link" / "result.json"))
        assert oct(target.stat().st_mode & 0o777) == "0o600"

    def test_the_leaf_is_still_refused(self, tmp_path, monkeypatch):
        """The pre-existing guarantee, kept: the walk must not have replaced
        the leaf check with an ancestor-only one."""
        real = tmp_path / "real.json"
        real.write_text("{}", encoding="utf-8")
        (tmp_path / "link.json").symlink_to(real)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(OSError, match="is a symlink or junction"):
            secure_path("link.json")

    def test_an_uninspectable_component_refuses_with_a_redacted_message(
            self, tmp_path, monkeypatch):
        """is_reparse_point() fails CLOSED, and lstat's own error text carries
        the RAW path — so the translation happens at this module's boundary."""
        f = tmp_path / "W2_000-00-0000.pdf"
        f.write_text("x", encoding="utf-8")

        def denied(path, *a, **kw):
            raise PermissionError(13, "Permission denied", str(path))

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("agents.lib.secure_fs.os.lstat", denied)
        with pytest.raises(OSError) as excinfo:
            secure_path("W2_000-00-0000.pdf")
        assert "could not be inspected" in str(excinfo.value)
        assert "000-00-0000" not in str(excinfo.value)


class TestAWindowsSpellingCannotSwitchTheWalkOff:
    """Windows resolves paths case-insensitively and honours both separators.

    The containment test that decides whether to walk an absolute path's
    ancestors was one raw `startswith`, sensitive to both. From `C:\\Work`, a
    caller passing `c:/Work/archive/2026/as_filed/W2.pdf` was therefore judged
    to be OUTSIDE the current directory and got the leaf-only check — `archive`
    a junction, `lstat` seeing the real leaf beyond it, and `Set-Acl` applied
    through it to whatever it points at. The ancestor safeguard turned off by
    nothing but a spelling, on the one platform whose junctions it exists for.

    Simulated by patching os.name, which is how every Windows branch in this
    module is reached from POSIX CI. The paths stay POSIX-shaped and REAL:
    only the prefix the containment test compares is respelled, so the
    components the walk builds are the genuine ones on disk and the refusal is
    a real refusal rather than a lookup that missed.
    """

    @staticmethod
    def _linked_workspace(tmp_path):
        outside = tmp_path / "outside" / "2026" / "as_filed"
        outside.mkdir(parents=True)
        doc = outside / "W2.pdf"
        doc.write_text("x", encoding="utf-8")
        doc.chmod(0o644)
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "archive").symlink_to(tmp_path / "outside")
        return ws, doc

    @posix_only
    def test_a_differently_cased_prefix_still_walks_the_ancestors(
            self, tmp_path, monkeypatch):
        ws, doc = self._linked_workspace(tmp_path)
        monkeypatch.chdir(ws)
        monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")
        # The cwd portion respelled; the tail left alone, so every component
        # built from it exists.
        respelled = str(ws).upper() + "/archive/2026/as_filed/W2.pdf"
        with pytest.raises(OSError, match="lies under"):
            secure_path(respelled)
        assert oct(doc.stat().st_mode & 0o777) == "0o644", \
            "the external file was secured through the linked ancestor"

    @posix_only
    def test_a_backslash_separated_tail_still_walks_the_ancestors(
            self, tmp_path, monkeypatch):
        ws, doc = self._linked_workspace(tmp_path)
        monkeypatch.chdir(ws)
        monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")
        with pytest.raises(OSError, match="lies under"):
            secure_path(str(ws) + "\\archive\\2026\\as_filed\\W2.pdf")
        assert oct(doc.stat().st_mode & 0o777) == "0o644"

    @posix_only
    def test_posix_containment_stays_case_sensitive(self, tmp_path, monkeypatch):
        """The fold is Windows-only, and that is not a detail: `Archive` and
        `archive` are two different directories on POSIX, and treating them as
        one would let the walk build components that do not exist and certify
        a path it never inspected."""
        from agents.lib.secure_fs import _normcase, _parts_below

        assert _normcase("Archive") == "Archive"
        assert _parts_below("/ws/ARCHIVE/x", "/ws/archive") is None
        assert _parts_below("/ws/archive/x", "/ws/archive") == ["x"]
        monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")
        assert _normcase("Archive") == "archive"
        assert _parts_below("/ws/ARCHIVE/x", "/ws/archive") == ["x"]

    def test_the_inspected_components_keep_the_spelling_that_exists_on_disk(
            self, tmp_path, monkeypatch):
        """Folding decides CONTAINMENT and nothing else. A component handed to
        lstat must be a path that exists, or the walk inspects nothing and
        reports clean."""
        from agents.lib.secure_fs import _named_components

        ws = tmp_path / "ws"
        (ws / "archive" / "2026").mkdir(parents=True)
        monkeypatch.chdir(ws)
        monkeypatch.setattr("agents.lib.secure_fs.os.name", "nt")
        components = _named_components(str(ws).upper() + "/archive/2026")
        assert [os.path.exists(c) for c in components] == [True, True], \
            f"a folded component was handed to the filesystem: {components}"


class TestNoUninspectablePathNamesItselfInATraceback:
    """`is_reparse_point()` fails closed — and used to fail closed LOUDLY.

    `os.lstat`'s OSError carries the operand in `.filename`, so its own text is
    `[Errno 13] Permission denied: '/documents/W2_000-00-0000.pdf'`. Three
    public call sites — parse_utils._validate_file_path, parse_utils.
    scan_workspace and tax_law_fetcher.load_tax_law — call it outside any
    translating try, and the first two run under CLIs that catch nothing, so an
    unreadable document printed its own name in a traceback. A scanned
    document's NAME is the disclosure AGENTS.md §1 restricts `documents/` to
    prevent, and an SSN in a filename is the absolute case.

    The structural gate in TestNoRuntimePathReachesAMessageUnredacted cannot
    see this one: it reads `raise` and `print` statements for interpolated
    paths, and here nothing was interpolated — the message was built by the OS
    and the statement was a bare propagation.
    """

    SSN_SHAPED_DOC = "W2_000-00-0000.pdf"

    @staticmethod
    def _denying_lstat(path, *a, **kw):
        raise PermissionError(13, "Permission denied", str(path))

    def test_the_raw_path_does_not_survive_the_propagation(
            self, tmp_path, monkeypatch):
        f = tmp_path / self.SSN_SHAPED_DOC
        f.write_text("x", encoding="utf-8")
        monkeypatch.setattr("agents.lib.secure_fs.os.lstat", self._denying_lstat)
        with pytest.raises(OSError) as excinfo:
            is_reparse_point(f)
        assert "000-00-0000" not in str(excinfo.value)
        assert self.SSN_SHAPED_DOC not in str(excinfo.value)

    def test_the_error_class_and_errno_survive_it(self, tmp_path, monkeypatch):
        """Callers distinguish a PermissionError from the rest; collapsing every
        inspection failure to a bare OSError would erase which one they have."""
        f = tmp_path / self.SSN_SHAPED_DOC
        f.write_text("x", encoding="utf-8")
        monkeypatch.setattr("agents.lib.secure_fs.os.lstat", self._denying_lstat)
        with pytest.raises(PermissionError) as excinfo:
            is_reparse_point(f)
        assert excinfo.value.errno == 13
        assert "Permission denied" in str(excinfo.value)

    def test_no_filename_attribute_is_left_claiming_a_path(
            self, tmp_path, monkeypatch):
        """Scrubbing `.filename` in place does NOT work — OSError.__str__ tests
        that member for NULL rather than for None, so assigning None renders a
        literal `: None`. The rebuilt exception never had one."""
        f = tmp_path / self.SSN_SHAPED_DOC
        f.write_text("x", encoding="utf-8")
        monkeypatch.setattr("agents.lib.secure_fs.os.lstat", self._denying_lstat)
        with pytest.raises(OSError) as excinfo:
            is_reparse_point(f)
        assert excinfo.value.filename is None
        assert "None" not in str(excinfo.value)

    def test_a_message_only_oserror_is_redacted_too(self, tmp_path, monkeypatch):
        """Not every OSError carries errno/strerror — one raised with a single
        string argument has the path only inside that string."""
        def raise_bare(path, *a, **kw):
            raise OSError(f"I/O error reading {path}")

        f = tmp_path / "W2_000-00-0000.pdf"
        f.write_text("x", encoding="utf-8")
        monkeypatch.setattr("agents.lib.secure_fs.os.lstat", raise_bare)
        with pytest.raises(OSError) as excinfo:
            is_reparse_point(f)
        assert "000-00-0000" not in str(excinfo.value)

    def test_a_missing_path_is_still_not_a_reparse_point(self, tmp_path):
        """The one case the narrowed except clause must still swallow: a path
        that does not exist cannot be a link, and treating that as an error
        would break every caller that checks before creating."""
        assert not is_reparse_point(tmp_path / "not_here.json")

    def test_the_parse_utils_call_sites_inherit_the_redaction(
            self, tmp_path, monkeypatch):
        """The finding named these two by name: a CLI that catches nothing,
        one lstat away from a taxpayer's filename in a traceback."""
        import agents.lib.parse_utils as pu

        doc = tmp_path / self.SSN_SHAPED_DOC
        doc.write_text("x", encoding="utf-8")
        monkeypatch.setattr("agents.lib.secure_fs.os.lstat", self._denying_lstat)
        with pytest.raises(OSError) as excinfo:
            pu._validate_file_path(str(doc), workspace=str(tmp_path))
        assert "000-00-0000" not in str(excinfo.value)
        with pytest.raises(OSError) as excinfo:
            pu.scan_workspace(str(tmp_path))
        assert "000-00-0000" not in str(excinfo.value)

    def test_the_tax_law_fetcher_call_site_inherits_the_redaction(
            self, tmp_path, monkeypatch):
        import agents.lib.tax_law_fetcher as tlf

        cache = tmp_path / "tax_law_2026.json"
        cache.write_text("{}", encoding="utf-8")
        monkeypatch.setattr("agents.lib.secure_fs.os.lstat", self._denying_lstat)
        with pytest.raises(OSError) as excinfo:
            tlf.load_tax_law(2026, str(tmp_path))
        assert str(tmp_path) not in str(excinfo.value)


class TestTheCliStopsAtTheFirstRefusal:
    """The callers pass a tree OUTERMOST-FIRST — /tax-amend passes `archive`,
    `archive/{year}` and `archive/{year}/as_filed` in one call — so a refusal
    on one target says the paths after it are not what they appear to be.
    Reporting the refusal and then carrying on secured the descendants THROUGH
    the refused component, which is the whole failure the refusal exists to
    prevent."""

    @staticmethod
    def _run(ws, *targets):
        env = dict(os.environ, PYTHONPATH=os.path.abspath(REPO_ROOT))
        return subprocess.run(
            [sys.executable, "-m", "agents.lib.secure_fs", *targets],
            capture_output=True, text=True, cwd=str(ws), env=env)

    @posix_only
    def test_the_tax_amend_call_shape_secures_nothing_through_the_link(
            self, tmp_path):
        outside = tmp_path / "outside" / "2026" / "as_filed"
        outside.mkdir(parents=True)
        (tmp_path / "outside").chmod(0o755)
        (outside.parent).chmod(0o755)
        outside.chmod(0o755)
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "archive").symlink_to(tmp_path / "outside")

        result = self._run(ws, "archive", "archive/2026", "archive/2026/as_filed")

        assert result.returncode == 1
        assert "not attempted" in result.stderr
        assert oct((tmp_path / "outside" / "2026").stat().st_mode & 0o777) == "0o755", \
            "a later target was secured through the refused component"
        assert oct(outside.stat().st_mode & 0o777) == "0o755"

    def test_the_count_is_reported_but_never_the_remaining_names(self, tmp_path):
        """Those names are a taxpayer's documents; the whole point of stopping
        is not to disclose the tree."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "real.json").write_text("{}", encoding="utf-8")
        (ws / "link.json").symlink_to(ws / "real.json")
        (ws / "W2_000-00-0000.pdf").write_text("x", encoding="utf-8")

        result = self._run(ws, "link.json", "W2_000-00-0000.pdf")

        assert result.returncode == 1
        assert "1 later path(s) not attempted" in result.stderr
        assert "W2_000-00-0000" not in result.stderr
        assert "000-00-0000" not in result.stderr

    @posix_only
    def test_a_clean_run_still_secures_every_target(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        for name in ("a.json", "b.json"):
            (ws / name).write_text("{}", encoding="utf-8")
            (ws / name).chmod(0o644)

        result = self._run(ws, "a.json", "b.json")

        assert result.returncode == 0, result.stderr
        assert oct((ws / "a.json").stat().st_mode & 0o777) == "0o600"
        assert oct((ws / "b.json").stat().st_mode & 0o777) == "0o600"


# --- The redactor mirrors the gate's pattern families -------------------------

# AWS's own published example secret, 40 characters and path-safe (no slash),
# kept SEPARATE from the keyword below. The commit-time gate matches the
# keyword and the value adjacent to each other, so a source line carrying both
# would — correctly — block this file. Composing them at use keeps the pattern
# under test without writing the shape into a tracked line.
FAKE_AWS_SECRET_PATHSAFE = "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEYab"
AWS_SECRET_KEYWORD = "aws_secret" + "_access_key"
# 40 characters with a long digit run inside, to pin the substitution ORDER.
# TEN digits, not nine: `_NINE_DIGITS` matches nine OR MORE and still fires,
# while the commit-time gate's token rule wants EXACTLY nine — so the fixture
# proves the ordering without the scanner blocking this file for carrying an
# SSN-shaped run, which is what it did on the first attempt.
FAKE_AWS_SECRET_WITH_DIGITS = "zq1234567890defghijklmnopqrstuvwxyzABCDE"


class TestTheRedactorMirrorsEveryGateFamily:
    """A rule that is DETECTED at commit time but not redacted at runtime is
    strictly worse than no rule: the gate blocks a path holding the credential
    and then prints the credential in the block message. The AWS-secret family
    shipped in the gate's PII_PATTERNS and not in this module's redactor, which
    is exactly that shape — so the parity claim is a test now, not a comment.
    """

    # One sample per member of the shell script's PII_PATTERNS array, keyed by
    # the shell VARIABLE NAME so a family added there and not here fails below
    # rather than passing silently.
    FAMILY_SAMPLES = {
        "SSN_DELIMITED": "documents/W2_000-00-0000.pdf",
        "SECRET_PATTERN": "keys/AKIAAAAAAAAAAAAAAAAA.txt",
        "AWS_SECRET_PATTERN": f"env/{AWS_SECRET_KEYWORD}={FAKE_AWS_SECRET_PATHSAFE}",
    }

    def test_the_sample_set_covers_every_family_the_gate_detects(self):
        """The bare-nine-digit rule is deliberately absent: it is token-based
        rather than a PII_PATTERNS member, and has its own tests above."""
        import re as _re

        script = Path(REPO_ROOT, "scripts", "pre-commit-pii-scan.sh").read_text(encoding="utf-8")
        array = _re.search(r"^PII_PATTERNS=\((.*)\)$", script, _re.M)
        assert array, "PII_PATTERNS array not found — has the gate been restructured?"
        names = set(_re.findall(r"\$\{?([A-Z_]+)\}?", array.group(1)))
        assert names == set(self.FAMILY_SAMPLES), (
            "the gate's detected families and this module's redacted families "
            f"have diverged: {names ^ set(self.FAMILY_SAMPLES)}")

    @pytest.mark.parametrize("family", sorted(FAMILY_SAMPLES))
    def test_every_family_is_redacted(self, family):
        sample = self.FAMILY_SAMPLES[family]
        out = redact(sample)
        assert "[REDACTED]" in out, f"{family}: nothing was masked"
        for token in (FAKE_AWS_SECRET_PATHSAFE, "000-00-0000",
                      "AKIAAAAAAAAAAAAAAAAA"):
            if token in sample:
                assert token not in out, f"{family}: the value survived"

    def test_an_aws_secret_containing_a_digit_run_is_fully_masked(self):
        """The substitution ORDER is load-bearing. Run the SSN or nine-digit
        rule first and it eats a digit run inside the 40-character value; the
        keyword+value form then no longer matches, and most of the credential
        prints. Widest match first."""
        out = redact(f"{AWS_SECRET_KEYWORD}={FAKE_AWS_SECRET_WITH_DIGITS}")
        assert FAKE_AWS_SECRET_WITH_DIGITS not in out
        for fragment in ("zq", "defghijklmnopqrstuvwxyzABCDE"):
            assert fragment not in out, "a fragment of the credential survived"

    def test_a_credential_containing_an_ssn_shaped_run_is_fully_masked(self):
        """The same ordering defect as the AWS case, in the other credential
        family and reported separately. `sk-[A-Za-z0-9_-]{20,}` accepts hyphens,
        so an API key can carry an SSN-SHAPED run inside it; with the narrow SSN
        rule running first the token became `sk-[REDACTED]` followed by twenty
        unmasked characters of the credential — and this function's whole job is
        sanitizing the terminal errors that name it.

        Assembled from fragments so the file itself carries no `sk-` token: a
        literal would be blocked by the commit-time gate whose families this
        class exists to mirror.
        """
        secret = "sk-" + "000-00-0000" + "AbCdEfGhIjKlMnOpQrSt"
        out = redact(f"Authorization: {secret}")
        assert secret not in out
        for fragment in ("AbCdEfGhIjKlMnOpQrSt", "000-00-0000"):
            assert fragment not in out, "a fragment of the credential survived"

    def test_the_hyphenated_and_upper_case_spellings_are_redacted(self):
        """`aws-secret-access-key` is as conventional as the underscored form
        in YAML and CLI config, and the gate matches both case-insensitively."""
        for keyword in ("aws-secret" + "-access-key",
                        "AWS_SECRET" + "_ACCESS_KEY"):
            out = redact(f"{keyword}: {FAKE_AWS_SECRET_PATHSAFE}")
            assert FAKE_AWS_SECRET_PATHSAFE not in out, keyword

    def test_an_unlabelled_forty_character_value_still_prints(self):
        """The counterweight: without the keyword the value is indistinguishable
        from a hash or a base64 blob, and masking every one of those would mangle
        ordinary messages. The gate draws the line in the same place."""
        assert FAKE_AWS_SECRET_PATHSAFE in redact(
            f"summaries/{FAKE_AWS_SECRET_PATHSAFE}.md")


# --- The whole class, not the three sites that were reported ------------------

class TestNoRuntimePathReachesAMessageUnredacted:
    """Three sites were REPORTED; the defect is a class.

    `secure_fs` redacts at its own boundary, but every module that names a
    runtime path in an error had the same hole — `archive.py` printed a refused
    entry's name, `parse_utils.py` printed a skipped document's name, `tie_out.py`
    printed the `summaries/` path — and each one is a taxpayer's scanned
    document, whose NAME is the disclosure AGENTS.md §1 restricts those
    directories to prevent. Fixing the reported three and leaving the rest is
    how this shipped the first time, so the invariant is enforced structurally:
    inside these modules, a `raise` or a `print` may not interpolate a path or
    an exception except through `redact()`.

    The exception half is not padding. `os.chmod`, `os.lstat` and `shutil.move`
    all name their operand in their OWN message text, so a `{path}` that is
    dutifully redacted next to a raw `{e}` discloses the path anyway, by the
    half of the sentence that came from the OS.

    SCOPE, stated rather than left to be discovered: this enforces the PATH and
    EXCEPTION class mechanically. Two neighbouring classes are handled by hand
    and deliberately not by this rule.

    Untrusted values echoed back by a validator (`got {value!r}`) are redacted
    at the sites where the value is a STRING, because no mechanical rule
    separates those from the integer indices and fixed-vocabulary labels in the
    same messages, and a detector that fires on correct code gets watered down
    until it means nothing — the same trade `_CHMOD_EXPLANATORY_MARKERS` makes.

    Numeric FIGURES are deliberately NOT redacted anywhere. `redact()` masks any
    run of nine or more digits, so a legitimate nine-figure amount would print
    as [REDACTED] — over-redacting the number a taxpayer has to check is its own
    failure, and AGENTS.md §1 draws the line at identity data, not at figures.
    """

    # EVERY module under agents/lib/, not the handful this change happened to
    # touch. Scoping it to those would have left `projection.py` naming a raw
    # workspace path and `tax_law_fetcher.py` naming the cache — the same
    # defect, one import away, with a gate sitting next to it reporting green.
    # RELATIVE PATHS, not stems, and the walk is `tests/engine_tree.py`'s
    # rather than this class's. A stem no longer names a file: `ny` is
    # `tax_computations/state/ny.py` and two packages could both hold a
    # `state`. The id pytest prints is the path, which is also the thing a
    # reader needs in order to open the offender.
    # The engine package itself is excluded by PATH and not by stem.
    # `p.stem != "__init__"` named ONE file while this directory was flat;
    # under a package it names one per package, and the first of those was
    # `tax_computations/__init__.py` — 5,600 lines, the entire federal engine,
    # and the module likeliest of all of them to name a workspace path. It left
    # this gate silently, with the roster count going UP.
    # Global names only: a genexpr in a CLASS body cannot see a name bound
    # beside it, which is the other reason the walk belongs in a module.
    MODULES = tuple(sorted(
        engine_relpath(p) for p in engine_files() if p != ENGINE_GUARD))
    # Matched against the identifier's `_`-separated TOKENS, not against the
    # whole name and not as a raw substring. The first version of this gate
    # held whole names and passed green over `workspace_dir`, `doc_id` and
    # `c.json_path` — each a runtime path or a document identity, each
    # invisible only because the list happened not to carry that exact
    # spelling. Tokens fix that without the false positives a substring rule
    # brings: `profile_year` CONTAINS "file" and is an integer.
    SENSITIVE_TOKENS = frozenset({
        "path", "paths", "dir", "dirs", "file", "files", "filename",
        "src", "dst", "target", "link", "links", "summaries", "workspace",
        "archive", "candidate", "component", "entry", "tmp", "root",
        # A TOKEN, not a whole name: `unlink_err` and `cleanup_err` are both
        # OSErrors that name the cache path in their own text, and a whole-name
        # rule walked straight past them.
        "err",
    })
    # Whole names only — as tokens these would match far too much. `doc_id` is
    # a document's identity; `doc_type` is the classification "w2" and is not.
    # A bare `name` is NOT a token above, and that is a judgement rather than
    # an omission: `src.name` is a path component and `bracket name` is a
    # label, and the two are indistinguishable by identifier. The attribute
    # rule below still catches `<path-ish>.name`, which is the form that
    # actually carries a filename; the handful of bare `name` emissions that
    # do carry user input (a scenario name, an archived entry) are redacted by
    # hand. Enforcing it mechanically would have meant redacting every
    # parameter-name in the math engine's validation errors, and a rule with
    # that much noise in it gets narrowed until it means nothing.
    SENSITIVE_EXACT = frozenset({"e", "p", "f", "out", "doc", "doc_id"})
    # Attributes that yield another path. `.suffix` and `type(e).__name__` are
    # scalars and deliberately absent: redacting those would be noise, and a
    # rule with noise in it gets exemptions added until it means nothing.
    PATHLIKE_ATTRS = frozenset({"name", "parent", "stem", "path", "filename", "args"})

    def _is_sensitive(self, identifier):
        lowered = identifier.lower()
        if identifier.isupper():          # a module constant, not runtime data
            return False
        if lowered in self.SENSITIVE_EXACT:
            return True
        return any(token in self.SENSITIVE_TOKENS for token in lowered.split("_"))

    def _offenders(self, module_name):
        import ast

        source = Path(REPO_ROOT, "agents", "lib",
                      module_name).read_text(encoding="utf-8")
        found = []
        for node in ast.walk(ast.parse(source)):
            is_raise = isinstance(node, ast.Raise)
            is_print = (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "print")
            if not (is_raise or is_print):
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.FormattedValue):
                    continue
                value = sub.value
                if (isinstance(value, ast.Call)
                        and getattr(value.func, "id", "") == "redact"):
                    continue
                bare = (isinstance(value, ast.Name)
                        and self._is_sensitive(value.id))
                # An attribute is judged on the ATTRIBUTE, not its object:
                # `c.json_path` is a path whatever `c` is called, and
                # `type(e).__name__` is a class name whatever `e` is.
                attr = (isinstance(value, ast.Attribute)
                        and value.attr != "__name__"
                        and (self._is_sensitive(value.attr)
                             or (value.attr in self.PATHLIKE_ATTRS
                                 and isinstance(value.value, ast.Name)
                                 and self._is_sensitive(value.value.id))))
                if bare or attr:
                    found.append(
                        f"{module_name}.py:{sub.lineno}: {{{ast.unparse(value)}}}")
        return found

    @pytest.mark.parametrize("module_name", MODULES)
    def test_every_message_redacts_its_path_and_its_cause(self, module_name):
        offenders = self._offenders(module_name)
        assert not offenders, (
            "runtime path or exception interpolated without redact(): "
            + "; ".join(offenders))

    def test_the_detector_catches_an_unredacted_interpolation(self, tmp_path):
        """The anti-vacuity guard: a scan that cannot fail is not a scan.

        Runs the SHIPPED walk — the one the parametrized test above uses —
        against a module written to offend in both shapes it recognises, a
        bare name and a path-like attribute.
        """
        lib = tmp_path / "agents" / "lib"
        lib.mkdir(parents=True)
        (lib / "sample.py").write_text(
            'def write(path, src):\n'
            '    print(f"writing {src.name}")\n'
            '    raise OSError(f"could not open {path}")\n', encoding="utf-8")
        offenders = self._offenders_from(tmp_path, "sample.py")
        assert sorted(o.split(": ")[1] for o in offenders) == ["{path}", "{src.name}"]

    def test_the_detector_passes_a_module_that_redacts(self, tmp_path):
        """The counterweight: redacted interpolations, and the scalars that
        deliberately do not need it, must NOT be flagged — a detector that
        fires on correct code gets its rule watered down until it means
        nothing."""
        lib = tmp_path / "agents" / "lib"
        lib.mkdir(parents=True)
        (lib / "clean.py").write_text(
            'def write(path, e, p):\n'
            '    raise OSError(f"{redact(path)} failed: {redact(e)}")\n'
            '    raise ValueError(f"bad suffix {p.suffix}: {type(e).__name__}")\n', encoding="utf-8")
        assert self._offenders_from(tmp_path, "clean.py") == []

    def _offenders_from(self, root, module_name):
        """Run the shipped `_offenders` against an arbitrary root, so the guards
        above test the detector that ships rather than a copy of it."""
        global REPO_ROOT
        saved = REPO_ROOT
        try:
            REPO_ROOT = str(root)
            return self._offenders(module_name)
        finally:
            REPO_ROOT = saved


class TestOnlyARefusalStopsTheCli(TestTheCliStopsAtTheFirstRefusal):
    """The stop is scoped to `PathRefused`, and the scope is the point.

    A refusal says something about the TREE — a component is a symlink,
    junction, or uninspectable — so every path beneath it is equally uncertain.
    A missing file or a denied chmod says nothing about the OTHER targets, and
    stopping there would leave siblings at their default mode over an unrelated
    error. `/tax-parse` passes `summaries/*.md result.json`: an unmatched glob
    arrives as a literal, unexpanded pattern, and `result.json` still has to be
    secured.
    """

    @posix_only
    def test_a_missing_target_does_not_stop_the_rest(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "result.json").write_text("{}", encoding="utf-8")
        (ws / "result.json").chmod(0o644)

        result = self._run(ws, "summaries/*.md", "result.json")

        assert result.returncode == 1, "the failure must still be reported"
        assert "not attempted" not in result.stderr
        assert oct((ws / "result.json").stat().st_mode & 0o777) == "0o600", \
            "an unrelated error left a sibling at its default mode"

    @posix_only
    def test_an_uninspectable_component_DOES_stop_the_rest(self, tmp_path):
        """The other half of the scope. A component that could not be inspected
        is refused on the same terms as one that IS a link — the tree is
        uncertified either way, and fail-closed is the whole reason
        `is_reparse_point()` propagates that error instead of returning False.
        """
        ws = tmp_path / "ws"
        ws.mkdir()
        blocked = ws / "blocked"
        blocked.mkdir()
        (blocked / "a.json").write_text("{}", encoding="utf-8")
        (ws / "b.json").write_text("{}", encoding="utf-8")
        (ws / "b.json").chmod(0o644)
        blocked.chmod(0o000)  # not traversable: lstat on the child cannot run
        try:
            result = self._run(ws, "blocked/a.json", "b.json")
            assert result.returncode == 1
            assert "not attempted" in result.stderr
            assert oct((ws / "b.json").stat().st_mode & 0o777) == "0o644"
        finally:
            blocked.chmod(0o700)

    def test_a_refusal_is_its_own_exception_type(self, tmp_path, monkeypatch):
        """Library callers catch OSError and translate; PathRefused subclasses
        it so that keeps working, while the CLI can tell the two apart."""
        from agents.lib.secure_fs import PathRefused

        real = tmp_path / "real.json"
        real.write_text("{}", encoding="utf-8")
        (tmp_path / "link.json").symlink_to(real)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(PathRefused):
            secure_path("link.json")
        with pytest.raises(OSError):
            secure_path("link.json")
        # ...and an ordinary failure is NOT one.
        with pytest.raises(OSError) as excinfo:
            secure_path("does_not_exist.json")
        assert not isinstance(excinfo.value, PathRefused)


class TestTheNoNetworkClaimIsTrue:
    """SECURITY.md tells a taxpayer that this repository's code makes no network
    request at all, and that the tax-law fetch is the AGENT's, not the library's.

    That is the strongest guarantee in the document and the one a reader most
    needs to be able to trust, so it is machine-checked rather than asserted.
    The earlier wording claimed the code itself fetched tax law from IRS.gov,
    which was never true — `tax_law_fetcher` only PARSES the source URLs the
    agent brings back (`urllib.parse`, pure string work) to decide whether the
    host is authoritative. Getting that backwards misplaces the network boundary
    in the reader's head, and the boundary is exactly what the next bullet in
    SECURITY.md asks them to reason about.
    """

    # Import roots that can open a socket. `urllib.parse` is deliberately absent
    # and `urllib.request` deliberately present: they are the same package and
    # only one of them reaches the network.
    NETWORK_MODULES = frozenset({
        "socket", "ssl", "http", "ftplib", "telnetlib", "smtplib", "poplib",
        "imaplib", "requests", "httpx", "aiohttp", "urllib3", "websockets",
        "xmlrpc", "asyncio.streams",
    })
    NETWORK_SUBMODULES = frozenset({"urllib.request", "urllib.error"})
    SHELL_FETCHERS = ("curl", "wget", "nc", "ssh")
    # Everything the original substring test (`"curl " in line`) caught, less
    # ONE shape: the fetcher's name as the tail of an identifier — dotted
    # (`state.nc import`, North Carolina's module, named by its postal code as
    # the state playbook requires) or word-joined (`spouse_itemizes_nc is`).
    # So the rule is a NEGATIVE lookbehind — anything but a word character or
    # a dot may precede — rather than a list of allowed predecessors, which
    # silently missed `>curl`, `exec:nc`, `[curl` and `!ssh`. The one word
    # character that may precede is the `n`/`t`/`r` of an ESCAPE written in
    # source (`"set -e\ncurl -s x"`), a command on the next line of a shell
    # string. Second alternative: the subprocess LIST form, the fetcher as a
    # whole quoted token opening a list, tuple or call (`["curl", "-s", url]`),
    # which the substring test never saw. It is anchored to the opening
    # bracket on purpose: a bare `"nc"` token is a lowercase postal code
    # (pdf_render's acronym list already holds "ca" and "ny").
    _FETCHER_RE = re.compile(
        r"(?:(?<![\w.])|(?<=\\[ntr]))(?:" + "|".join(SHELL_FETCHERS) + r")\s"
        r"|[\[(]\s*[\"'](?:" + "|".join(SHELL_FETCHERS) + r")[\"']\s*[,)\]]")

    @classmethod
    def _shells_out(cls, line: str) -> bool:
        return cls._FETCHER_RE.search(line) is not None

    def _python_sources(self):
        # `agents/`, deliberately WIDER than `agents/lib/`, and so deliberately
        # not `engine_files()`: the question is whether anything shipped under
        # `agents/` reaches the network, and a module added beside `lib/`
        # tomorrow is exactly the case that must not slip through. The two
        # spellings enumerate the same files today, which is what would make
        # narrowing this to the engine look free.
        return sorted(Path(REPO_ROOT, "agents").rglob("*.py"))

    def test_no_library_module_imports_a_network_client(self):
        import ast

        offenders = []
        for source in self._python_sources():
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names = [node.module]
                for name in names:
                    root = name.split(".")[0]
                    if root in self.NETWORK_MODULES or name in self.NETWORK_SUBMODULES:
                        offenders.append(
                            f"{source.name}:{node.lineno}: imports {name}")
        assert not offenders, (
            "SECURITY.md promises this code opens no socket: " + "; ".join(offenders))

    def test_no_shipped_script_shells_out_to_a_fetcher(self):
        """The other way to reach the network without importing anything."""
        offenders = []
        scripts = (sorted(Path(REPO_ROOT, "agents").rglob("*.sh"))
                   + sorted(Path(REPO_ROOT, "scripts").rglob("*.sh")))
        for source in scripts + self._python_sources():
            for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if self._shells_out(stripped):
                    offenders.append(f"{source.name}:{lineno}: {stripped[:60]}")
        assert not offenders, (
            "SECURITY.md promises this code opens no socket: " + "; ".join(offenders))

    def test_the_fetcher_detector_tells_a_command_from_a_module_path(self):
        """Anti-vacuity both ways: the command forms are caught, and a module
        path ending in a fetcher's name — `state.nc` — is not."""
        for caught in ('nc -l 8080', 'subprocess.run("curl https://x")',
                       "os.system('wget http://x')", 'x=$(ssh host true)',
                       'a && nc host 80', '/usr/bin/curl -s x',
                       'CMD=wget http://x', '"{curl x"',
                       '"set -e\\ncurl -s x"',       # a literal backslash-n
                       "\tnc host 80",                  # a real tab
                       ">curl x", "exec:nc x", "[curl x]", "!ssh host",
                       'subprocess.run(["curl", "-s", url])',
                       "Popen(('nc', host))"):
            assert self._shells_out(caught), caught
        for clean in ("from agents.lib.tax_computations.state.nc import (",
                      'raise ValueError("spouse_itemizes_nc is required")',
                      "if spouse_itemizes_nc is None:",
                      "sync_nc = 1", "encurl = 2",
                      '"ny", "nyc", "nc",', '{"nc": 1}'):
            assert not self._shells_out(clean), clean

    def test_the_detector_would_catch_a_network_import(self, tmp_path):
        """Anti-vacuity: the walk must fail on a module that does reach out."""
        import ast

        (tmp_path / "reaches_out.py").write_text(
            "import urllib.request\n\n\ndef fetch(url):\n"
            "    return urllib.request.urlopen(url).read()\n", encoding="utf-8")
        found = []
        for node in ast.walk(ast.parse((tmp_path / "reaches_out.py").read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in self.NETWORK_SUBMODULES:
                        found.append(alias.name)
        assert found == ["urllib.request"]

    def test_the_url_parsing_the_fetcher_does_use_is_not_flagged(self):
        """The counterweight: `urllib.parse` is string work, and a rule that
        banned it would fire on the source-pinning check SECURITY.md relies on."""
        assert "urllib.parse" not in self.NETWORK_SUBMODULES
        assert "urllib" not in self.NETWORK_MODULES
        fetcher = Path(REPO_ROOT, "agents", "lib", "tax_law_fetcher.py").read_text(encoding="utf-8")
        assert "from urllib.parse import urlparse" in fetcher, (
            "the source-pinning check no longer parses URLs — re-check what "
            "SECURITY.md says the library's role in the fetch is")


class TestTheLauncherNamesNoPathWhenItCannotResolveItsRoot:
    """`run.sh` is invoked by every skill, and its one failure path printed
    `$0`.

    That argument is an absolute path whenever a skill invokes the launcher by
    one, and the tree it sits under is named by the person who made it — the
    scanned-document and taxpayer-identity shapes AGENTS.md §1 restricts those
    directories to prevent. Both halves disclosed it: this script's own `echo`,
    and the shell's `cd: can't cd to ...` diagnostic underneath it. A POSIX
    shell has no redactor to reach for, so the path is dropped rather than
    masked; what a reader needs — which script, which step, what to do — does
    not require it.
    """

    # A NAME rather than an SSN-shaped token, deliberately. The mechanism is
    # the same for any string in the path, and writing nine digits into a
    # tracked file — even synthetic ones — is what the PII gate blocks and what
    # `.pii-allowlist` exists to make a deliberate exception for. Nothing here
    # needs the exception.
    IDENTIFIER = "Jane-Q-Household-Returns"

    def _tree(self, tmp_path):
        lib = tmp_path / self.IDENTIFIER / "agents" / "lib"
        lib.mkdir(parents=True)
        launcher = lib / "run.sh"
        launcher.write_text(
            Path(REPO_ROOT, "agents", "lib", "run.sh").read_text(encoding="utf-8"), encoding="utf-8")
        launcher.chmod(0o755)
        return lib

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                        reason="root traverses a 000 directory, so the "
                               "failure this measures cannot be produced")
    @posix_only
    def test_a_root_it_cannot_reach_is_reported_without_the_path(self,
                                                                 tmp_path):
        """Run it from inside the tree with the grandparent unreadable: the
        script itself opens (the cwd is already resolved) and the two-levels-up
        `cd` cannot traverse. That is the real failure — an unreadable ancestor
        or an oddly-resolved `$0` — rather than a fabricated message."""
        lib = self._tree(tmp_path)
        blocked = tmp_path / self.IDENTIFIER
        # The `cd` and the `chmod` both happen INSIDE the child, in that order.
        # Passing `cwd=` to subprocess would have the child resolve that
        # absolute path after the directory is already unreadable, so the
        # traversal fails before the launcher is ever reached — the test would
        # measure Python's spawn, not this script.
        try:
            result = subprocess.run(
                ["sh", "-c",
                 f'cd "{lib}" || exit 99\n'
                 f'chmod 000 "{blocked}" || exit 98\n'
                 'sh ./run.sh -c pass\n'
                 'status=$?\n'
                 f'chmod 755 "{blocked}"\n'
                 'exit $status\n'],
                capture_output=True, text=True)
        finally:
            blocked.chmod(0o755)

        assert result.returncode == 1, (
            "the launcher continued past a root it could not resolve, which "
            "is how it ends up exec'ing the HOST interpreter silently: "
            f"{result.stderr}")
        assert self.IDENTIFIER not in result.stderr, (
            "the workspace path reached the terminal: " + result.stderr)
        assert self.IDENTIFIER not in result.stdout
        assert "cannot resolve the toolkit root" in result.stderr, (
            "the path was dropped and so was the diagnosis — a refusal has to "
            "say what failed")

    def test_neither_half_of_the_diagnostic_can_carry_a_path(self):
        """A SOURCE assertion, and deliberately so — the behavioural test above
        cannot reach this claim.

        It invokes the launcher as `sh ./run.sh`, so `$0` is relative and no
        workspace name could appear in the output whatever the script printed.
        Making `$0` absolute is not a matter of writing a longer test: opening
        `<id>/agents/lib/run.sh` and `cd`-ing to `<id>` need the SAME traversal
        permission on `<id>`, so an interpreter that got far enough to run the
        script is one whose `cd` succeeds. The two states cannot co-exist, and
        a test that ran anyway would be asserting an absence it had no way to
        produce — which is the vacuous shape this suite keeps finding.

        So the disclosure half is pinned where it is decidable. Both channels
        are covered because both leaked: the script's own message, and the
        shell's `cd: can't cd to <path>` underneath it, which discloses just as
        completely and is silenced by the redirect rather than by anything the
        script says.
        """
        source = Path(REPO_ROOT, "agents", "lib", "run.sh").read_text(encoding="utf-8")
        echoed = [line for line in source.splitlines()
                  if "echo" in line and not line.lstrip().startswith("#")]
        assert echoed, "no diagnostics at all — this test read the wrong file"
        leaking = [line for line in echoed if "$0" in line or "$ROOT" in line]
        assert not leaking, (
            "a launcher diagnostic interpolates the path it was invoked by, "
            f"which is the workspace name AGENTS.md §1 restricts: {leaking}")

        resolution = [line for line in source.splitlines()
                      if "dirname" in line and "cd" in line]
        assert len(resolution) == 1, (
            f"expected one root-resolution line, found {resolution}")
        assert "2>/dev/null" in resolution[0], (
            "the shell prints its own path-bearing `cd:` diagnostic on the "
            "same failure, so dropping the path from the echo alone leaks it "
            f"anyway: {resolution[0]}")

    @posix_only
    def test_the_launcher_still_runs_from_that_same_tree(self, tmp_path):
        """The counterweight: a launcher that refused every invocation would
        satisfy the assertion above perfectly, and the identifier only stays
        out of the output because nothing ran."""
        lib = self._tree(tmp_path)
        result = subprocess.run(
            ["sh", "./run.sh", "-c", "print('ran')"],
            cwd=str(lib), capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "ran" in result.stdout


class TestYearOrShapeOnlyPrintsAYear:
    """`year_or_shape` decides what a refusal about a hand-edited year field
    may disclose. Its first version tested the wrong property.

    "An integer cannot carry anything sensitive" was the reasoning, and it does
    not hold: `tax_year` is hand-edited, and an account, policy or member
    number lands in it as an int exactly as a misplaced name lands in it as a
    str. `redact` is not the backstop — `_NINE_DIGITS` masks a nine-digit run
    and nothing shorter, so eight digits printed in full — which is the same
    reason this function exists for strings at all.
    """

    def test_a_real_year_still_prints(self):
        """The counterweight. Suppressing everything would pass every leak
        assertion below while making the message useless: WHICH year the file
        is for is the whole content of a year-mismatch refusal."""
        assert year_or_shape(2025) == "TY2025"
        assert year_or_shape(_MIN_PRINTABLE_YEAR) == f"TY{_MIN_PRINTABLE_YEAR}"
        assert year_or_shape(_MAX_PRINTABLE_YEAR) == f"TY{_MAX_PRINTABLE_YEAR}"

    def test_an_integer_outside_the_window_is_shape_only(self):
        # Built from strings, not written as literals: a nine-digit run in
        # source is exactly what the PII gate blocks, and it blocked this.
        ssn_shaped = int("1" * 9)
        card_shaped = int("4" + "2" * 15)
        for hidden in (87654321,            # an eight-digit account number
                       ssn_shaped,          # nine digits: an SSN typed in
                       card_shaped,
                       _MIN_PRINTABLE_YEAR - 1,
                       _MAX_PRINTABLE_YEAR + 1,
                       -87654321, 0):
            shape = year_or_shape(hidden)
            assert str(abs(hidden)) not in shape, (
                f"year_or_shape printed a non-year integer: {shape!r}")
            assert shape.startswith("an int ("), shape

    def test_the_digit_count_is_all_that_survives(self):
        assert year_or_shape(87654321) == "an int (8 digits)"
        assert year_or_shape(-5) == "an int (1 digits)"

    def test_the_window_covers_every_callers_accepted_range(self):
        """Direction matters, and only one direction is a leak.

        NARROWER than a caller and a legitimate year prints as a shape: the
        message degrades, and nothing escapes. WIDER and the window stops
        meaning "a year" — so it is pinned against the widest range any caller
        accepts rather than left to drift with it.
        """
        sys.path.insert(0, REPO_ROOT)
        from agents.lib import tax_calendar as cal
        assert _MIN_PRINTABLE_YEAR <= cal._MIN_YEAR, (
            "tax_calendar accepts a year this refuses to print, so its own "
            "mismatch message would name no year at all")
        assert _MAX_PRINTABLE_YEAR >= cal._MAX_YEAR
        assert cal._MIN_YEAR - _MIN_PRINTABLE_YEAR < 200, (
            "the printable window has drifted far wider than any caller's "
            "accepted range, which is the direction that discloses")

    def test_a_bool_is_still_caught_before_int(self):
        """`isinstance(True, int)` is True, and 1 is outside the window — so a
        bool would now read as "an int (1 digits)" rather than leaking, but it
        would also stop naming the one shape that reads like a year."""
        assert year_or_shape(True) == "a bool"
        assert year_or_shape(False) == "a bool"


class TestTheOneWriterCoversTheTwoFileTransaction:
    """V10's second half. `apply_changes` rewrites `taxpayer_profile.json` and
    `result.json` from one decision, and a half-applied pair is a workspace whose
    profile says one thing and whose return says another with nothing to tell a
    reader which. It hand-rolled that transaction because the single-file helper
    could not express it; `atomic_paths` is the shape that can.
    """

    def _pair(self, tmp_path):
        a, b = tmp_path / "profile.json", tmp_path / "result.json"
        a.write_text('{"a": 1}', encoding="utf-8")
        b.write_text('{"b": 2}', encoding="utf-8")
        return a, b

    def _leftovers(self, tmp_path):
        return sorted(p.name for p in tmp_path.iterdir()
                      if p.suffix in (".tmp", ".bak"))

    @posix_only
    def test_both_targets_are_published_on_a_clean_exit(self, tmp_path):
        a, b = self._pair(tmp_path)
        with atomic_paths(a, b) as (ta, tb):
            ta.write_text('{"a": 9}', encoding="utf-8")
            tb.write_text('{"b": 9}', encoding="utf-8")
        assert a.read_text(encoding="utf-8") == '{"a": 9}' and b.read_text(encoding="utf-8") == '{"b": 9}'
        assert self._leftovers(tmp_path) == []

    @posix_only
    def test_an_exception_in_the_body_publishes_neither(self, tmp_path):
        a, b = self._pair(tmp_path)
        with pytest.raises(RuntimeError):
            with atomic_paths(a, b) as (ta, tb):
                ta.write_text('{"a": 9}', encoding="utf-8")
                tb.write_text('{"b": 9}', encoding="utf-8")
                raise RuntimeError("the caller changed its mind")
        assert a.read_text(encoding="utf-8") == '{"a": 1}' and b.read_text(encoding="utf-8") == '{"b": 2}'
        assert self._leftovers(tmp_path) == []

    def test_a_failed_second_rename_restores_the_first(self, tmp_path,
                                                       monkeypatch):
        """The case the hand-rolled version existed for, and the one no test
        covered. Without the restore the profile holds the new value and the
        result the old one — a pair that never existed together."""
        a, b = self._pair(tmp_path)
        real = os.replace
        calls = {"n": 0}

        def fail_on_the_second(src, dst, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("the second rename failed")
            return real(src, dst, *args, **kwargs)

        monkeypatch.setattr(os, "replace", fail_on_the_second)
        with pytest.raises(OSError):
            with atomic_paths(a, b) as (ta, tb):
                ta.write_text('{"a": 9}', encoding="utf-8")
                tb.write_text('{"b": 9}', encoding="utf-8")
        assert a.read_text(encoding="utf-8") == '{"a": 1}', "the first target was not restored"
        assert b.read_text(encoding="utf-8") == '{"b": 2}'

    def test_the_restore_puts_back_the_exact_original_bytes(self, tmp_path,
                                                            monkeypatch):
        """A hardlink to the original inode, renamed back — not a re-write of
        remembered bytes. The version this replaces restored with `write_bytes`,
        so a crash DURING the rollback truncated the file the rollback existed
        to protect."""
        a, b = self._pair(tmp_path)
        a.write_bytes(b"\x00\xff binary-ish \n" * 100)
        original = a.read_bytes()
        real = os.replace
        calls = {"n": 0}

        def fail_on_the_second(src, dst, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("boom")
            return real(src, dst, *args, **kwargs)

        monkeypatch.setattr(os, "replace", fail_on_the_second)
        with pytest.raises(OSError):
            with atomic_paths(a, b) as (ta, tb):
                ta.write_bytes(b"new")
                tb.write_text("new", encoding="utf-8")
        assert a.read_bytes() == original

    def test_a_target_that_did_not_exist_is_removed_again_on_a_late_failure(
            self, tmp_path, monkeypatch):
        """There is no original to restore, so the restore is a delete. Leaving
        the new file behind would be a half-applied pair wearing the shape of a
        complete one."""
        a = tmp_path / "new.json"
        b = tmp_path / "result.json"
        b.write_text('{"b": 2}', encoding="utf-8")
        real = os.replace
        calls = {"n": 0}

        def fail_on_the_second(src, dst, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("boom")
            return real(src, dst, *args, **kwargs)

        monkeypatch.setattr(os, "replace", fail_on_the_second)
        with pytest.raises(OSError):
            with atomic_paths(a, b) as (ta, tb):
                ta.write_text("{}", encoding="utf-8")
                tb.write_text("{}", encoding="utf-8")
        assert not a.exists(), "a target with no original was left behind"
        assert b.read_text(encoding="utf-8") == '{"b": 2}'

    def test_every_target_is_owner_only_at_the_moment_of_the_RENAME(
            self, tmp_path, monkeypatch):
        """Read at the RENAME, which is the only point where the pre-fix window
        is observable — and the only reading that is not vacuous.

        Asserting the mode AFTER the publish passed with `secure_fd` removed and
        with the pre-rename `secure_path` removed, because `mkstemp` already
        creates at 0600 and writing to an existing path does not change it. What
        that spelling could not see is a writer that RECREATES the temp, which is
        exactly what the pre-rename call exists for."""
        if os.name == "nt":  # pragma: no cover - POSIX mode bits
            pytest.skip("POSIX mode bits")
        a, b = self._pair(tmp_path)
        seen = []
        real = os.replace
        monkeypatch.setattr(os, "replace", lambda src, dst, *r, **k: (
            seen.append(os.stat(src).st_mode & 0o777), real(src, dst, *r, **k))[1])
        yielded = []
        old = os.umask(0o022)      # without this the assertion is vacuous
        try:
            with atomic_paths(a, b) as (ta, tb):
                # BEFORE the first byte. On POSIX this is `mkstemp`'s own 0600
                # rather than `secure_fd`'s doing — removing `secure_fd` leaves
                # this assertion green, verified — and that is stated rather than
                # implied, because the alternative is a test that reads as
                # covering the Windows ACL arm and does not. THAT arm is not
                # observable from here at all: it shells out, and this suite runs
                # on POSIX. What this pins is the property a caller depends on —
                # the path handed over is already owner-only — whichever call
                # provides it on the platform in hand.
                yielded = [p.stat().st_mode & 0o777 for p in (ta, tb)]
                # RECREATED, the reportlab-shaped hazard: a writer handed a path
                # may replace the file, and whatever it creates carries the
                # umask unless something tightens it before the rename.
                ta.unlink()
                ta.write_text("{}", encoding="utf-8")
                tb.write_text("{}", encoding="utf-8")
        finally:
            os.umask(old)
        assert yielded == [0o600, 0o600], (
            f"a temp was handed to the caller at {yielded} — it must be "
            "owner-only before it can hold anything")
        assert seen == [0o600, 0o600], f"a temp was renamed at {seen}"
        assert a.stat().st_mode & 0o777 == 0o600
        assert b.stat().st_mode & 0o777 == 0o600

    @posix_only
    def test_the_temp_names_are_unique_so_two_runs_do_not_collide(self, tmp_path):
        """The hand-rolled version used a FIXED `.tmp` suffix beside the target,
        so two applies running at once wrote the same scratch file and one
        published the other's bytes. V10's second half is concurrency."""
        a, b = self._pair(tmp_path)
        seen = set()
        for _ in range(3):
            with atomic_paths(a, b) as (ta, tb):
                seen.update({ta.name, tb.name})
                ta.write_text("{}", encoding="utf-8")
                tb.write_text("{}", encoding="utf-8")
        assert len(seen) == 6, f"temp names repeat across runs: {sorted(seen)}"

    @posix_only
    def test_a_published_temp_is_not_unlinked_afterwards(self, tmp_path,
                                                          monkeypatch):
        """The rename frees the temp NAME. Left on the cleanup list, the unlink
        in `finally` is a harmless miss until the day `mkstemp` hands that name
        out again — at which point it deletes somebody else's file."""
        a, b = self._pair(tmp_path)
        unlinked = []
        real = os.unlink
        monkeypatch.setattr(os, "unlink",
                            lambda p, *a_, **k: (unlinked.append(str(p)),
                                                 real(p, *a_, **k))[1])
        with atomic_paths(a, b) as (ta, tb):
            names = {str(ta), str(tb)}
            ta.write_text("{}", encoding="utf-8")
            tb.write_text("{}", encoding="utf-8")
        assert not (names & set(unlinked)), (
            f"a temp that had been renamed away was unlinked: "
            f"{sorted(names & set(unlinked))}")

    def test_a_link_failure_that_is_not_about_hardlinks_propagates(
            self, tmp_path, monkeypatch):
        """Only "this filesystem cannot hardlink" is a reason to copy. A blanket
        `except OSError` would answer EEXIST — something took the name — by
        overwriting whatever took it."""
        a, b = self._pair(tmp_path)
        monkeypatch.setattr(os, "link", lambda *a_, **k: (_ for _ in ()).throw(
            FileExistsError(errno.EEXIST, "taken")))
        with pytest.raises(OSError):
            with atomic_paths(a, b) as (ta, tb):
                ta.write_text("{}", encoding="utf-8")
                tb.write_text("{}", encoding="utf-8")
        assert a.read_text(encoding="utf-8") == '{"a": 1}' and b.read_text(encoding="utf-8") == '{"b": 2}'

    def test_the_copied_backup_is_owner_only_and_never_orphaned(
            self, tmp_path, monkeypatch):
        """`shutil.copy2` opens at the umask default and only reaches 0600 when
        `copystat` finishes, so a failure anywhere in it left a world-readable —
        sometimes partial — copy of `taxpayer_profile.json` that nothing removed,
        because it was registered for cleanup only AFTER the copy returned."""
        if os.name == "nt":  # pragma: no cover - POSIX mode bits
            pytest.skip("POSIX mode bits")
        a, b = self._pair(tmp_path)
        a.write_text('{"a": "secret"}', encoding="utf-8")
        monkeypatch.setattr(os, "link", lambda *a_, **k: (_ for _ in ()).throw(
            OSError(errno.EOPNOTSUPP, "no hardlinks")))
        modes = []
        real = shutil.copyfileobj

        def spy(src, dst, *r, **k):
            modes.append(os.fstat(dst.fileno()).st_mode & 0o777)
            return real(src, dst, *r, **k)

        monkeypatch.setattr(shutil, "copyfileobj", spy)
        old = os.umask(0o022)      # without this the assertion is vacuous
        try:
            with atomic_paths(a, b) as (ta, tb):
                ta.write_text("{}", encoding="utf-8")
                tb.write_text("{}", encoding="utf-8")
        finally:
            os.umask(old)
        assert modes == [0o600, 0o600], f"a backup was written at {modes}"
        assert self._leftovers(tmp_path) == []

    @posix_only
    def test_a_restore_that_itself_fails_is_reported_as_half_applied(
            self, tmp_path, monkeypatch):
        """The state the transaction exists to prevent, reached anyway. The one
        thing worse than reaching it is reaching it silently — the caller was
        told "no changes applied" over a half-applied pair."""
        a, b = self._pair(tmp_path)
        real = os.replace
        calls = {"n": 0}

        def flaky(src, dst, *r, **k):
            calls["n"] += 1
            if calls["n"] >= 2:          # the publish of b, then the restore of a
                raise OSError(errno.EIO, "boom")
            return real(src, dst, *r, **k)

        monkeypatch.setattr(os, "replace", flaky)
        with pytest.raises(HalfApplied) as raised:
            with atomic_paths(a, b) as (ta, tb):
                ta.write_text('{"a": 9}', encoding="utf-8")
                tb.write_text('{"b": 9}', encoding="utf-8")
        assert "HALF APPLIED" in str(raised.value)
        assert raised.value.__cause__ is not None, "the publish error was lost"

    def test_the_same_target_twice_is_refused(self, tmp_path):
        """Two names for one file publish twice onto it and the second silently
        wins — a lost write wearing the shape of a completed transaction."""
        a, _ = self._pair(tmp_path)
        with pytest.raises(ValueError):
            with atomic_paths(a, a):
                pass

    @posix_only
    def test_a_filesystem_without_hardlinks_falls_back_to_a_copy(self, tmp_path,
                                                                 monkeypatch):
        """The other side of the same rule: EOPNOTSUPP is a real reason, and the
        transaction must still work where hardlinks do not exist."""
        a, b = self._pair(tmp_path)
        monkeypatch.setattr(os, "link", lambda *a_, **k: (_ for _ in ()).throw(
            OSError(errno.EOPNOTSUPP, "no hardlinks here")))
        with atomic_paths(a, b) as (ta, tb):
            ta.write_text('{"a": 9}', encoding="utf-8")
            tb.write_text('{"b": 9}', encoding="utf-8")
        assert a.read_text(encoding="utf-8") == '{"a": 9}' and b.read_text(encoding="utf-8") == '{"b": 9}'
        assert self._leftovers(tmp_path) == []

    def test_no_targets_is_refused(self):
        with pytest.raises(ValueError):
            with atomic_paths():
                pass


class TestTheOneWriter:
    """V10: five modules had hand-rolled a temp-then-rename and two more wrote
    IN PLACE — `scenario.py` and `tie_out.py` opened the real target with
    O_TRUNC, so an interrupted write left a truncated scenario, or a truncated
    tie-out report, under the name the next reader trusts. The second is the
    hard gate's own record, the artifact V8 binds a filing package to."""

    def _writer(self):
        from agents.lib.secure_fs import atomic_write, write_secure
        return atomic_write, write_secure

    @posix_only
    def test_the_file_is_owner_only_from_the_first_byte(self, tmp_path):
        """Created at 600 rather than written-then-chmod'd: a file created at
        the umask default holds the whole of its contents before anything
        tightens it, and on Windows with no ACL at all until the call returns.
        The mode is read at the moment of the RENAME, which is the only point
        where the pre-fix window is observable."""
        from agents.lib import secure_fs

        seen = {}
        real_replace = os.replace

        def spying_replace(src, dst):
            seen["mode"] = os.stat(src).st_mode & 0o777
            return real_replace(src, dst)

        atomic_write, _ = self._writer()
        old = os.umask(0o022)   # permissive, or the assertion is vacuous
        try:
            secure_fs.os.replace = spying_replace
            with atomic_write(tmp_path / "out.txt") as handle:
                handle.write("data")
        finally:
            secure_fs.os.replace = real_replace
            os.umask(old)
        assert seen["mode"] == 0o600
        assert (tmp_path / "out.txt").stat().st_mode & 0o777 == 0o600

    @posix_only
    def test_a_failed_write_leaves_the_target_as_it_was(self, tmp_path):
        atomic_write, _ = self._writer()
        target = tmp_path / "out.txt"
        target.write_text("the good copy", encoding="utf-8")
        with pytest.raises(RuntimeError):
            with atomic_write(target) as handle:
                handle.write("half of something else")
                raise RuntimeError("interrupted")
        assert target.read_text(encoding="utf-8") == "the good copy"
        assert [p.name for p in tmp_path.iterdir()] == ["out.txt"]

    @posix_only
    def test_bytes_and_text_both_round_trip(self, tmp_path):
        _, write_secure = self._writer()
        assert write_secure(tmp_path / "a.txt", "héllo").read_text(encoding="utf-8") == "héllo"
        assert write_secure(tmp_path / "b.bin", b"\r\n\x00").read_bytes() == b"\r\n\x00"

    @posix_only
    def test_the_temp_sits_beside_the_target(self, tmp_path):
        """One filesystem, so the rename is atomic — a temp in /tmp is a COPY
        across devices, which is neither atomic nor owner-only on arrival."""
        from agents.lib.secure_fs import atomic_path
        (tmp_path / "sub").mkdir()
        with atomic_path(tmp_path / "sub" / "out.txt") as tmp:
            assert tmp.parent == tmp_path / "sub"

    def test_a_failure_securing_the_temp_leaves_nothing_behind(self, tmp_path,
                                                               monkeypatch):
        """Securing the descriptor can fail — the Windows ACL path shells out —
        and it used to sit OUTSIDE the cleanup scope, so the temp stayed on
        disk to accumulate over retries. Every writer this replaced cleaned up
        in that case."""
        from agents.lib import secure_fs

        atomic_write, _ = self._writer()
        monkeypatch.setattr(secure_fs, "secure_fd",
                            lambda fd, path: (_ for _ in ()).throw(OSError("no ACL")))
        with pytest.raises(OSError, match="no ACL"):
            with atomic_write(tmp_path / "out.txt") as handle:
                handle.write("never reached")
        assert list(tmp_path.iterdir()) == []



windows_only = pytest.mark.skipif(
    os.name != "nt", reason="reads back an NTFS DACL")


@windows_only
class TestTheWindowsAclIsActuallyRestrictive:
    """The reason the windows CI job is worth running at all.

    Everything else this module asserts about Windows either stubs
    `subprocess.run` — proving the PowerShell was ASSEMBLED, never that it did
    anything — or reads back a POSIX mode NTFS does not have. So a job that ran
    the module and went green would have proved that PowerShell was invoked,
    and nothing about whether the file is actually restricted. That is the shape
    `docs` warns about elsewhere: a check that always passes and one that never
    fires look identical from outside.

    These read the DACL back with `icacls`, which is a different tool from the
    `Set-Acl` that wrote it. `_secure_path_windows` does verify its own work,
    and that verification is worth having — but a writer checking its own write
    with the same API cannot see an API that lies, and this can.
    """

    @staticmethod
    def _acl(path) -> str:
        out = subprocess.run(["icacls", str(path)], capture_output=True,
                             text=True, check=True)
        return out.stdout

    # The principals that make "owner-only" false. `Everyone` and
    # `Authenticated Users` are the broad ones; `BUILTIN\\Users` is the one a
    # default DACL grants and the one an inherited rule brings in.
    BROAD = ("Everyone", "Authenticated Users", "\\Users:", "BUILTIN\\Users")

    def test_a_secured_file_grants_no_broad_principal(self, tmp_path):
        target = tmp_path / "result.json"
        target.write_text("{}", encoding="utf-8")
        secure_path(str(target))
        acl = self._acl(target)
        for principal in self.BROAD:
            assert principal not in acl, f"{principal} still has access:\n{acl}"

    def test_a_secured_file_carries_no_INHERITED_rule(self, tmp_path):
        """`(I)` marks an inherited ACE. The DACL is protected, so there must be
        none: an inherited grant from the parent is exactly the access
        `SetAccessRuleProtection` exists to cut off, and it would not show up in
        a check that only looked at the explicit rules."""
        target = tmp_path / "answers.md"
        target.write_text("nothing sensitive\n", encoding="utf-8")
        secure_path(str(target))
        assert "(I)" not in self._acl(target)

    def test_a_secured_directory_is_INHERITABLE(self, tmp_path):
        """`(OI)(CI)` — object and container inherit. Without them a protected
        directory strips the inherited ACEs off what already lives inside it,
        leaving a summaries/*.md with an EMPTY DACL, its owner locked out; and
        anything created afterwards falls back to the creating token's default
        DACL, which grants SYSTEM and Administrators. POSIX 0700 covers every
        child by denying traversal, and this is what has to stand in for it."""
        target = tmp_path / "summaries"
        target.mkdir()
        secure_path(str(target))
        acl = self._acl(target)
        assert "(OI)" in acl and "(CI)" in acl, acl

    def test_a_file_created_inside_a_secured_directory_is_covered_too(self, tmp_path):
        """The property the inheritance flags are FOR, asserted end to end
        rather than through the flags that are supposed to produce it."""
        directory = tmp_path / "reviews"
        directory.mkdir()
        secure_path(str(directory))
        child = directory / "2026-review.md"
        child.write_text("workpaper\n", encoding="utf-8")
        acl = self._acl(child)
        for principal in self.BROAD:
            assert principal not in acl, f"{principal} reaches a child:\n{acl}"

    def test_a_bare_chmod_changes_the_ACL_not_at_all(self, tmp_path):
        """AGENTS.md §1 says `chmod` on Windows only flips the read-only DOS
        attribute and leaves NTFS untouched. That claim is the whole reason the
        `os.name == "nt"` branch exists, and nothing had ever checked it on the
        platform it is about.

        Stated as "chmod changes nothing, secure_path changes something" rather
        than as "a broad principal loses access", because a per-user temp
        directory on a runner already grants no broad principal — the first
        spelling asserted a premise that is false on the very host the job runs
        on, and would have been a test that could only fail."""
        target = tmp_path / "profile.json"
        target.write_text("{}", encoding="utf-8")
        before = self._acl(target)
        os.chmod(str(target), 0o600)
        assert self._acl(target) == before, (
            "chmod altered the DACL, which would mean AGENTS.md §1's reason for "
            "this whole branch is wrong")
        secure_path(str(target))
        assert self._acl(target) != before, (
            "secure_path left the DACL exactly as chmod found it")
        for principal in self.BROAD:
            assert principal not in self._acl(target)


def test_the_execution_policy_is_bypassed_for_the_windows_acl_call():
    """Deliberately NOT inside the Windows-only class above.

    A restrictive execution policy is one of the two things that can stop the
    ACL script, and its absence is invisible on POSIX and fatal on Windows. The
    OTHER one is why this module no longer calls `Get-Acl`/`Set-Acl` at all: a
    stock windows-latest runner could not autoload
    `Microsoft.PowerShell.Security` — `CouldNotAutoloadMatchingModule` — and
    bypassing the policy did NOT fix that, measured. A guard that only runs on
    the platform where the bug is already obvious is a guard that lets it back
    in everywhere else, so this one runs everywhere.
    """
    source = (Path(__file__).resolve().parent.parent
              / "agents" / "lib" / "secure_fs.py").read_text(encoding="utf-8")
    assert '"-ExecutionPolicy", "Bypass"' in source, (
        "the PowerShell that sets the owner-only DACL is subject to the host's "
        "execution policy again; on a stock Windows host nothing can be "
        "restricted at all and AGENTS.md §1 does not hold there")
