"""Content-level PII scan — the commit-time guard for full SSNs.

The filename/extension scan in the same script blocks the usual PII carriers
(PDF, XLSX, CSV, taxpayer_profile.json, answers.md, reviews/). This covers the
residual case: a full SSN typed into an otherwise-innocent tracked file, which
no other gate in the repository sees.

Each test drives the real script inside a throwaway git repo, because the
behaviour under test is a git-diff interaction that a unit test cannot reach.
"""

import os
import re
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


def _make_repo(tmp_path: Path, branch: str | None = None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    """A throwaway git repo carrying a copy of the scanner and the allowlist.

    `branch` names the initial branch. The push tests below need one that is NOT
    `main`, because refusing a push to `main` is a behaviour under test there and
    it has to be somewhere a test goes on purpose.
    """
    subprocess.run(["git", "init", "-q"] + (["-b", branch] if branch else []),
                   cwd=tmp_path, check=True)
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


def _stage(repo: Path, name: str, content: str, force: bool = False) -> None:
    """`force` is `git add -f`, and it is how the gitignored artifacts actually
    arrive at this scan — that or a workspace whose .gitignore predates the
    entry. It is also required for a name a CONTRIBUTOR's global gitignore
    happens to cover, which would otherwise fail the staging step itself and
    never reach the scan under test."""
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    subprocess.run(["git", "add"] + (["-f"] if force else []) + [name],
                   cwd=repo, check=True)


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


def test_range_mode_scans_the_root_commit_when_it_is_the_base(tmp_path):
    """`A...B` excludes A, which silently exempted the initial import.

    The gate before publishing a private repository is a full-history audit,
    `$(git rev-list --max-parents=0 HEAD)...HEAD` — and with the base excluded
    that scanned every commit EXCEPT the one most likely to carry a file nobody
    reviewed. Run against this exact repository shape it printed "PII scan
    clean — 1 commit(s) inspected" over a root commit holding a full SSN.

    A root commit is the one base for which exclusion cannot be justified by
    "it was already scanned by the range that landed it": nothing precedes it.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "leak.md", f"SSN: {FAKE_SSN}\n")
    root = _commit(repo, "root commit carrying a leak")
    _stage(repo, "ok.md", "clean\n")
    _commit(repo, "clean")

    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{root}...HEAD"],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 1, f"the initial import went unscanned: {r.stdout!r}"
    assert "leak.md" in r.stdout, "must name the file it found, not merely fail"
    assert FAKE_SSN not in r.stdout + r.stderr


def test_range_mode_still_excludes_a_non_root_base(tmp_path):
    """The root commit is an exception, not a redefinition of `A...B`.

    A CI base is already on main and was scanned by the pull request that landed
    it. Including every base would fail today's PR for content that passed its
    own review — the paired assertion that keeps the fix above narrow.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "nothing here\n")
    _commit(repo, "root")
    _stage(repo, "leak.md", f"SSN: {FAKE_SSN}\n")
    base = _commit(repo, "already on main")
    _stage(repo, "more.md", "still nothing\n")
    _commit(repo, "more")

    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{base}...HEAD"],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, f"a non-root base must stay excluded: {r.stdout!r}"


def test_root_detection_dies_on_a_broken_git_rather_than_guessing(tmp_path):
    """The root-commit check must tell "confirmed: no parent" apart from "the
    check itself failed" — an I/O error, a broken git wrapper. Both used to
    make `git rev-parse -q --verify "${merge_base}^"` exit non-zero
    identically, so a genuine failure was silently read as proof of root.

    A fake `git` on PATH that fails only `rev-list --parents -n 1
    <merge_base>` (what the fix reads instead) simulates that failure. The
    base here has a real parent, so the correct behavior on a working git is
    to exclude it — the fix must refuse to guess and die instead of silently
    doing that (or anything else) when it cannot actually tell.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "nothing here\n")
    _commit(repo, "root")
    _stage(repo, "next.md", "also nothing\n")
    base = _commit(repo, "already on main — has a real parent")
    _stage(repo, "more.md", "still nothing\n")
    _commit(repo, "more")

    fakebin = tmp_path.parent / f"{tmp_path.name}_fakebin"
    fakebin.mkdir()
    real_git = shutil.which("git")
    wrapper = fakebin / "git"
    wrapper.write_text(
        "#!/bin/bash\n"
        f'if [ "$1" = "rev-list" ] && [ "$2" = "--parents" ] && [ "$5" = "{base}" ]; then\n'
        "  exit 1\n"
        "fi\n"
        f'exec "{real_git}" "$@"\n'
    )
    os.chmod(wrapper, 0o755)

    env = dict(os.environ, PATH=f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}")
    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{base}...HEAD"],
                       cwd=repo, capture_output=True, text=True, env=env)
    assert r.returncode == 1, (
        f"a broken merge-base check must block, not guess a verdict: "
        f"stdout={r.stdout!r} stderr={r.stdout!r}"
    )
    assert "could not run" in r.stdout, r.stdout


def test_a_root_commit_is_still_detected_when_its_MESSAGE_mentions_a_parent(tmp_path):
    """The root check reads the commit object's HEADER block, so it must stop at
    the blank line that ends it.

    `git cat-file -p | grep '^parent '` was the first attempt and false-matches
    a commit MESSAGE line beginning "parent " — which reads as "this commit has
    a parent", excludes the base, and silently reopens the initial-import
    bypass. The message below is the one that broke it.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "leak.md", f"SSN: {FAKE_SSN}\n")
    root = _commit(repo, "import\n\nparent bug tracked separately\n")
    _stage(repo, "ok.md", "clean\n")
    _commit(repo, "clean")

    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{root}...HEAD"],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 1, f"the initial import went unscanned: {r.stdout!r}"
    assert "leak.md" in r.stdout


def _clone_shallow(origin: Path, dest: Path, depth: int,
                   branch: str | None = None) -> Path:
    """A genuinely shallow clone — `file://` forces the transport that honours
    --depth, where a plain local path hardlinks the whole object store instead.
    """
    subprocess.run(["git", "clone", "-q", "--depth", str(depth)]
                   + (["--branch", branch] if branch else [])
                   + [f"file://{origin}", str(dest)], check=True)
    assert subprocess.run(["git", "rev-parse", "--is-shallow-repository"],
                          cwd=dest, capture_output=True, text=True
                          ).stdout.strip() == "true", "the clone is not shallow"
    (dest / "scripts").mkdir(exist_ok=True)
    shutil.copy(SCAN, dest / "scripts" / "pre-commit-pii-scan.sh")
    return dest


def test_a_shallow_boundary_base_is_not_scanned_as_a_root_commit(tmp_path):
    """In a shallow clone the boundary commit's parents were never fetched, so
    `git rev-list --parents -n 1` prints the sha alone — exactly what a TRUE
    root prints. Read as a root, the base is appended to the scan set and later
    diffed against the empty tree: its entire SNAPSHOT is scanned as if newly
    added, including files carried down from history the range never asked
    about. A pull request then fails on content some earlier commit legitimately
    holds and today's diff never touched.

    Here `leak.md` exists only in the commit BELOW the boundary. The range is
    clean; anything that blocks has scanned outside it.
    """
    origin = _make_repo(tmp_path / "origin")
    _stage(origin, "leak.md", f"SSN: {FAKE_SSN}\n")
    _commit(origin, "root")
    _stage(origin, "mid.md", "clean\n")
    _commit(origin, "mid")
    _stage(origin, "head.md", "also clean\n")
    _commit(origin, "head")

    shallow = _clone_shallow(origin, tmp_path / "shallow", depth=2)
    base = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=shallow,
                          capture_output=True, text=True).stdout.strip()

    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{base}...HEAD"],
                       cwd=shallow, capture_output=True, text=True)
    assert r.returncode == 0, (
        "a shallow boundary was scanned as a root commit — the block came from "
        f"outside the range: {r.stdout!r}")


def test_a_commit_whose_recorded_parent_is_missing_blocks(tmp_path):
    """The same ambiguity inside the range, where the answer is different.

    A commit that records a parent this clone does not have cannot be diffed
    against it. The fallback used to be the empty tree — the whole snapshot,
    reported afterwards as "N commit(s) inspected", which is coverage the scan
    did not have. It blocks instead, and says what to fix.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "nothing here\n")
    base = _commit(repo, "base")
    _stage(repo, "more.md", "still nothing\n")
    head = _commit(repo, "more")

    parent = subprocess.run(["git", "rev-parse", f"{head}^"], cwd=repo,
                            capture_output=True, text=True).stdout.strip()
    fakebin = tmp_path.parent / f"{tmp_path.name}_missingparent"
    fakebin.mkdir()
    real_git = shutil.which("git")
    wrapper = fakebin / "git"
    # Only the existence probe for that parent fails, which is what a shallow
    # graft looks like from here: the commit object still records the sha, the
    # object store does not have it.
    wrapper.write_text(
        "#!/bin/bash\n"
        f'if [ "$1" = "rev-parse" ] && [ "$#" -ge 4 ] && [ "$4" = "{parent}^{{commit}}" ]; then\n'
        "  exit 128\n"
        "fi\n"
        f'exec "{real_git}" "$@"\n'
    )
    os.chmod(wrapper, 0o755)

    env = dict(os.environ, PATH=f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}")
    r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{base}...HEAD"],
                       cwd=repo, capture_output=True, text=True, env=env)
    assert r.returncode == 1, (
        f"an unscannable history reported a verdict: {r.stdout!r}")
    assert "shallow" in r.stdout, r.stdout


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
    # `base` is deliberately NOT the root commit. A root base is INCLUDED in the
    # scan (see test_range_mode_scans_the_root_commit_when_it_is_the_base), so
    # its own files would join this count and this assertion would be measuring
    # root inclusion rather than the coverage line it is here to pin.
    _stage(repo, "seed.md", "nothing here\n")
    _commit(repo, "root")
    _stage(repo, "seed2.md", "nothing here either\n")
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


def test_withholding_and_planning_directories_are_blocked(tmp_path):
    """Planning reports remain sensitive even when forced past .gitignore."""
    for path in ("withholding/report.md", ".planning/HANDOFF.json"):
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


# Agent runtime state under `.claude/`. Every one of these is gitignored, so
# anything reaching this scan arrived through `git add -f` or through a
# workspace whose .gitignore predates the entry — the same way the runtime
# workspace directories above get here.
CLAUDE_RUNTIME_STATE = [
    ".claude/.archive-files",
    ".claude/.archive-files.Ab3xZ9",   # the mktemp sibling a timeout leaves
    ".claude/.archive-dirs",
    ".claude/.archive-index-dirty",  # empty, but still agent runtime state
    ".claude/.perms-stamp",
    ".claude/.perms-stamp.Qw7rTt",
    ".claude/.skill-invariants-stamp",
    ".claude/settings.local.json",
    ".claude/hookify.block-git-push.local.md",
    "sub/.claude/.archive-files",      # a clone checked out inside another
]


def test_the_scan_blocks_agent_runtime_state_under_dot_claude(tmp_path):
    """The content scan structurally cannot stand in for this rule.

    `.archive-files` and `.archive-dirs` are lists of paths beneath archive/ —
    a taxpayer's scanned documents, named after what they are — and a PATHNAME
    has neither an SSN shape nor a credential shape. A full-history index of one
    is read through and pronounced clean.

    The fixture content is deliberately a plain synthetic path, matching nothing
    the content patterns look for. That is what proves the PATH rule fired: a
    fixture carrying an SSN would block through the wrong branch and prove
    nothing about this one.
    """
    for path in CLAUDE_RUNTIME_STATE:
        repo = _make_repo(tmp_path / path.replace("/", "_"))
        _stage(repo, path, "archive/2025/documents/W2-Example-Employer.pdf\n",
               force=True)
        r = _run(repo)
        assert r.returncode == 1, f"{path} committed cleanly"
        assert "BLOCKED PATH (agent runtime state)" in r.stdout, (
            f"{path} was blocked, but by some other rule: {r.stdout}")


def test_the_scan_still_allows_the_tracked_claude_config(tmp_path):
    """The counterweight, and what stops the rule above being 'simplified' into
    a blanket `.claude/` block later.

    This is the real commit shape: settings.json carries the PostToolUse hooks,
    hooks/ the scripts they run, agents/ the read-only reviewers, and
    skills/<name> the tracked relative symlink Claude Code discovers through.
    Staged together, because that is how they arrive.

    Being explicit about what this can and cannot catch: it passes unchanged
    against a scanner that has no `.claude/` rule at all, which is inherent —
    an over-blocking rule is the only thing it can see. The test above is the
    evidence that the rule exists; this one is the evidence that it is narrow.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, ".claude/settings.json", '{"hooks": {"PostToolUse": []}}\n')
    _stage(repo, ".claude/hooks/harden-runtime-perms.sh", "#!/usr/bin/env bash\n")
    _stage(repo, ".claude/agents/bias-direction-reviewer.md", "# reviewer\n")
    _stage(repo, "sub/.claude/settings.json", "{}\n")
    (repo / ".agents" / "skills" / "tax-setup").mkdir(parents=True)
    (repo / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
    (repo / ".claude" / "skills" / "tax-setup").symlink_to(
        "../../.agents/skills/tax-setup")
    subprocess.run(["git", "add", ".claude/skills/tax-setup"], cwd=repo,
                   check=True)

    r = _run(repo)
    assert r.returncode == 0, (
        f"the repository's own Claude Code configuration is uncommittable: "
        f"{r.stdout}")


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
    # The one entry here whose CONTENT this scanner structurally cannot vet.
    # tax-generate writes this generator per workspace with the taxpayer's NAME
    # in its section headers, and a name has neither an SSN shape nor a
    # credential shape, so the content pass reads it line by line and
    # pronounces it clean. .gitignore names it, AGENTS.md §1 counts it
    # sensitive and the permissions hook chmods it 600; being named by all
    # three and not by this case is what let `git add -f generate_tax_pdf.py`
    # commit a taxpayer's name.
    "generate_tax_pdf.py",
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


# A sha256 is 64 hex characters, and one containing a maximal run of EXACTLY
# nine digits reads as a bare SSN to the token rule. Assembled rather than
# written whole, for the same reason FAKE_SSN is: a literal nine-digit run in
# this file would trip the scanner it exercises.
FAKE_DIGEST = "a" * 20 + "12345" + "6789" + "b" * 35
_LOCK_LINE = f"pdfplumber==0.11.10 \\\n    --hash=sha256:{FAKE_DIGEST}\n"

# The second exempt path, and the one whose digests a re-run of the acceptance
# replays REWRITES. Its token is the `sha256 ` word plus a backticked digest, on
# one line — the shape the record writes and the shape it instructs a renewer to
# write.
_RECORD = "docs/ops/review-subagent-acceptance.md"
_RECORD_LINE = f"**Input.** `5c23279:agents/lib/x.py`,\nsha256 `{FAKE_DIGEST}`.\n"


def test_dependency_lock_digests_do_not_false_positive(tmp_path):
    """Pinning the tree with --generate-hashes must not block the commit.

    A sha256 contains a nine-digit run often enough that the real lock tripped
    this rule 52 times the first time it was generated. The documented escape
    is `--no-verify`, so a gate that fires every time the lock is regenerated
    would train contributors to bypass the PII scan itself — costing far more
    protection than the rule buys on a file of hex digests.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "requirements.txt", _LOCK_LINE)
    assert len(FAKE_DIGEST) == 64, "digest must be sha256-shaped to be exempt"
    assert _run(repo).returncode == 0

    # The acceptance record pins four of them and a re-run rewrites them, so it
    # is the same every-regeneration clause on a file whose whole purpose is to
    # be re-committed after a replay.
    _stage(repo, _RECORD, _RECORD_LINE)
    assert _run(repo).returncode == 0, (
        "renewing an acceptance pin blocks its own commit, and the documented "
        "escape from that is --no-verify on the PII gate")

    # ...and the live record is written in the shape the exemption keys on. The
    # gate and the document are one mechanism in two files: rewrap a pin so the
    # `sha256` word and its digest fall on separate lines and the exemption —
    # which sed applies a LINE at a time — stops covering it, with the first
    # symptom a blocked commit during a season-open re-run.
    #
    # Per TOKEN, which is how `strip_artifact_digests` strips them: its `sed` is
    # a global substitution, so every digest on a line has to carry its own
    # `sha256 ` word. Asked per LINE — which is what this was — ONE well-formed
    # token excused every other backticked digest beside it, and the shape that
    # slips through is the one a renewal writes: `sha256 \`<new>\` (was
    # \`<old>\`)`. The test passed, the hook\'s six modules passed, and the real
    # gate blocked the commit, because Replay A\'s current digest carries a run
    # of exactly nine bare digits. A control that reads the file at a coarser
    # grain than the thing it models will always have a gap that shape.
    record = (REPO / _RECORD).read_text()
    loose = []
    for n, line in enumerate(record.splitlines(), 1):
        for m in re.finditer(r"`[0-9a-f]{64}`", line):
            if not line[:m.start()].endswith("sha256 "):
                loose.append(f"{_RECORD}:{n}")
    assert not loose, (
        f"a digest in {_RECORD} is written outside the exempt token shape "
        f"(`sha256 ` immediately before it, on one line): {sorted(set(loose))}")

    # ...and the control on that reading, because the corpus is currently clean
    # and a per-line check passes over it exactly as a per-token one does. The
    # second digest here is the one the real gate counts.
    two = f"sha256 `{FAKE_DIGEST}` (was `{FAKE_DIGEST}`)"
    assert any(not two[:m.start()].endswith("sha256 ")
               for m in re.finditer(r"`[0-9a-f]{64}`", two)), (
        "a second digest written beside a well-formed token was read as exempt "
        "— that is the per-line reading the gate does not share")
    repo = _make_repo(tmp_path / "renewal")
    _stage(repo, _RECORD, two + "\n")
    assert _run(repo).returncode == 1, (
        "the gate exempted a bare digest sitting beside a well-formed token, "
        "so the shape this test holds the record to is stricter than the gate\'s")


def test_a_real_ssn_in_the_lock_file_still_blocks(tmp_path):
    """The exemption removes the DIGEST TOKENS, not the file.

    This is the test that keeps the fix from becoming a hiding place: the
    exempt path is still fully scanned for everything that is not a
    `--hash=sha256:` token followed by exactly 64 hex characters.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "requirements.txt", _LOCK_LINE + f"# contact {FAKE_SSN}\n")
    assert _run(repo).returncode == 1

    repo = _make_repo(tmp_path / "record")
    _stage(repo, _RECORD, _RECORD_LINE + f"Reviewed by {FAKE_SSN}.\n")
    assert _run(repo).returncode == 1, (
        "the acceptance record is exempt for one TOKEN, not as a file")

    # The token's own bounds, both halves. A bare digest carries no `sha256`
    # word and is not the shape the record writes; nine digits typed as a digest
    # are nine digits. Neither is exempt, and the second is the one that decides
    # whether this is a hiding place or an exemption.
    repo = _make_repo(tmp_path / "loose")
    _stage(repo, _RECORD, f"sha256\n`{FAKE_DIGEST}`.\n")
    assert _run(repo).returncode == 1, (
        "a digest wrapped away from its `sha256` word was exempted anyway, so "
        "the shape the record is held to is not the shape the gate reads")


def test_the_digest_exemption_does_not_leak_to_other_paths(tmp_path):
    """Scoped by exact path, never by token shape alone.

    Were it keyed only on the `--hash=sha256:` shape, prefixing a secret with
    that string anywhere in the tree would launder it. Blocking the identical
    line in notes.md is the deliberate, conservative half of that trade.
    """
    repo = _make_repo(tmp_path)
    _stage(repo, "notes.md", _LOCK_LINE)
    assert _run(repo).returncode == 1

    for n, elsewhere in enumerate(("notes.md", "docs/ops/other-record.md",
                                   "review-subagent-acceptance.md")):
        repo = _make_repo(tmp_path / f"elsewhere{n}")
        _stage(repo, elsewhere, _RECORD_LINE)
        assert _run(repo).returncode == 1, (
            f"the acceptance record's token laundered a digest in {elsewhere}")


def test_every_hashed_lock_in_the_repo_is_covered_by_the_digest_exemption():
    """The exemption is by EXACT PATH, deliberately — a name pattern would be a
    hiding place. The cost of that is a sibling: `requirements-dev.txt` landed
    hashed like the runtime lock, was covered by nothing, and blocked the very
    commit that introduced it with 101 matches that were all sha256 digits.

    So the set is checked rather than remembered. Any lock this repository gains
    must be added to `strip_artifact_digests` in the same commit that hashes it.
    """
    scanner = (REPO / "scripts" / "pre-commit-pii-scan.sh").read_text()
    arm = re.search(r"strip_artifact_digests\(\) \{.*?\n\}", scanner, re.S)
    assert arm, "strip_artifact_digests no longer has a readable body"

    hashed = sorted(p for p in REPO.glob("requirements*.txt")
                    if "--hash=sha256:" in p.read_text())
    assert hashed, "no hashed lock found at all — has the pinning changed?"
    for lock in hashed:
        assert lock.name in arm.group(0), (
            f"{lock.name} is pinned with --generate-hashes but is not in "
            "strip_artifact_digests; committing it will block on digit runs "
            "inside its digests, and the documented way out is --no-verify — "
            "which is how a gate teaches people to bypass the PII scan")


def test_the_digest_exemption_removes_only_the_digest_token():
    """Scoped along both axes. A real SSN on a requirements line is still
    scanned, because only the `--hash=sha256:<64 hex>` tokens are removed and
    not the lines holding them."""
    scanner = (REPO / "scripts" / "pre-commit-pii-scan.sh").read_text()
    assert "s/--hash=sha256:[0-9a-f]{64}//g" in scanner


# --- Y6: the pre-push hook scans instead of refusing ------------------------
#
# The hook these tests drive is written by `agents/install.sh`, so they install
# it the way a contributor does and then run REAL pushes at a real bare remote.
# That is the only way to exercise it: git supplies the ref lines on stdin, and
# a hook invoked by hand is a hook whose input the test invented.


def _install_hooks(repo: Path, home: Path) -> None:
    """Install the managed git hooks the way a contributor does."""
    home.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["bash", "agents/install.sh", "--hooks-only"], cwd=repo,
                       capture_output=True, text=True,
                       env={**os.environ, "HOME": str(home)})
    assert r.returncode == 0, f"hook install failed:\n{r.stdout}\n{r.stderr}"
    assert (repo / ".git" / "hooks" / "pre-push").is_file(), "no pre-push hook"


def _pushable_clone(tmp_path: Path) -> tuple[Path, Path]:
    """A throwaway clone with the managed hooks installed and a bare `origin`."""
    clone = _make_repo(tmp_path / "clone", branch="work")
    (clone / "agents").mkdir()
    shutil.copy(REPO / "agents" / "install.sh", clone / "agents")
    _install_hooks(clone, tmp_path / "home")

    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)],
                   cwd=clone, check=True)

    subprocess.run(["git", "add", "-A"], cwd=clone, check=True)
    _commit(clone, "seed")
    return clone, remote


def _push(clone: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "push", "origin", *args], cwd=clone,
                          capture_output=True, text=True)


def test_a_clean_branch_pushes_without_no_verify(tmp_path):
    """The whole point of Y6. `--no-verify` disables the pre-commit PII gate as
    well as this hook, so a workflow whose documented push command carries it
    teaches the reflex that turns the content scan off."""
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "notes.md", "Nothing sensitive here.\n")
    _commit(clone, "ordinary work")
    r = _push(clone, "HEAD:refs/heads/feature")
    assert r.returncode == 0, (
        f"a clean branch was refused:\nstdout={r.stdout}\nstderr={r.stderr}")


def test_a_full_ssn_in_an_outgoing_commit_blocks_the_push(tmp_path):
    """The protection the blanket refusal never actually provided: it refused
    every push identically, so it could not tell a clean branch from this one.

    The commit is made with --no-verify, which is exactly how such a commit
    comes to exist — this hook is the second chance at it, and CI the third.

    The banner is asserted here rather than in a case of its own, because a
    second push through the same path buys only the string. Range mode used to
    announce itself as CI, which sends a contributor standing at their own
    terminal to look for a workflow run that does not exist, and the remedies it
    named were index-shaped: `--cached` lists nothing once the commit is written.
    """
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "leak.md", f"SSN: {FAKE_SSN}\n")
    _commit(clone, "leak")
    r = _push(clone, "HEAD:refs/heads/feature")
    assert r.returncode != 0, "a full SSN was pushed"
    assert FAKE_SSN not in r.stdout + r.stderr, (
        "the block message echoed the value it exists to keep off the wire")
    assert "PUSH BLOCKED" in r.stdout, (
        f"expected a push-shaped banner; got:\n{r.stdout}")
    assert "CI BLOCKED" not in r.stdout
    assert "--cached" not in r.stdout, (
        "the block pointed at the index, which is empty at push time")


def test_a_commit_removed_by_a_later_one_still_blocks_the_push(tmp_path):
    """Push mode inherits range mode's per-commit scan, and this is why that
    matters here rather than only in CI: the value is in the history being
    published even though the tip is clean. A net `base...head` diff sees
    nothing."""
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "leak.md", f"SSN: {FAKE_SSN}\n")
    _commit(clone, "leak")
    (clone / "leak.md").write_text("cleaned up\n")
    subprocess.run(["git", "add", "leak.md"], cwd=clone, check=True)
    _commit(clone, "remove it again")
    r = _push(clone, "HEAD:refs/heads/feature")
    assert r.returncode != 0, (
        "the tip was clean, so only a per-commit scan catches this")


def test_only_the_commits_the_remote_lacks_are_scanned(tmp_path):
    """The base is what the remote already has, exactly as in CI: a commit the
    remote carries was somebody's earlier push and is not this push's business.

    Without that bound, every push of a NEW branch re-scans the whole history
    and one old blocked commit makes the repository permanently unpushable —
    the cry-wolf shape that gets a gate switched off for good. The new-branch
    case is the one that can over-scan, because git reports the remote sha as
    all zeros and the range has to be recovered from the remote-tracking refs;
    an update to an existing branch is handed its own base.
    """
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "leak.md", f"SSN: {FAKE_SSN}\n")
    _commit(clone, "leak")
    forced = _push(clone, "--no-verify", "HEAD:refs/heads/work")
    assert forced.returncode == 0, f"seeding push failed:\n{forced.stderr}"

    # A NEW branch whose history contains that commit: remote sha is zeros.
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=clone,
                   check=True)
    _stage(clone, "next.md", "ordinary follow-up\n")
    _commit(clone, "clean follow-up")
    r = _push(clone, "HEAD:refs/heads/feature")
    assert r.returncode == 0, (
        f"a new branch was refused for a leak the remote already has:\n"
        f"{r.stdout}\n{r.stderr}")

    # ...and again as an UPDATE to that branch, where git supplies the base.
    _stage(clone, "more.md", "more ordinary work\n")
    _commit(clone, "second follow-up")
    r = _push(clone, "HEAD:refs/heads/feature")
    assert r.returncode == 0, (
        f"an update to a pushed branch was refused:\n{r.stdout}\n{r.stderr}")


def test_pushing_to_main_is_still_refused(tmp_path):
    """The half of the old blanket refusal that was a protection rather than
    friction. The pre-commit hook blocks COMMITTING to main; nothing else
    blocked PUSHING there, so replacing the refusal with a scan alone would
    have removed a control while claiming to remove only a nuisance."""
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "notes.md", "Nothing sensitive here.\n")
    _commit(clone, "ordinary work")
    for branch in ("main", "master"):
        r = _push(clone, f"HEAD:refs/heads/{branch}")
        assert r.returncode != 0, f"a direct push to {branch} was allowed"


def test_deleting_a_remote_branch_is_not_blocked(tmp_path):
    """A deletion publishes no content, and git signals it with an all-zero
    LOCAL sha. Treating that as a range makes the hook refuse the ordinary
    branch cleanup after a merge — which is how `--no-verify` gets back into
    the documented workflow through a different door."""
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "notes.md", "Nothing sensitive here.\n")
    _commit(clone, "ordinary work")
    assert _push(clone, "HEAD:refs/heads/feature").returncode == 0
    r = _push(clone, "--delete", "refs/heads/feature")
    assert r.returncode == 0, f"branch deletion was refused:\n{r.stdout}{r.stderr}"


def test_the_hook_blocks_when_the_scan_script_is_missing(tmp_path):
    """Fails closed, like every other path in this gate. An unperformed scan
    must never look like a clean one — and the script going missing is the
    realistic case, since it is deleted by a bad rebase or absent in a clone
    made before it existed."""
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "notes.md", "Nothing sensitive here.\n")
    _commit(clone, "ordinary work")
    (clone / "scripts" / "pre-commit-pii-scan.sh").unlink()
    r = _push(clone, "HEAD:refs/heads/feature")
    assert r.returncode != 0, "pushed with no scanner present"
    assert "PUSH BLOCKED" in r.stdout


def test_a_shallow_history_blocks_rather_than_scanning_around_it(tmp_path):
    """A shallow boundary and a true root commit look identical to `rev-list`:
    neither has a parent in this clone. The difference is in the commit OBJECT,
    and it decides whether the range the hook builds is complete.

    Given the boundary as its base, the scan EXCLUDES it — its own root check
    reads the object, finds the recorded parent this clone lacks, and does not
    re-include it. The oldest commit of the push then goes unscanned while
    everything reports clean. A root commit has no such gap, which is why the
    ordinary first push above is allowed to take the same path.
    """
    clone, remote = _pushable_clone(tmp_path)
    _stage(clone, "notes.md", "Nothing sensitive here.\n")
    _commit(clone, "ordinary work")
    assert _push(clone, "HEAD:refs/heads/work").returncode == 0

    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "work",
                    f"file://{remote}", str(shallow)], check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"],
                   cwd=shallow, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=shallow, check=True)
    _install_hooks(shallow, tmp_path / "home-shallow")

    # A remote nothing has been fetched from, so there is no remote-tracking
    # ref to bound the range and the boundary itself becomes the oldest
    # outgoing commit. Pushing a shallow checkout somewhere new is how this
    # arises for real — a CI job opening a pull request from its own checkout.
    elsewhere = tmp_path / "elsewhere.git"
    subprocess.run(["git", "init", "-q", "--bare", str(elsewhere)], check=True)
    subprocess.run(["git", "remote", "remove", "origin"], cwd=shallow, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(elsewhere)],
                   cwd=shallow, check=True)
    _stage(shallow, "more.md", "more ordinary work\n")
    _commit(shallow, "work on a shallow clone")

    r = _push(shallow, "HEAD:refs/heads/feature")
    assert r.returncode != 0, "a range that omits its oldest commit was pushed"
    assert "records a parent this clone does not have" in r.stdout, (
        "the refusal must come from the HOOK's own object read. The scanner's "
        "generic merge-base failure also mentions `unshallow`, so a looser "
        "assertion passes for a base that is merely unresolvable"
        f":\n{r.stdout}")


def test_a_remote_with_no_tracking_refs_is_bounded_by_nothing(tmp_path):
    """The bound has to be what THIS remote has. `--remotes` — every remote's
    tracking refs — was the fallback here, and it is wrong in the direction that
    matters: it excludes what SOME remote holds, and a commit another remote
    holds is exactly what this push publishes to a place that has never seen it.

    The reproduction is the fork/upstream shape CONTRIBUTING.md invites: a value
    reaches `origin` past the gate, a second remote is added and never fetched,
    and the push to it inspected nothing and said nothing.
    """
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "leak.md", f"SSN: {FAKE_SSN}\n")
    _commit(clone, "leak")
    assert _push(clone, "--no-verify", "HEAD:refs/heads/work").returncode == 0

    elsewhere = tmp_path / "elsewhere.git"
    subprocess.run(["git", "init", "-q", "--bare", str(elsewhere)], check=True)
    subprocess.run(["git", "remote", "add", "client", str(elsewhere)],
                   cwd=clone, check=True)
    r = subprocess.run(["git", "push", "client", "HEAD:refs/heads/feature"],
                       cwd=clone, capture_output=True, text=True)
    assert r.returncode != 0, (
        "a commit origin already has was republished to a remote that has "
        "never seen it, unscanned")


def test_a_push_by_url_is_bounded_by_what_that_url_advertises(tmp_path):
    """The counterweight to the test above: a remote named by URL must still be
    bounded, not fall back to re-scanning every commit it already has. Asking
    the destination is what makes both cases one case.

    The ref has to be a NEW one. Git reports the real remote sha for a branch the
    destination already carries, however the destination was named, so an update
    never reaches the code that recovers the range.
    """
    clone, remote = _pushable_clone(tmp_path)
    _stage(clone, "notes.md", "Nothing sensitive here.\n")
    _commit(clone, "ordinary work")
    assert _push(clone, "HEAD:refs/heads/work").returncode == 0

    _stage(clone, "next.md", "more ordinary work\n")
    _commit(clone, "follow-up")
    r = subprocess.run(["git", "push", str(remote), "HEAD:refs/heads/other"],
                       cwd=clone, capture_output=True, text=True)
    assert r.returncode == 0, f"a push by URL was refused:\n{r.stdout}{r.stderr}"
    assert "1 commit(s) inspected" in r.stdout, (
        f"the destination was not asked, so the whole history was rescanned:"
        f"\n{r.stdout}")


def test_a_neutered_scanner_blocks_rather_than_passing_silently(tmp_path):
    """Exit 0 is not evidence that the scan ran. The script is read from the
    WORKING TREE at push time, so what executes is whatever is on disk — a
    half-finished local edit to it, or a rewrite the push itself would publish.

    Replacing it with `exit 0` published a full SSN with no output at all. The
    hook requires the affirmative coverage line, which is the same reason range
    mode prints one.
    """
    clone, _ = _pushable_clone(tmp_path)
    (clone / "scripts" / "pre-commit-pii-scan.sh").write_text("#!/bin/bash\nexit 0\n")
    _stage(clone, "leak.md", f"SSN: {FAKE_SSN}\n", force=True)
    _commit(clone, "neuter the scanner and leak")
    r = _push(clone, "HEAD:refs/heads/feature")
    assert r.returncode != 0, "a scanner that inspected nothing reported clean"
    assert "PUSH BLOCKED" in r.stdout


def test_deleting_main_is_refused_even_though_a_deletion_publishes_nothing(tmp_path):
    """The destination guard runs BEFORE the deletion skip. Ordered the other
    way — a deletion publishes no content, so skip it — `git push origin
    --delete main` became the one form of writing to that branch the hook
    allowed, and this diff makes `--delete` the documented plain command."""
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "notes.md", "Nothing sensitive here.\n")
    _commit(clone, "ordinary work")
    assert _push(clone, "--no-verify", "HEAD:refs/heads/main").returncode == 0
    r = _push(clone, "--delete", "refs/heads/main")
    assert r.returncode != 0, "the hook allowed remote main to be deleted"
    assert "PUSH BLOCKED" in r.stdout


def test_a_ref_that_publishes_nothing_says_so(tmp_path):
    """A hook that prints nothing cannot be told from one that did not run. A
    tag pointing at an already-pushed commit is the ordinary way to reach this,
    and it used to exit silently."""
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "notes.md", "Nothing sensitive here.\n")
    _commit(clone, "ordinary work")
    assert _push(clone, "HEAD:refs/heads/feature").returncode == 0
    subprocess.run(["git", "tag", "-a", "-m", "release", "v1"], cwd=clone,
                   check=True)
    r = _push(clone, "refs/tags/v1")
    assert r.returncode == 0, f"a tag push was refused:\n{r.stdout}{r.stderr}"
    assert "publishes no commit" in r.stdout, (
        f"the hook passed the ref without a word about it:\n{r.stdout!r}")


def test_an_unknown_scan_context_is_an_error_not_a_label(tmp_path):
    """`PII_SCAN_CONTEXT` changes wording only, and the set is closed for the
    reason `agents/lib/capabilities.py` gives about its own ids: a typo must not
    silently select nothing. Lowercase `push` printed a banner reading "push
    BLOCKED" and quietly dropped the push-specific remedy, and an arbitrary
    value could fake a clean line into somebody's scrollback."""
    repo = _make_repo(tmp_path)
    _stage(repo, "seed.md", "nothing here\n")
    base = _commit(repo, "base")
    _stage(repo, "ok.md", "still nothing\n")
    _commit(repo, "clean")

    for value in ("push", "PII scan clean"):
        r = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh",
                            f"{base}...HEAD"], cwd=repo,
                           capture_output=True, text=True,
                           env={**os.environ, "PII_SCAN_CONTEXT": value})
        assert r.returncode == 1, f"{value!r} was accepted as a context label"
        assert "not a label" in r.stdout, r.stdout


def _stub_scanner(clone: Path, body: str) -> None:
    """Replace the scanner the hook will read with `body`, committed.

    Committed rather than left dirty because the point of several cases below is
    what runs at PUSH time, and the hook reads the working tree either way — a
    stub that is also in the history is the harsher version of the same thing.
    """
    (clone / "scripts" / "pre-commit-pii-scan.sh").write_text(body)
    os.chmod(clone / "scripts" / "pre-commit-pii-scan.sh", 0o755)
    subprocess.run(["git", "add", "-f", "scripts/pre-commit-pii-scan.sh"],
                   cwd=clone, check=True)


def test_a_scanner_that_denies_coverage_is_not_read_as_clean(tmp_path):
    """The verdict was a SUBSTRING test, and a stub printing "No PII scan clean
    result is available" contains it. The banner is matched whole now, and
    against the commit count this push enumerated — which is a verification
    rather than a string, and is what a scanner cannot accidentally satisfy."""
    clone, _ = _pushable_clone(tmp_path)
    _stub_scanner(clone, '#!/bin/bash\necho "No PII scan clean result is available"\nexit 0\n')
    _stage(clone, "leak.md", f"SSN: {FAKE_SSN}\n", force=True)
    _commit(clone, "deny coverage and leak")
    r = _push(clone, "HEAD:refs/heads/feature")
    assert r.returncode != 0, "a message denying coverage was read as coverage"
    assert "PUSH BLOCKED" in r.stdout


def test_every_ref_of_a_multi_ref_push_is_processed(tmp_path):
    """The scanner inherited the REMAINING ref lines as its stdin. Anything that
    consumed them — the scanner itself, or a tool it calls — made this loop's
    next `read` see EOF, and every ref after the first went out with neither a
    scan nor the destination guard.

    The stub eats stdin and then delegates, which is the whole mechanism: with
    the redirect in place it eats nothing and both refs are scanned.
    """
    clone, _ = _pushable_clone(tmp_path)
    shutil.copy(SCAN, clone / "scripts" / "real-scan.sh")
    os.chmod(clone / "scripts" / "real-scan.sh", 0o755)
    subprocess.run(["git", "add", "-f", "scripts/real-scan.sh"], cwd=clone,
                   check=True)
    # Eats stdin for the RANGE call only. The name check is handed its input on
    # a pipe, so a stub that ate that too would be caught by the name check
    # rather than by the case under test — which is what happened, and is the
    # count verification doing its job on the wrong question.
    _stub_scanner(clone, '#!/bin/bash\n'
                         'if [ "${1:-}" != "--names" ]; then cat > /dev/null; fi\n'
                         'exec "$(git rev-parse --show-toplevel)/scripts/real-scan.sh" "$@"\n')
    _commit(clone, "a scanner that reads stdin")

    r = _push(clone, "HEAD:refs/heads/one", "HEAD:refs/heads/two")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    # Counted on the RANGE banner, not on "PII scan clean" — the ref-name check
    # prints one of those too, so the looser count reached two from a single ref
    # and the case passed with the redirect removed.
    assert r.stdout.count("commit(s) inspected individually") == 2, (
        f"expected one scan per ref; the rest of the protocol was eaten:"
        f"\n{r.stdout}")


def test_a_tag_pointing_at_a_blob_is_refused(tmp_path):
    """`git rev-list <blob>` succeeds with EMPTY output, so a ref whose target
    is not a commit read as "publishes no commit" and went out carrying its
    whole content, unscanned. The old blanket refusal covered this."""
    clone, _ = _pushable_clone(tmp_path)
    (clone / "payload.txt").write_text(f"SSN: {FAKE_SSN}\n")
    blob = subprocess.run(["git", "hash-object", "-w", "payload.txt"],
                          cwd=clone, capture_output=True, text=True,
                          check=True).stdout.strip()
    (clone / "payload.txt").unlink()
    subprocess.run(["git", "tag", "payload", blob], cwd=clone, check=True)
    r = _push(clone, "refs/tags/payload")
    assert r.returncode != 0, "a blob was published with no scan"
    assert "not a commit" in r.stdout, r.stdout


def test_an_ssn_in_a_ref_name_blocks_the_push(tmp_path):
    """A ref name is published as-is and is not file content, so nothing
    downstream masks it — the pathname rule's argument exactly, and the reason
    that rule runs BEFORE any exemption. The rule stays in the scanner; the hook
    hands it the names.
    """
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "notes.md", "Nothing sensitive here.\n")
    _commit(clone, "ordinary work")
    r = _push(clone, f"HEAD:refs/heads/taxpayer-{FAKE_SSN}")
    assert r.returncode != 0, "a ref named after an SSN was published"
    assert FAKE_SSN not in r.stdout + r.stderr, (
        "the refusal printed the name it exists to keep off the wire")


def test_an_allowlisted_value_in_a_ref_name_still_pushes(tmp_path):
    """The counterweight. `000-00-0000` is allowlisted, and a name rule that
    ignored the allowlist would be a new way for the gate to cry wolf — on the
    synthetic values this repository's own fixtures use."""
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "notes.md", "Nothing sensitive here.\n")
    _commit(clone, "ordinary work")
    r = _push(clone, "HEAD:refs/heads/fixture-000-00-0000")
    assert r.returncode == 0, f"an allowlisted name was refused:\n{r.stdout}"


def test_only_the_published_ref_name_is_checked(tmp_path):
    """The LOCAL ref is not published, and checking it was a cry-wolf source.

    `git push <sha>:refs/heads/x` makes the local ref the sha itself, and about
    one 40-hex sha in eight holds a maximal run of exactly nine digits — the
    bare-SSN rule. This surfaced as a test that failed one run in eight, on a
    push carrying nothing sensitive at all: the same false-positive class the
    hashed-lock digest exemption exists for, arriving through a new caller.

    A local branch named after an SSN and pushed under a clean name publishes
    nothing sensitive, so it passes; the counterweight is the case above, where
    the name reaching the remote is the one that carries it.
    """
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "notes.md", "Nothing sensitive here.\n")
    _commit(clone, "ordinary work")
    subprocess.run(["git", "branch", f"local-{FAKE_SSN}"], cwd=clone, check=True)
    r = _push(clone, f"local-{FAKE_SSN}:refs/heads/clean-name")
    assert r.returncode == 0, (
        f"a local-only name blocked a push that publishes nothing sensitive:"
        f"\n{r.stdout}")


def test_the_destination_is_asked_rather_than_the_tracking_refs(tmp_path):
    """Remote-tracking refs are a local CACHE of what some remote held at the
    last fetch, which is a different question from what THIS push's destination
    has. `remote.origin.pushurl` separates the two by configuration: the range
    was bounded by what `origin` fetched from, and the commits went somewhere
    that had never seen them, unscanned and unannounced.

    Git hands the hook the real destination as its second argument.
    """
    clone, remote = _pushable_clone(tmp_path)
    _stage(clone, "leak.md", f"SSN: {FAKE_SSN}\n")
    _commit(clone, "leak")
    assert _push(clone, "--no-verify", "HEAD:refs/heads/work").returncode == 0

    elsewhere = tmp_path / "elsewhere.git"
    subprocess.run(["git", "init", "-q", "--bare", str(elsewhere)], check=True)
    subprocess.run(["git", "config", "remote.origin.pushurl", str(elsewhere)],
                   cwd=clone, check=True)
    r = _push(clone, "HEAD:refs/heads/feature")
    assert r.returncode != 0, (
        "the push went to a destination that has never seen the commit, "
        "bounded by what a different remote had")


def test_a_force_rewind_that_publishes_nothing_is_not_refused(tmp_path):
    """The counterweight to requiring a coverage line: a rewind moves the ref
    and publishes no commit, so the scan enumerates nothing and prints no
    banner. Reading that as an unperformed scan blocked a correct push, which is
    the cry-wolf shape that gets a gate switched off."""
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "one.md", "first\n")
    first = _commit(clone, "first")
    _stage(clone, "two.md", "second\n")
    _commit(clone, "second")
    assert _push(clone, "HEAD:refs/heads/feature").returncode == 0

    r = _push(clone, "--force", f"{first}:refs/heads/feature")
    assert r.returncode == 0, (
        f"a rewind publishing no commit was refused:\n{r.stdout}{r.stderr}")
    assert "publishes no new commit" in r.stdout, r.stdout


def _commit_at(repo: Path, msg: str, when: str) -> str:
    """A commit with a chosen committer date, which is what `git rev-list`
    orders by unless told otherwise."""
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", "commit", "-q", "--no-verify", "-m", msg], cwd=repo,
                   check=True, env=env)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()


def test_the_oldest_outgoing_commit_is_found_by_topology_not_by_date(tmp_path):
    """`git rev-list` orders by committer DATE by default, and a parent is not
    guaranteed to come after its children. Taking the last line as "oldest"
    therefore picked a DESCENDANT, whose parent — itself an outgoing commit —
    became the base and was excluded from the range.

    The graph is the smallest one that shows it: P introduces the value, two
    children each remove it, a merge joins them, and the dates run M > B > P > A
    so that default traversal emits A last. The tip is clean and both parents of
    the merge are clean; only P carries the SSN, and only a range that starts
    below P sees it.
    """
    clone, _ = _pushable_clone(tmp_path)
    _stage(clone, "notes.md", "Nothing sensitive here.\n")
    _commit(clone, "on the remote already")
    assert _push(clone, "HEAD:refs/heads/work").returncode == 0

    _stage(clone, "leak.md", f"SSN: {FAKE_SSN}\n")
    _commit_at(clone, "P introduces it", "2026-03-03T00:00:00Z")
    subprocess.run(["git", "branch", "-q", "side"], cwd=clone, check=True)

    (clone / "leak.md").write_text("removed\n")
    subprocess.run(["git", "add", "leak.md"], cwd=clone, check=True)
    _commit_at(clone, "A removes it", "2026-01-01T00:00:00Z")

    subprocess.run(["git", "checkout", "-q", "side"], cwd=clone, check=True)
    (clone / "leak.md").write_text("removed\n")
    subprocess.run(["git", "add", "leak.md"], cwd=clone, check=True)
    _commit_at(clone, "B removes it", "2026-04-04T00:00:00Z")

    subprocess.run(["git", "merge", "-q", "--no-ff", "-m", "M", "work"],
                   cwd=clone, check=True,
                   env={**os.environ, "GIT_AUTHOR_DATE": "2026-05-05T00:00:00Z",
                        "GIT_COMMITTER_DATE": "2026-05-05T00:00:00Z"})

    r = _push(clone, "HEAD:refs/heads/feature")
    assert r.returncode != 0, "the commit that introduced the value was not scanned"
    assert "PII pattern in content" in r.stdout, (
        "the range must have COVERED the introducing commit and the scan must "
        "have found it there. A coverage refusal instead means the base was "
        f"still wrong and only the self-check caught it:\n{r.stdout}")


def test_a_name_check_that_reports_nothing_is_not_read_as_a_check(tmp_path):
    """The `--names` mode has its own way of succeeding without working: given
    nothing on stdin it reports zero names checked and exits 0. So the hook
    verifies the count coming back, exactly as it does for the range scan —
    "exit 0 is not evidence" applies to the mode added to close that hole."""
    clone, _ = _pushable_clone(tmp_path)
    shutil.copy(SCAN, clone / "scripts" / "real-scan.sh")
    os.chmod(clone / "scripts" / "real-scan.sh", 0o755)
    subprocess.run(["git", "add", "-f", "scripts/real-scan.sh"], cwd=clone,
                   check=True)
    _stub_scanner(clone, '#!/bin/bash\n'
                         'if [ "${1:-}" = "--names" ]; then exit 0; fi\n'
                         'exec "$(git rev-parse --show-toplevel)/scripts/real-scan.sh" "$@"\n')
    _commit(clone, "a name check that checks nothing")

    r = _push(clone, f"HEAD:refs/heads/taxpayer-{FAKE_SSN}")
    assert r.returncode != 0, "a ref name went out with no check having happened"
    assert "reported nothing about the ref" in r.stdout, r.stdout
    # And the refusal must not repeat the name. Every message AFTER the check
    # may print it, because by then it has passed these patterns; this one
    # cannot, and the name is the candidate value — writing it into scrollback
    # is the disclosure the check exists to prevent. The first version of this
    # message named the ref, and this assertion is what found it.
    assert FAKE_SSN not in r.stdout + r.stderr


def test_a_scan_reporting_fewer_commits_than_the_push_publishes_blocks(tmp_path):
    """What the COUNT buys over "the banner is present". A scanner that
    inspected some of the push and reported cleanly on that is the partial-scan
    case, and it looks exactly like a complete one from outside."""
    clone, _ = _pushable_clone(tmp_path)
    _stub_scanner(clone, '#!/bin/bash\n'
                         'if [ "${1:-}" = "--names" ]; then\n'
                         '  echo "PII scan clean — 1 name(s) checked."; exit 0\n'
                         'fi\n'
                         'echo "PII scan clean — 0 commit(s) inspected individually,"\n'
                         'echo "0 changed file(s) checked."\n'
                         'exit 0\n')
    _stage(clone, "leak.md", f"SSN: {FAKE_SSN}\n", force=True)
    _commit(clone, "report a partial scan and leak")
    r = _push(clone, "HEAD:refs/heads/feature")
    assert r.returncode != 0, "a scan covering none of the push was read as clean"
    assert "reported inspecting 0 commit(s)" in r.stdout, r.stdout
