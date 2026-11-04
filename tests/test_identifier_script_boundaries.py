"""AA107 task 3: touching digits cannot hide a different decimal family."""
import os
from pathlib import Path
import shutil
import subprocess
import unicodedata

import pytest

from agents.lib.secure_fs import _ID_DECIMAL_16
from tests.test_pii_content_scan import FAKE_SSN, SCAN, _commit, _make_repo, _run, _stage


def _digits(zero, value=FAKE_SSN.replace("-", "")):
    return "".join(chr(zero + int(c)) for c in value)


def _zeroes():
    return sorted({n for n in range(0x110000) if unicodedata.decimal(chr(n), None) == 0} | set(_ID_DECIMAL_16))


def _tokenize(tmp_path, lines, awk="awk"):
    """Run the production tokenizer with record numbers on its output only.
    Recognition and source offsets come from the real script, not a mirror."""
    source = SCAN.read_text()
    definitions = source[source.index("SSN_DASH="):source.index("# Bare decimal tokens")]
    program = source[source.index("_ssn_matches() {"):source.index("count_hits() {")]
    assert program.count("print text(a,b-a+1)") == 1
    program = program.replace("print text(a,b-a+1)", 'print NR ":" text(a,b-a+1)')
    program = program.replace(" | awk ", " | " + awk + " ")
    error = tmp_path / "tokenizer-error"
    result = subprocess.run(["bash", "-c", definitions + "\n" + program + '\n_ssn_matches "$(cat)"'],
                            input="\n".join(lines) + "\n", capture_output=True, text=True,
                            env={**os.environ, "LC_ALL": "C", "GREP_ERR": str(error)}, timeout=30)
    assert result.returncode == 0 and not error.exists(), "the production tokenizer failed"
    return [(int(number) - 1, value) for number, value in
            (line.split(":", 1) for line in result.stdout.splitlines())]


@pytest.mark.parametrize("awk", ["awk", "gawk", "mawk"])
def test_every_decimal_family_retains_its_boundary_beside_another_family(tmp_path, awk):
    if not shutil.which(awk):
        pytest.skip("optional awk implementation unavailable")
    bases = _zeroes()
    lines, expected = [], []
    for index, zero in enumerate(bases):
        value = _digits(zero)
        # Adjacent families include the mathematical styles and Unicode 16's
        # contiguous Myanmar sets; a UTF-8 prefix is not a family identifier.
        neighbour = chr(bases[(index + 1) % len(bases)] + 1)
        for gap in ("", "\u200b", " "):
            spelling = gap.join(value)
            for before in (False, True):
                lines.append(neighbour + spelling if before else spelling + neighbour)
                expected.append(spelling)
    matches = _tokenize(tmp_path, lines, awk)
    found = {index for index, value in matches if value == expected[index]}
    assert found == set(range(len(lines))), "a decimal-family boundary was lost"


def test_mixed_script_identifiers_and_same_family_longer_numbers_keep_their_readings(tmp_path):
    bases = _zeroes()
    mixed = ["".join(chr(bases[(start + i) % len(bases)] + int(c))
                     for i, c in enumerate(FAKE_SSN.replace("-", ""))) for start in range(len(bases))]
    ten = "01234" + "56789"
    longer = [_digits(zero, ten[offset:] + ten[:offset]) for zero in bases for offset in range(10)]
    matches = _tokenize(tmp_path, mixed + longer)
    indices = {index for index, _ in matches}
    assert indices == set(range(len(mixed))), "an existing mixed-script mask or a longer-number boundary changed"


@pytest.mark.parametrize("location", ["content", "pathname", "ref", "deleted history"])
@pytest.mark.parametrize("before", [False, True])
def test_the_real_gate_blocks_foreign_digits_touching_another_script(tmp_path, location, before):
    value = _digits(0x0660)
    touching = chr(0x0966 + 1)
    value = touching + value if before else value + touching
    repo = _make_repo(tmp_path)
    if location in {"ref", "deleted history"}:
        base = _commit(repo, "seed")
    if location == "ref":
        result = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", "--names", base], cwd=repo,
                                input="refs/heads/" + value + "\n", capture_output=True, text=True)
    else:
        _stage(repo, f"tests/fixtures/{value}.md" if location == "pathname" else "notes.md",
               "ordinary work\n" if location == "pathname" else value + "\n")
        if location == "deleted history":
            _commit(repo, "synthetic example")
            _stage(repo, "notes.md", "ordinary work\n")
            head = _commit(repo, "remove example")
            result = subprocess.run(["bash", "scripts/pre-commit-pii-scan.sh", f"{base}...{head}"],
                                    cwd=repo, capture_output=True, text=True)
        else:
            result = _run(repo)
    assert result.returncode == 1, "a touching digit concealed a foreign identifier"
    assert value not in result.stdout + result.stderr, "the refusal disclosed the value"


def test_allowlisting_uses_the_exact_source_span_at_a_script_boundary(tmp_path):
    repo = _make_repo(tmp_path)
    identifier = _digits(0x0660)
    value = identifier + chr(0x0966 + 1)
    _stage(repo, "notes.md", value + "\n")
    allow = Path(repo / ".pii-allowlist")
    original = allow.read_text()
    _stage(repo, ".pii-allowlist", original + "\n" + value + "\n")
    assert _run(repo).returncode == 1, "allowlisting a larger token excused its identifier span"
    _stage(repo, ".pii-allowlist", original + "\n" + identifier + "\n")
    assert _run(repo).returncode == 0, "an exact source-spelling exemption was lost"
