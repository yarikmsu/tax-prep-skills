"""Content-level PII scan — the commit-time guard for full SSNs.

The filename/extension scan in the same script blocks the usual PII carriers
(PDF, XLSX, CSV, taxpayer_profile.json, answers.md, reviews/). This covers the
residual case: a full SSN typed into an otherwise-innocent tracked file, which
no other gate in the repository sees.

Each test drives the real script inside a throwaway git repo, because the
behaviour under test is a git-diff interaction that a unit test cannot reach.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCAN = REPO / "scripts" / "pre-commit-pii-scan.sh"

# Assembled at runtime, never written as a literal.
#
# A test for a PII scanner cannot contain PII-shaped literals: this file would
# trip the very scan it exercises, and the obvious workaround — adding the value
# to .pii-allowlist — would make the scanner skip it everywhere AND make
# test_full_ssn_in_an_added_line_blocks fail, because these tests copy the real
# allowlist into the throwaway repo. Splitting the value keeps the scanner at
# full strength and the test honest.
_SSN_AREA, _SSN_GROUP, _SSN_SERIAL = "123", "45", "6789"
FAKE_SSN = f"{_SSN_AREA}-{_SSN_GROUP}-{_SSN_SERIAL}"

# AWS's own documentation example key, split for the same reason.
FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


def _make_repo(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    """A throwaway git repo carrying a copy of the scanner and the allowlist."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"],
                   cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "scripts").mkdir()
    dest = tmp_path / "scripts" / "pre-commit-pii-scan.sh"
    shutil.copy(SCAN, dest)
    os.chmod(dest, 0o755)
    shutil.copy(REPO / ".pii-allowlist", tmp_path / ".pii-allowlist")
    # STAGED, because the scanner has no working-tree fallback: an exemption
    # only counts if it is part of the snapshot being committed. This mirrors
    # the real repository, where .pii-allowlist is tracked.
    subprocess.run(["git", "add", ".pii-allowlist"], cwd=tmp_path, check=True)
    return tmp_path


def _stage(repo: Path, name: str, content: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    subprocess.run(["git", "add", name], cwd=repo, check=True)


def _run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh"],
                          cwd=repo, capture_output=True, text=True)


def test_full_ssn_in_an_added_line_blocks(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md", f"Taxpayer SSN: {FAKE_SSN}\n")
    assert _run(repo).returncode == 1


def test_block_message_never_prints_the_matched_value(tmp_path):
    """Printing the match would put the SSN into terminal scrollback and CI logs,
    which is the exact disclosure the scanner exists to prevent."""
    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md", f"Taxpayer SSN: {FAKE_SSN}\n")
    result = _run(repo)
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert FAKE_SSN not in combined, "scanner echoed the value it was guarding"
    assert "notes.md" in combined, "scanner must name the offending file"


def test_masked_last_four_format_passes(tmp_path):
    """XXX-XX-1234 is the ALLOWED storage format and must never block."""
    repo = _make_repo(tmp_path)
    _stage(repo, "profile.md", 'ssn_last4: "XXX-XX-6789"\n')
    assert _run(repo).returncode == 0


def test_allowlisted_example_ssn_passes(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "docs.md", "Example only: 000-00-0000\n")
    assert _run(repo).returncode == 0


def test_ssn_under_tests_fixtures_still_blocks(tmp_path):
    """tests/fixtures/ is exempt from the FILENAME scan, not the content scan —
    a real SSN in a synthetic fixture is exactly the mistake this catches."""
    repo = _make_repo(tmp_path)
    _stage(repo, "tests/fixtures/result.json", f'{{"ssn": "{FAKE_SSN}"}}\n')
    assert _run(repo).returncode == 1


def test_aws_access_key_blocks(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "config.py", f'KEY = "{FAKE_AWS_KEY}"\n')
    assert _run(repo).returncode == 1


def test_preexisting_content_in_an_unmodified_region_does_not_block(tmp_path):
    """Only ADDED lines are scanned, so editing a file elsewhere stays committable."""
    repo = _make_repo(tmp_path)
    _stage(repo, "legacy.md", f"old: {FAKE_SSN}\n")
    subprocess.run(["git", "commit", "-q", "--no-verify", "-m", "seed"],
                   cwd=repo, check=True)
    (repo / "legacy.md").write_text(f"old: {FAKE_SSN}\nnew innocent line\n")
    subprocess.run(["git", "add", "legacy.md"], cwd=repo, check=True)
    assert _run(repo).returncode == 0


def test_tax_figures_and_form_numbers_do_not_false_positive(tmp_path):
    """Dollar amounts, form numbers and ISO dates saturate this repository. A
    scanner that flags them gets bypassed with --no-verify as a reflex, which
    costs more protection than the extra patterns would ever buy."""
    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md",
           "Form 1099-DIV line 1a $400 due 2026-04-15, phone 555-123-4567\n")
    assert _run(repo).returncode == 0


def test_clean_diff_passes(tmp_path):
    """The case that catches a scanner aborting under `set -e` rather than
    blocking: an aborted script exits non-zero with no message, which is
    indistinguishable from a legitimate block."""
    repo = _make_repo(tmp_path)
    _stage(repo, "readme.md", "Nothing sensitive here.\n")
    result = _run(repo)
    assert result.returncode == 0, (
        f"clean diff rejected; stdout={result.stdout!r} stderr={result.stderr!r}")


# --- Review findings (PR #67, round 1) -------------------------------------

# Modern project-scoped keys carry hyphens INSIDE the secret, which a
# [A-Za-z0-9]{20,} class terminates at. Assembled from parts, as above.
FAKE_OPENAI_KEY = "sk-" + "proj-" + "b7Qx" * 6


def test_allowlisted_value_on_the_same_line_does_not_mask_a_real_ssn(tmp_path):
    """The allowlist must exempt individual MATCHES, not whole lines.

    `XXX-XX-` is allowlisted so the masked storage format never trips the scan.
    Filtering whole lines meant any line that also carried the masked form
    smuggled a real SSN past the gate — and a taxpayer profile line showing both
    is the single most likely place for that to happen.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "profile.md", f"masked XXX-XX-6789 alongside real {FAKE_SSN}\n")
    assert _run(repo).returncode == 1


def test_project_scoped_api_key_blocks(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "config.py", f'KEY = "{FAKE_OPENAI_KEY}"\n')
    assert _run(repo).returncode == 1


def test_non_ascii_filename_is_still_content_scanned(tmp_path):
    """`git diff --name-only` QUOTES a non-ASCII path, and the quoted form is
    not a valid pathspec — so the file's added lines were never scanned. This
    toolkit exists to ingest foreign-language documents."""
    repo = _make_repo(tmp_path)
    _stage(repo, "relevé.md", f"SSN: {FAKE_SSN}\n")
    assert _run(repo).returncode == 1


def test_non_ascii_filename_is_still_blocked_by_extension(tmp_path):
    """The same quoting defect in the PRE-EXISTING filename scan: basename() of
    a quoted path ends in `.pdf"`, which the `\\.(pdf|...)$` rule does not match."""
    repo = _make_repo(tmp_path)
    _stage(repo, "relevé.pdf", "x")
    assert _run(repo).returncode == 1


def test_binary_classified_file_is_blocked_rather_than_skipped(tmp_path):
    """git emits no `+` lines for a blob it classifies as binary, so an
    added-lines scan reads it as clean. A UTF-16 notes.txt holding an SSN is the
    concrete case. Absence of scannable content must fail closed, not open."""
    repo = _make_repo(tmp_path)
    (repo / "notes.txt").write_bytes(f"SSN: {FAKE_SSN}\n".encode("utf-16"))
    subprocess.run(["git", "add", "notes.txt"], cwd=repo, check=True)
    assert _run(repo).returncode == 1


def _commit(repo: Path, msg: str) -> str:
    subprocess.run(["git", "commit", "-q", "--no-verify", "-m", msg],
                   cwd=repo, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()


def test_range_mode_applies_the_same_rules(tmp_path):
    """CI passes `<base>...<head>` instead of scanning the index.

    CI is the only gate left for a contributor who used --no-verify, so its
    rules must be the hook's rules. They used to be hand-mirrored in ci.yml,
    which is how four bypasses shipped to both copies simultaneously.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "nothing here\n")
    base = _commit(repo, "base")
    _stage(repo, "leak.md", f"SSN: {FAKE_SSN}\n")
    _commit(repo, "leak")

    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{base}...HEAD"],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 1
    assert FAKE_SSN not in r.stdout + r.stderr
    assert "CI BLOCKED" in r.stdout, "range mode must not say COMMIT BLOCKED"
    # die() shares the "CI BLOCKED" banner, so the assertions above are equally
    # satisfied by a scan that crashed before reading a single line — verified by
    # forcing merge-base to fail. Naming the offending file is what separates
    # "found it" from "never looked".
    assert "leak.md" in r.stdout, "must name the file it detected, not merely fail"
    assert "BLOCKED CONTENT" in r.stdout


def test_range_mode_passes_on_a_clean_range(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "nothing here\n")
    base = _commit(repo, "base")
    _stage(repo, "more.md", "still nothing\n")
    _commit(repo, "more")

    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{base}...HEAD"],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"


def test_ci_invokes_the_shared_script_rather_than_reimplementing_it(tmp_path):
    """AGENTS.md used to warn that these rules were duplicated and had to change
    together. Calling one script removes the class of drift entirely — this test
    stops the duplication from creeping back."""
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "bash scripts/pre-commit-pii-scan.sh" in ci, (
        "CI must call the shared scanner")
    assert "BLOCKED_EXTENSIONS=" not in ci, (
        "CI is re-implementing the file-type rules instead of calling the script")


def test_range_mode_reports_coverage_so_a_clean_pass_is_evidence(tmp_path):
    """In CI the log is the only record this gate ran. A silent exit 0 there is
    indistinguishable from a scan that examined nothing — a false pass that
    looks exactly like a real one."""
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "nothing here\n")
    base = _commit(repo, "base")
    _stage(repo, "a.md", "still nothing\n")
    _stage(repo, "b.md", "also nothing\n")
    _commit(repo, "more")

    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{base}...HEAD"],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0
    assert "2 changed file(s)" in r.stdout, (
        f"range mode must state what it covered; got {r.stdout!r}")


def test_hook_mode_stays_silent_on_success(tmp_path):
    """The git hook runs on every commit; a success banner there is noise."""
    repo = _make_repo(tmp_path)
    _stage(repo, "readme.md", "nothing here\n")
    r = _run(repo)
    assert r.returncode == 0
    assert r.stdout.strip() == "", f"hook should be silent, got {r.stdout!r}"


# --- Review findings (PR #67, round 2) -------------------------------------

FAKE_GH_PAT = "github_pat_" + "1A" * 26


def test_range_mode_scans_each_commit_even_when_a_later_one_deletes_the_secret(tmp_path):
    """`git diff base...head` is the NET tree diff. A secret added in one commit
    and removed in the next vanishes from it while remaining in the pushed
    history — and catching exactly that is why CI runs this at all."""
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "nothing\n")
    base = _commit(repo, "base")
    _stage(repo, "leak.md", f"SSN: {FAKE_SSN}\n")
    _commit(repo, "adds the secret")
    subprocess.run(["git", "rm", "-q", "leak.md"], cwd=repo, check=True)
    _commit(repo, "removes it again")

    net = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"],
                         cwd=repo, capture_output=True, text=True).stdout
    assert "leak.md" not in net, "precondition: the net diff must look clean"

    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{base}...HEAD"],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 1, "secret still reachable in history must be caught"
    assert FAKE_SSN not in r.stdout + r.stderr


def test_bare_nine_digit_ssn_blocks(tmp_path):
    """Nine contiguous digits is a full SSN — a plausible result of manual entry
    or document extraction."""
    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md", f"SSN {_SSN_AREA}{_SSN_GROUP}{_SSN_SERIAL}\n")
    assert _run(repo).returncode == 1


def test_space_separated_ssn_blocks(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md", f"SSN {_SSN_AREA} {_SSN_GROUP} {_SSN_SERIAL}\n")
    assert _run(repo).returncode == 1


def test_longer_digit_runs_do_not_false_positive(tmp_path):
    """Only tokens that are EXACTLY nine digits count, so account numbers,
    phone numbers and identifiers of other lengths stay committable."""
    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md",
           "acct 12345678901234 phone 5551234567 id 12345678 epoch 1767225600\n")
    r = _run(repo)
    assert r.returncode == 0, f"false positive: {r.stdout!r}"


def test_fine_grained_github_token_blocks(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "config.py", f'TOKEN = "{FAKE_GH_PAT}"\n')
    assert _run(repo).returncode == 1


def test_added_line_beginning_with_two_plus_signs_is_scanned(tmp_path):
    """Content starting with `++ ` renders as `+++ ` in the patch, which a
    header heuristic discards. --output-indicator-new separates them properly."""
    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md", f"++ diff-looking line with {FAKE_SSN}\n")
    assert _run(repo).returncode == 1


def test_unstaged_allowlist_edit_does_not_exempt_a_staged_secret(tmp_path):
    """The allowlist must come from the snapshot being committed. A working-tree
    edit that was never staged must not silently widen the exemption."""
    repo = _make_repo(tmp_path)
    subprocess.run(["git", "add", ".pii-allowlist"], cwd=repo, check=True)
    _commit(repo, "track the allowlist")

    # Local, UNSTAGED exemption for the value we are about to commit.
    with (repo / ".pii-allowlist").open("a") as fh:
        fh.write(f"{FAKE_SSN}\n")
    _stage(repo, "leak.md", f"SSN: {FAKE_SSN}\n")

    assert _run(repo).returncode == 1, (
        "an unstaged allowlist edit exempted a staged secret")


def test_staged_allowlist_entry_does_exempt(tmp_path):
    """The complement: once the exemption is actually staged, it applies."""
    repo = _make_repo(tmp_path)
    with (repo / ".pii-allowlist").open("a") as fh:
        fh.write(f"{FAKE_SSN}\n")
    subprocess.run(["git", "add", ".pii-allowlist"], cwd=repo, check=True)
    _stage(repo, "leak.md", f"SSN: {FAKE_SSN}\n")
    assert _run(repo).returncode == 0


def test_unusable_base_ref_blocks_rather_than_reporting_clean(tmp_path):
    """A base commit CI never fetched must not produce an empty file list that
    reads as a clean scan."""
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "nothing\n")
    _commit(repo, "base")
    r = subprocess.run(
        ["bash", "scripts/pre-commit-pii-scan.sh", "0" * 40 + "...HEAD"],
        cwd=repo, capture_output=True, text=True)
    assert r.returncode == 1
    assert "did not happen" in r.stdout, f"got {r.stdout!r}"


def test_runtime_workspace_directories_are_blocked(tmp_path):
    """README promises the hooks block these. A text artifact under summaries/
    or documents/ carries financial data the content patterns cannot recognise,
    and `git add -f` bypasses .gitignore."""
    for path in ("summaries/w2.md", "documents/notes.txt", "scenarios/a.json",
                 "reviews/2025-review.md"):
        repo = _make_repo(tmp_path / path.replace("/", "_"))
        _stage(repo, path, "ordinary text\n")
        assert _run(repo).returncode == 1, f"{path} was not blocked"


def test_documents_gitkeep_stays_committable(tmp_path):
    """documents/.gitkeep is tracked on purpose — it is what makes the directory
    exist in a fresh clone."""
    repo = _make_repo(tmp_path)
    _stage(repo, "documents/.gitkeep", "")
    assert _run(repo).returncode == 0


def test_fixture_under_an_archive_path_is_not_blocked_by_the_path_rule(tmp_path):
    """tests/fixtures/ takes precedence over the runtime-workspace path rules —
    real fixtures live at tests/fixtures/projection/archive/2025/result.json,
    and both the `archive/` path rule and the `result.json` filename rule would
    otherwise reject them."""
    repo = _make_repo(tmp_path)
    _stage(repo, "tests/fixtures/projection/archive/2025/result.json",
           '{"line_24": 100}\n')
    assert _run(repo).returncode == 0


# --- Round 3: four bypasses in the scan's own scoping ------------------------
#
# Each of these reached HEAD with the scanner reporting clean. They share one
# shape: the scan asked git for a subset of the change, and the subset had a
# hole. A pattern is only as good as the text it is run against.

# AWS's STS temporary-credential prefix, and AWS's own documented example secret
# access key. Split for the reason given at the top of this file.
FAKE_STS_KEY = "ASIA" + "IOSFODNN7EXAMPLE"
FAKE_AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/" + "bPxRfiCYEXAMPLEKEY"


def test_merge_conflict_resolution_is_scanned(tmp_path):
    """A merge commit's conflict resolution exists in NEITHER parent.

    `--no-merges` dropped the only commit that introduced it, so both parents
    scanned clean while the value sat in HEAD. Scanning a merge against its
    first parent restores coverage: content from the merged-in branch is
    re-examined (already scanned via its own commits, so no new false
    positives) and the resolution itself is finally seen.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "seed\n")
    base = _commit(repo, "base")

    _stage(repo, "conflict.md", "alpha\n")
    _commit(repo, "ours")

    subprocess.run(["git", "checkout", "-q", "-b", "side", base], cwd=repo, check=True)
    _stage(repo, "conflict.md", "beta\n")
    _commit(repo, "theirs")

    subprocess.run(["git", "checkout", "-q", "-"], cwd=repo, check=True)
    subprocess.run(["git", "merge", "--no-commit", "side"],
                   cwd=repo, capture_output=True)
    _stage(repo, "conflict.md", f"resolved SSN: {FAKE_SSN}\n")
    _commit(repo, "merge side")

    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{base}...HEAD"],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 1, "merge-resolution content escaped the scan"
    assert FAKE_SSN not in r.stdout + r.stderr


def test_allowlist_is_resolved_per_commit_not_from_head(tmp_path):
    """Allowlisting a value later must not retroactively exempt the commit that
    leaked it — that history is still pushed and still exposed."""
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "seed\n")
    base = _commit(repo, "base")

    _stage(repo, "leak.md", f"SSN: {FAKE_SSN}\n")
    _commit(repo, "leak")

    allow = repo / ".pii-allowlist"
    allow.write_text(allow.read_text() + FAKE_SSN + "\n")
    subprocess.run(["git", "add", ".pii-allowlist"], cwd=repo, check=True)
    _commit(repo, "allowlist it after the fact")

    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{base}...HEAD"],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 1, "a later allowlist entry exempted an earlier leak"
    assert "leak.md" in r.stdout


def test_temporary_aws_access_key_blocks(tmp_path):
    """ASIA is the STS temporary-credential prefix. Short-lived is not harmless:
    the window is hours, and the repository is the wrong place to spend it."""
    repo = _make_repo(tmp_path)
    _stage(repo, "config.env", f"AWS_ACCESS_KEY_ID={FAKE_STS_KEY}\n")
    assert _run(repo).returncode == 1


def test_aws_secret_access_key_blocks_when_named(tmp_path):
    """A secret access key is 40 unremarkable base64 characters — matching it
    bare would flag every hash in the repository. The assignment keyword is what
    makes it identifiable, so the pattern requires it."""
    repo = _make_repo(tmp_path)
    _stage(repo, "config.env", f"aws_secret_access_key = {FAKE_AWS_SECRET}\n")
    assert _run(repo).returncode == 1


def test_bare_forty_char_token_does_not_false_positive(tmp_path):
    """The counterweight to the test above: an unlabelled 40-char string is a
    git SHA, a checksum, or a base64 blob far more often than a credential."""
    repo = _make_repo(tmp_path)
    _stage(repo, "checksums.txt", f"integrity = {FAKE_AWS_SECRET}\n")
    assert _run(repo).returncode == 0


def test_symlink_replaced_by_a_file_is_scanned(tmp_path):
    """Replacing a symlink with a regular file is git status T, which the
    ACMR filter excluded before either the filename or the content check ran."""
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "seed\n")
    subprocess.run(["ln", "-s", "seed.md", "link.md"], cwd=repo, check=True)
    subprocess.run(["git", "add", "link.md"], cwd=repo, check=True)
    _commit(repo, "seed and symlink")

    (repo / "link.md").unlink()
    _stage(repo, "link.md", f"SSN: {FAKE_SSN}\n")
    assert _run(repo).returncode == 1, "typechange bypassed the scan"


def test_symlink_replaced_by_a_blocked_file_type_is_blocked(tmp_path):
    """The same typechange hole in the FILENAME rules, not just the content
    scan — a symlink becoming secrets.pdf must still be rejected."""
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "seed\n")
    subprocess.run(["ln", "-s", "seed.md", "notes.pdf"], cwd=repo, check=True)
    subprocess.run(["git", "add", "notes.pdf"], cwd=repo, check=True)
    _commit(repo, "seed and symlink")

    (repo / "notes.pdf").unlink()
    _stage(repo, "notes.pdf", "harmless text\n")
    assert _run(repo).returncode == 1, "typechange bypassed the filename rules"


# --- Round 4: exemptions and pathnames --------------------------------------
#
# Round 3's theme was "the scan asked git for a subset with a hole". Round 4's
# is narrower and sharper: every EXEMPTION is an attack surface, and the
# pathname is content the content scan can never see.


def test_unstaged_allowlist_after_a_staged_deletion_does_not_exempt(tmp_path):
    """The working-tree fallback was guarded by "only when untracked" — and
    staging a DELETION of the tracked file satisfies that guard, re-enabling
    the fallback for an allowlist the committed snapshot never contained."""
    repo = _make_repo(tmp_path)
    _commit(repo, "track the allowlist")

    subprocess.run(["git", "rm", "-q", "--cached", ".pii-allowlist"],
                   cwd=repo, check=True)
    (repo / ".pii-allowlist").write_text(f"{FAKE_SSN}\n")
    _stage(repo, "leak.md", f"SSN: {FAKE_SSN}\n")

    assert _run(repo).returncode == 1, "an unstaged allowlist granted an exemption"


def test_ssn_in_a_pathname_blocks(tmp_path):
    """The filename rules know extensions and known names; the content rules
    read added lines. Neither looks at the path itself, so a file NAMED after
    an SSN committed cleanly with entirely innocent contents."""
    repo = _make_repo(tmp_path)
    _stage(repo, f"reports/{FAKE_SSN}.txt", "nothing sensitive in here\n")
    assert _run(repo).returncode == 1


def test_block_message_redacts_a_sensitive_pathname(tmp_path):
    """Naming the offending file is how the message is useful — but when the
    NAME is the secret, printing it performs the disclosure being prevented."""
    repo = _make_repo(tmp_path)
    _stage(repo, f"reports/{FAKE_SSN}.txt", "nothing sensitive in here\n")
    result = _run(repo)
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert FAKE_SSN not in combined, "scanner echoed the sensitive pathname"
    assert "[REDACTED]" in combined, "redaction must leave a visible marker"
    assert "reports/" in combined, "the non-sensitive part must still be shown"


def test_redaction_reaches_the_other_block_messages(tmp_path):
    """`BLOCKED FILE TYPE` and `BLOCKED PATH` are separate code paths printing
    separate messages, and each must redact the name it prints.

    Reaching them needs care: the pathname scan runs FIRST and `continue`s, so a
    real SSN in a filename never gets that far — an earlier version of this test
    used one and silently re-tested the pathname branch instead. An ALLOWLISTED
    value is the fixture that works: `count_hits` filters it to zero so the file
    falls through to the filename rules, while `redact` (which does not consult
    the allowlist) still rewrites it, proving redaction is applied there.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "000-00-0000.pdf", "x\n")
    by_extension = _run(repo)
    assert by_extension.returncode == 1
    assert "BLOCKED FILE TYPE" in by_extension.stdout, "wrong branch under test"
    assert "000-00-0000" not in by_extension.stdout, "extension branch printed it raw"

    repo2 = _make_repo(tmp_path / "workspace")
    (repo2 / "summaries").mkdir()
    (repo2 / "summaries" / "000-00-0000.md").write_text("x\n")
    subprocess.run(["git", "add", "-f", "summaries/000-00-0000.md"],
                   cwd=repo2, check=True)
    by_path = _run(repo2)
    assert by_path.returncode == 1
    assert "BLOCKED PATH" in by_path.stdout, "wrong branch under test"
    assert "000-00-0000" not in by_path.stdout, "workspace-path branch printed it raw"


def test_ordinary_pathnames_are_not_redacted(tmp_path):
    """The counterweight: redaction must not mangle the ordinary case, or the
    message stops naming the file it is meant to identify."""
    repo = _make_repo(tmp_path)
    _stage(repo, "quarterly-report-2026.pdf", "x\n")
    result = _run(repo)
    assert result.returncode == 1
    # Scoped to the line that NAMES the file: the standing explanatory line
    # mentions [REDACTED] on every block, so asserting over the whole message
    # would pass for the wrong reason.
    named = [ln for ln in result.stdout.splitlines() if "BLOCKED" in ln and ".pdf" in ln]
    assert named, f"no file-naming line found in:\n{result.stdout}"
    assert "quarterly-report-2026.pdf" in named[0]
    assert "[REDACTED]" not in named[0]


def test_an_ssn_inside_a_longer_digit_run_is_still_redacted(tmp_path):
    """The redactor matches nine digits ANYWHERE, not only as an isolated token.

    Narrowing it to the detector's token semantics would make the block message
    for `acct1234567890.pdf` print all ten digits verbatim — and the first nine
    may be an SSN. That is the detected-but-not-redacted shape that turns the
    gate into a disclosure channel, so the breadth is deliberate: over-redaction
    costs legibility, under-redaction discloses.
    """
    repo = _make_repo(tmp_path)
    nine = f"{_SSN_AREA}{_SSN_GROUP}{_SSN_SERIAL}"
    _stage(repo, f"acct{nine}0.pdf", "x\n")
    result = _run(repo)
    assert result.returncode == 1
    assert nine not in result.stdout, "an SSN inside a longer run was printed"


def test_a_mangled_name_is_explained_rather_than_left_confusing(tmp_path):
    """The cost of that breadth: a legitimate `statement_202604151234.pdf` is
    named in the block message under a filename that does not exist. The message
    must say so and point at the authoritative list, or the user hunts for a
    file that was never there."""
    repo = _make_repo(tmp_path)
    _stage(repo, "statement_202604151234.pdf", "x\n")
    result = _run(repo)
    assert result.returncode == 1
    # The load-bearing assertion. Both checks below match the STANDING FOOTER
    # printed on every block, so they pass even with redact() stubbed to a no-op
    # — verified by mutation. Only the absence of the digit run proves the name
    # was mangled, which is what makes the explanation necessary at all.
    assert "202604151234" not in result.stdout, (
        "the digit run reached the message unredacted — an SSN embedded in a "
        "longer number would reach it the same way")
    assert "[REDACTED]" in result.stdout
    assert "git diff --cached --name-only" in result.stdout, (
        "a redacted name must come with a way to find the real one")


def test_gitkeep_exemption_covers_the_path_rule_only(tmp_path):
    """documents/.gitkeep is exempt from the `documents/` path rule because it
    is tracked on purpose. It used to `continue` before the content scan too,
    so editing it to hold an SSN was a clean commit."""
    repo = _make_repo(tmp_path)
    _stage(repo, "documents/.gitkeep", f"SSN: {FAKE_SSN}\n")
    assert _run(repo).returncode == 1, ".gitkeep skipped the content scan"


def test_empty_gitkeep_stays_committable(tmp_path):
    """The exemption itself must survive the fix — an empty .gitkeep is what
    makes documents/ exist in a fresh clone."""
    repo = _make_repo(tmp_path)
    _stage(repo, "documents/.gitkeep", "")
    assert _run(repo).returncode == 0


# --- Round 5: the AWS rule, and the detector/redactor split ------------------

# 40 characters, no slash, so it can appear inside a pathname.
FAKE_AWS_SECRET_PATHSAFE = "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEYab"
assert len(FAKE_AWS_SECRET_PATHSAFE) == 40


def test_aws_secret_with_yaml_aligned_spacing_blocks(tmp_path):
    """YAML aligns values with arbitrary runs of spaces. A `{1,4}` separator
    limit let a five-space gap carry the credential straight through — and the
    bare value deliberately matches nothing else, so nothing caught it."""
    repo = _make_repo(tmp_path)
    _stage(repo, "config.yml", f"aws_secret_access_key:      {FAKE_AWS_SECRET}\n")
    assert _run(repo).returncode == 1


def test_aws_secret_with_hyphenated_key_name_blocks(tmp_path):
    """`aws-secret-access-key` is as conventional as the underscored spelling in
    YAML and CLI config."""
    repo = _make_repo(tmp_path)
    _stage(repo, "config.yml", f"aws-secret-access-key: {FAKE_AWS_SECRET}\n")
    assert _run(repo).returncode == 1


def test_aws_secret_key_name_is_matched_case_insensitively(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "config.env", f"AWS_SECRET_ACCESS_KEY={FAKE_AWS_SECRET}\n")
    assert _run(repo).returncode == 1


def test_every_detected_pattern_family_is_also_redacted(tmp_path):
    """The detector and the redactor were separate lists for one round, and the
    AWS rule went into the detector alone — so the scanner blocked a path
    holding an AWS secret and then printed the secret in the block message.

    A rule that is detected but not redacted turns the gate into a disclosure
    channel, which is strictly worse than not having the rule. This walks every
    family through the pathname route, where the value is guaranteed to reach
    the message.
    """
    families = {
        "ssn": FAKE_SSN,
        "aws-access-key-id": FAKE_AWS_KEY,
        "openai-key": FAKE_OPENAI_KEY,
        "aws-secret-access-key": f"aws_secret_access_key={FAKE_AWS_SECRET_PATHSAFE}",
    }
    for name, value in families.items():
        repo = _make_repo(tmp_path / name)
        _stage(repo, f"reports/{value}.txt", "innocent contents\n")
        result = _run(repo)
        assert result.returncode == 1, f"{name}: pathname not blocked"
        combined = result.stdout + result.stderr
        assert value not in combined, f"{name}: block message echoed the value"
        assert "[REDACTED]" in combined, f"{name}: no redaction marker"


# --- Round 6: the environment, not the diff ---------------------------------
#
# These share a premise the earlier rounds did not question: that `git` returns
# what the script expects. Local configuration can change the output format, and
# a failure inside a process substitution is not the loop's failure.


def _config(repo: Path, key: str, value: str) -> None:
    subprocess.run(["git", "config", key, value], cwd=repo, check=True)


def test_color_ui_always_does_not_bypass_the_content_scan(tmp_path):
    """`color.ui=always` wraps added lines in ANSI escapes, so they no longer
    begin with the literal marker the parser looks for and `added` comes back
    empty. A developer's display preference must not disable a security gate."""
    repo = _make_repo(tmp_path)
    _config(repo, "color.ui", "always")
    _stage(repo, "notes.md", f"SSN: {FAKE_SSN}\n")
    assert _run(repo).returncode == 1


def test_external_diff_driver_does_not_bypass_the_content_scan(tmp_path):
    """`diff.external` replaces git's patch output entirely with the program's."""
    repo = _make_repo(tmp_path)
    _config(repo, "diff.external", "/usr/bin/true")
    _stage(repo, "notes.md", f"SSN: {FAKE_SSN}\n")
    assert _run(repo).returncode == 1


def test_textconv_driver_does_not_bypass_the_content_scan(tmp_path):
    """A textconv driver rewrites the blob before diffing, so the added lines
    the scanner sees are the driver's output rather than what is committed."""
    repo = _make_repo(tmp_path)
    (repo / ".gitattributes").write_text("* diff=strip\n")
    _config(repo, "diff.strip.textconv", "/usr/bin/true")
    _stage(repo, "notes.md", f"SSN: {FAKE_SSN}\n")
    assert _run(repo).returncode == 1


def test_nine_digit_ssn_adjacent_to_an_underscore_blocks(tmp_path):
    """A path of the form `W2_<nine digits>.txt` — the token class included
    `_`, so the label and the digits came out as ONE token and the
    exactly-nine-digits test rejected it. Underscore is a separator in every
    naming convention that produces this.

    (The example is described rather than spelled: writing it out makes this
    docstring a bare nine-digit SSN, which the fixed scanner rightly blocks.)
    """
    repo = _make_repo(tmp_path)
    _stage(repo, f"W2_{_SSN_AREA}{_SSN_GROUP}{_SSN_SERIAL}.txt", "innocent\n")
    assert _run(repo).returncode == 1


def test_nine_digit_ssn_adjacent_to_an_underscore_blocks_in_content(tmp_path):
    """The same hole in the added-line scan, not only the pathname."""
    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md", f"taxpayer_{_SSN_AREA}{_SSN_GROUP}{_SSN_SERIAL}\n")
    assert _run(repo).returncode == 1


def test_failing_rev_list_blocks_rather_than_reporting_clean(tmp_path):
    """`done < <(git rev-list ...) || die` checks the WHILE loop's status, not
    git's — a process substitution's failure never reaches it. An empty SCOPES
    then exits 0, so CI passes having inspected nothing.

    Injected with a `git` shim that fails only for rev-list, which is the one
    way to reproduce it deterministically.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "seed\n")
    base = _commit(repo, "base")
    _stage(repo, "leak.md", f"SSN: {FAKE_SSN}\n")
    _commit(repo, "leak")

    real_git = shutil.which("git")
    shim_dir = repo / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        f'#!/bin/bash\nif [ "$1" = "rev-list" ]; then exit 1; fi\n'
        f'exec {real_git} "$@"\n')
    os.chmod(shim, 0o755)

    env = {**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}"}
    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{base}...HEAD"],
                       cwd=repo, capture_output=True, text=True, env=env)
    assert r.returncode == 1, "a failed rev-list reported a clean scan"
    assert "could not run" in r.stdout, "must report the scan as not performed"


# --- Self-review round: option injection via the pathname --------------------


def test_leading_dash_pathname_does_not_abort_the_scan(tmp_path):
    """`basename "$file"` parses a leading-dash NAME as an OPTION.

    `--version.pdf` made basename exit non-zero with a raw "illegal option"
    message, and `set -e` aborted the whole scan before any banner. That is
    fail-closed, so nothing leaked — but a refusal that does not explain itself
    is indistinguishable from a crash, and this script's contract is that every
    block says why. The file must be blocked ON ITS MERITS instead.
    """
    repo = _make_repo(tmp_path)
    (repo / "--version.pdf").write_text("taxpayer data\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    result = _run(repo)
    assert result.returncode == 1
    assert "illegal option" not in result.stderr, "basename parsed the name as a flag"
    assert "BLOCKED FILE TYPE" in result.stdout, (
        "the .pdf rule must be what rejects it, with a real banner")


def test_leading_dash_pathname_is_content_scanned(tmp_path):
    """The same path with a permitted extension must reach the content scan
    rather than aborting before it."""
    repo = _make_repo(tmp_path)
    (repo / "--notes.md").write_text(f"SSN: {FAKE_SSN}\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    result = _run(repo)
    assert result.returncode == 1
    assert "BLOCKED CONTENT" in result.stdout


def test_filename_that_is_an_echo_flag_is_still_matched(tmp_path):
    """bash's `echo` consumes a leading -n/-e/-E as a flag, so `echo "$filename"`
    fed an EMPTY name to the extension rules for a file named exactly `-e`.
    Harmless today because those names carry no blocked extension, but the rule
    must match on the real name, not on whatever echo chose to print."""
    repo = _make_repo(tmp_path)
    (repo / "-e.pdf").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    result = _run(repo)
    assert result.returncode == 1
    assert "BLOCKED FILE TYPE" in result.stdout


# --- Self-review round: the scanner's own runtime assumptions ----------------

# A Latin-1 / cp1252 'é'. Git classifies a file containing it as TEXT, so the
# binary guard never fires and the added-line scan is what has to catch it.
LATIN1_BYTE = b"\xe9"


def _stage_bytes(repo: Path, name: str, content: bytes) -> None:
    (repo / name).write_bytes(content)
    subprocess.run(["git", "add", name], cwd=repo, check=True)


def _run_env(repo: Path, env_extra: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh"],
                          cwd=repo, capture_output=True, text=True,
                          env={**os.environ, **env_extra})


def test_non_utf8_byte_on_the_line_does_not_hide_the_ssn(tmp_path):
    """Under a UTF-8 locale grep SILENTLY SKIPS any line that is not valid
    UTF-8, so one stray Latin-1 byte hid EVERY match on that line and a full
    SSN committed clean on a stock macOS machine (LANG=en_US.UTF-8).

    This is the toolkit's own use case, not an exotic one: it ingests
    foreign-language documents, and cp1252 is what a European bank statement or
    a Windows-authored CSV produces when pasted into a tracked file.
    """
    repo = _make_repo(tmp_path)
    _stage_bytes(repo, "notes.md",
                 b"Client Ren" + LATIN1_BYTE + b" Dupont SSN "
                 + FAKE_SSN.encode() + b"\n")
    r = _run_env(repo, {"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"})
    assert r.returncode == 1, "a non-UTF-8 byte hid the SSN on that line"


def test_non_utf8_byte_does_not_hide_a_bare_nine_digit_ssn(tmp_path):
    """Same hole via the token-splitting rule rather than the delimited one."""
    repo = _make_repo(tmp_path)
    _stage_bytes(repo, "notes.md",
                 b"Cliente Jos" + LATIN1_BYTE + b", id "
                 + f"{_SSN_AREA}{_SSN_GROUP}{_SSN_SERIAL}".encode() + b"\n")
    r = _run_env(repo, {"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"})
    assert r.returncode == 1


def test_utf8_content_still_scans_normally(tmp_path):
    """The counterweight: pinning LC_ALL=C must not stop the scanner reading
    legitimate UTF-8, which this toolkit's documents are full of."""
    repo = _make_repo(tmp_path)
    _stage_bytes(repo, "notes.md",
                 f"Bénéficiaire — SSN {FAKE_SSN}\n".encode("utf-8"))
    assert _run(repo).returncode == 1


def test_utf8_filename_still_blocks_by_extension(tmp_path):
    repo = _make_repo(tmp_path)
    _stage_bytes(repo, "état.pdf", b"x\n")
    assert _run(repo).returncode == 1


def test_running_from_a_subdirectory_still_scans(tmp_path):
    """Paths come back relative to the REPO ROOT but a pathspec resolves
    relative to the CURRENT DIRECTORY. From a subdirectory the two disagreed,
    every per-file diff matched nothing, and the script printed its clean banner
    having scanned no content at all — the exact false pass that banner exists
    to make impossible."""
    repo = _make_repo(tmp_path)
    (repo / "sub").mkdir()
    _stage(repo, "sub/leak.txt", f"ssn {FAKE_SSN}\n")
    r = subprocess.run(["bash", "../scripts/pre-commit-pii-scan.sh"],
                       cwd=repo / "sub", capture_output=True, text=True)
    assert r.returncode == 1, "scanning from a subdirectory found nothing"


def test_range_mode_from_a_subdirectory_does_not_claim_a_false_clean(tmp_path):
    """Worse in range mode, which prints a POSITIVE claim about its coverage."""
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "seed\n")
    base = _commit(repo, "base")
    (repo / "sub").mkdir()
    _stage(repo, "sub/leak.txt", f"ssn {FAKE_SSN}\n")
    _commit(repo, "leak")
    r = subprocess.run(["bash", "../scripts/pre-commit-pii-scan.sh", f"{base}...HEAD"],
                       cwd=repo / "sub", capture_output=True, text=True)
    assert r.returncode == 1, "range mode reported clean from a subdirectory"
    assert "PII scan clean" not in r.stdout


def test_outside_a_git_worktree_blocks_rather_than_reporting_clean(tmp_path):
    """No repository means nothing can be scanned, which must never read as
    'nothing to worry about'."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO / "scripts", tmp_path / "scripts")
    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 1
    assert "could not run" in r.stdout


# --- Separator forms --------------------------------------------------------
#
# `[- ]` matched exactly one hyphen or one space. Every form below reached a
# commit cleanly before the pattern required a CONSISTENT separator instead:
# a run of spaces (aligned columns), a tab, dot grouping and slash grouping.
# Dot grouping is the ordinary convention in the European documents this
# toolkit is built to ingest, so it is not an exotic input.
#
# Assembled from parts for the reason at the top of this file: a literal here
# would be a literal in a tracked file, and the scan would block its own tests.
@pytest.mark.parametrize("sep,label", [
    ("-", "hyphen"),
    (" ", "single space"),
    ("  ", "aligned columns"),
    ("\t", "tab"),
    (".", "dot grouping"),
    ("/", "slash grouping"),
])
def test_every_separated_ssn_form_blocks(tmp_path, sep, label):
    repo = _make_repo(tmp_path)
    value = f"{_SSN_AREA}{sep}{_SSN_GROUP}{sep}{_SSN_SERIAL}"
    _stage(repo, "notes.md", f"taxpayer ssn {value}\n")
    r = _run(repo)
    assert r.returncode == 1, f"{label} SSN committed cleanly"
    assert value not in r.stdout + r.stderr, "scanner echoed the value"


def test_a_money_range_is_not_mistaken_for_an_ssn(tmp_path):
    """The counterweight that decides HOW to widen the separator.

    The naive widening — one character class plus `+` — matches three digits,
    a dot, two digits, a space-hyphen-space run, then four digits: an ordinary
    money range, of which this repository contains five. Requiring the same
    separator on both sides admits every real SSN form and none of these.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "figures.md",
           "Deduction range: $1,400.00 - 2400 and $2,181.82 - 5000\n")
    assert _run(repo).returncode == 0, "a money range must stay committable"


def test_an_ssn_written_directly_after_a_label_blocks(tmp_path):
    """Letters used to be part of the extracted token, so `SSN<nine digits>`
    became ONE token that the exactly-nine-digits test rejected — the same shape
    as the `W2_<digits>` case fixed a round earlier, minus the separator. The
    token is now a run of digits, which no label can absorb."""
    repo = _make_repo(tmp_path)
    bare = _SSN_AREA + _SSN_GROUP + _SSN_SERIAL
    _stage(repo, "notes.md", f"SSN{bare}\n")
    assert _run(repo).returncode == 1


def test_a_ten_digit_number_still_does_not_false_positive(tmp_path):
    """Its counterweight: a maximal digit run cannot be a substring of a longer
    number, so a phone number stays one 10-digit token and is not an SSN."""
    repo = _make_repo(tmp_path)
    _stage(repo, "contacts.md", "phone 1234567890 and account 12345678901234\n")
    assert _run(repo).returncode == 0


# --- Filename and path rules ------------------------------------------------
#
# These are the scanner's ORIGINAL function and what README calls security
# layer 2, and until now nothing exercised them: both `BLOCKED FILENAME`
# branches could be deleted wholesale with the whole suite green — verified by
# mutation. The content scan added in this PR arrived with 90 tests; the rule
# it was built on top of had none.
@pytest.mark.parametrize("name", [
    "taxpayer_profile.json", "result.json", "answers.md", "parse_ledger.json",
    "holdings.json", "projection_inputs.json", "crypto_transactions.json",
    "tax_law_2026.json", "FBAR_2026.md", "2026-review.md",
    "tax_calendar_2026.ics",
])
def test_sensitive_filenames_are_blocked(tmp_path, name):
    repo = _make_repo(tmp_path)
    _stage(repo, name, "entirely innocent content\n")
    r = _run(repo)
    assert r.returncode == 1, f"{name} committed cleanly"
    assert "BLOCKED FILENAME" in r.stdout


@pytest.mark.parametrize("ext", ["pdf", "xlsx", "csv", "png", "zip", "PDF"])
def test_sensitive_file_types_are_blocked(tmp_path, ext):
    repo = _make_repo(tmp_path)
    _stage(repo, f"statement.{ext}", "innocent\n")
    r = _run(repo)
    assert r.returncode == 1, f".{ext} committed cleanly"
    assert "BLOCKED FILE TYPE" in r.stdout


def test_an_ordinary_source_file_is_not_caught_by_the_filename_rules(tmp_path):
    """The counterweight: the rules above must be a list, not a mood."""
    repo = _make_repo(tmp_path)
    _stage(repo, "agents/lib/results.json", '{"ok": true}\n')
    _stage(repo, "notes_answers.md", "nothing\n")
    assert _run(repo).returncode == 0


def test_hook_mode_says_commit_blocked_not_ci_blocked(tmp_path):
    """The converse of the range-mode banner check. Only one direction was
    pinned, so hook mode could tell a developer at their terminal that CI had
    blocked something."""
    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md", f"ssn {FAKE_SSN}\n")
    r = _run(repo)
    assert r.returncode == 1
    assert "COMMIT BLOCKED" in r.stdout
    assert "CI BLOCKED" not in r.stdout


def test_a_filename_containing_glob_metacharacters_is_still_scanned(tmp_path):
    """`:(literal)` is what stops a name like `report[1].md` being read as a
    pathspec PATTERN, which would match nothing and skip the content scan
    while the file committed cleanly."""
    repo = _make_repo(tmp_path)
    _stage(repo, "report[1].md", f"ssn {FAKE_SSN}\n")
    assert _run(repo).returncode == 1


def test_a_grep_that_cannot_run_blocks_rather_than_reporting_clean(tmp_path):
    """`|| true` cannot tell "no match" (exit 1) from "grep failed" (exit >= 2),
    so a broken grep produced an empty match set and a staged SSN scanned clean.
    The shim fails only for the extraction call, which is the deterministic way
    to reproduce it."""
    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md", f"ssn {FAKE_SSN}\n")
    shim = repo / "shim"
    shim.mkdir()
    (shim / "grep").write_text(
        "#!/bin/bash\n"
        'for a in "$@"; do [ "$a" = "-oE" ] && exit 2; done\n'
        'exec /usr/bin/grep "$@"\n')
    os.chmod(shim / "grep", 0o755)
    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh"], cwd=repo,
                       capture_output=True, text=True,
                       env={**os.environ, "PATH": f"{shim}:{os.environ['PATH']}"})
    assert r.returncode == 1, "a grep that could not run reported a clean scan"
    assert "could not run" in r.stdout


def test_no_unredacted_patch_survives_the_scan(tmp_path):
    """Every intermediate this script writes holds the raw value, and only two
    of six temp files were covered by the cleanup trap — a signal or an abort
    between creation and cleanup stranded a full patch, SSN included, in the
    system temp directory. One work directory, removed by one trap, is what
    makes "no file outlives the run" checkable.

    The temp parent is discovered by running `mktemp -d` the way the scanner
    does, NOT by setting TMPDIR: BSD `mktemp` (macOS) ignores TMPDIR and uses a
    per-user confstr path, so a TMPDIR-based version of this test passed
    vacuously on the platform it was written on and would only ever have run on
    Linux. Detection keys on the SSN itself, which no unrelated process's
    scratch file can contain.
    """
    probe = subprocess.run(["mktemp", "-d"], capture_output=True, text=True,
                           check=True).stdout.strip()
    parent = Path(probe)
    shutil.rmtree(parent, ignore_errors=True)
    parent = parent.parent

    def _entries():
        return set(parent.glob("tmp.*"))

    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md", f"ssn {FAKE_SSN}\n")
    before = _entries()
    r = _run(repo)
    assert r.returncode == 1

    leaked = []
    for entry in _entries() - before:
        try:
            files = [entry] if entry.is_file() else list(entry.rglob("*"))
            for f in files:
                if f.is_file() and FAKE_SSN in f.read_text(errors="ignore"):
                    leaked.append(str(f))
        except OSError:
            continue
    assert not leaked, f"unredacted value survived in temporary files: {leaked}"
