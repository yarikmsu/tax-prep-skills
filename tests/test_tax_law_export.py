"""The isolated cache copy the freshness auditor reads (V7).

That agent is the only one here with network access, and its "read
`tax_law_<year>.json` and nothing else" scope is a sentence in a prompt — `Read`
is measurably not directory-confined, so nothing enforces it. What is
enforceable is what the agent is pointed AT.

THE COPY IS FAITHFUL. The first draft filtered the cache against the schema, and
the auditor's job includes reporting keys the schema does not define: its item 6
is "nothing statutory-fixed has leaked in" and its item 8 requires `state.type`
as a positive declaration. The filter removed the very thing those findings are
made of. So nothing is dropped; the export REFUSES instead, and the counterweight
class below is as load-bearing as the refusals.
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents.lib.tax_law_export import (  # noqa: E402
    ExportError,
    build_audit_copy,
    identifier_findings,
)

# Assembled at runtime, never written as a literal — this file would otherwise
# trip the very gate whose patterns the export inherits.
_AREA, _GROUP, _SERIAL = "123", "45", "6789"
FAKE_SSN = f"{_AREA}-{_GROUP}-{_SERIAL}"
FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"

posix_only = pytest.mark.skipif(os.name != "posix",
                                reason="POSIX mode bits; Windows uses a DACL")


def _workspace(tmp_path: Path, cache: dict, year: int = 2026) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    (ws / f"tax_law_{year}.json").write_text(json.dumps(cache, indent=2),
                                             encoding="utf-8")
    return ws


def _minimal(year: int = 2026) -> dict:
    return {
        "tax_year": year,
        "federal": {
            "filing_statuses": {
                "single": {"standard_deduction": 15000,
                           "brackets": [{"rate": 0.1, "min": 0, "max": 1000}]},
            },
            "salt_cap": {"amount": 40000, "mfs_amount": 20000},
        },
        "state": {"code": "CA", "name": "California",
                  "type": "graduated_income"},
        "_sources": {"federal.salt_cap": "https://www.irs.gov/pub/p17.pdf"},
    }


def _dest(tmp_path: Path) -> Path:
    """An explicit destination for every test, so the suite leaves nothing in
    $TMPDIR. The default `mkdtemp` path has no cleanup, and fourteen tests using
    it left 225 directories behind on the machine that found this."""
    return tmp_path / "out"


# --- What must SURVIVE ------------------------------------------------------
#
# These come first on purpose. A refusal-only suite is satisfied by a module
# that refuses everything, and the failure this class exists for is the opposite
# one: an export that quietly resolves the auditor's findings for it.

class TestTheCopyIsFaithful:
    def test_a_clean_cache_is_copied_byte_for_byte(self, tmp_path):
        ws = _workspace(tmp_path, _minimal())
        target = build_audit_copy(2026, ws, dest=_dest(tmp_path))
        assert (target.read_text(encoding="utf-8")
                == (ws / "tax_law_2026.json").read_text(encoding="utf-8")), (
            "the copy must be the bytes that were validated — re-serialising "
            "would let key order and int/float identity drift")

    def test_a_key_the_schema_does_not_define_survives(self, tmp_path):
        """The auditor's item 6: a statutory-fixed constant that has leaked into
        the cache is a FINDING. Dropping it makes the finding unreachable."""
        cache = _minimal()
        cache["federal"]["safe_harbor_threshold"] = 150000
        ws = _workspace(tmp_path, cache)
        target = build_audit_copy(2026, ws, dest=_dest(tmp_path))
        copy = json.loads(target.read_text(encoding="utf-8"))
        assert copy["federal"]["safe_harbor_threshold"] == 150000

    def test_a_state_type_the_template_does_not_carry_survives(self, tmp_path):
        """The auditor's item 8, and the case that made this concrete: a cache
        falsely declaring Colorado a no-income-tax state arrived as a clean
        generic block, because CO's template has no `type` key to match."""
        cache = _minimal()
        cache["state"] = {"code": "CO", "name": "Colorado", "type": "none"}
        ws = _workspace(tmp_path, cache)
        target = build_audit_copy(2026, ws, dest=_dest(tmp_path))
        assert json.loads(target.read_text(encoding="utf-8"))["state"]["type"] == "none"

    def test_a_ptet_block_and_a_historical_rate_survive(self, tmp_path):
        """Both are dynamic-key blocks whose seeds ship empty, so a schema walk
        had nothing to match them against and dropped every entry."""
        cache = _minimal()
        cache["ptet"] = {"CA": {"schedule": [{"rate": 0.093}], "vintage": "2026"}}
        cache["federal"] = dict(cache["federal"], historical_top_rate={"2017": 0.396})
        ws = _workspace(tmp_path, cache)
        target = build_audit_copy(2026, ws, dest=_dest(tmp_path))
        copy = json.loads(target.read_text(encoding="utf-8"))
        assert copy["ptet"]["CA"]["schedule"] == [{"rate": 0.093}]
        assert copy["federal"]["historical_top_rate"] == {"2017": 0.396}

    def test_prose_and_an_unauthoritative_pin_survive(self, tmp_path):
        """Neither is this module's business. A source host is the auditor's
        item 3, and it judges one better than a regex does; prose is what a
        `note` field is for. What this module refuses is narrower and stated."""
        cache = _minimal()
        cache["state"]["note"] = "CA does not recognize HSA."
        cache["_sources"]["federal.bad"] = "https://irs.gov.evil.example.com/x"
        ws = _workspace(tmp_path, cache)
        target = build_audit_copy(2026, ws, dest=_dest(tmp_path))
        copy = json.loads(target.read_text(encoding="utf-8"))
        assert copy["state"]["note"].startswith("CA does not")
        assert "federal.bad" in copy["_sources"]


# --- What must be REFUSED ---------------------------------------------------

class TestTheExportRefuses:
    @pytest.mark.parametrize("plant", [
        pytest.param(lambda c: c["state"].__setitem__("name", f"CA {FAKE_SSN}"),
                     id="a-schema-known-string-field"),
        pytest.param(lambda c: c["federal"].__setitem__(FAKE_SSN, 1),
                     id="a-key"),
        pytest.param(
            lambda c: c["federal"]["filing_statuses"]["single"]["brackets"][0]
            .__setitem__("memo", f"client {FAKE_SSN}"),
            id="inside-a-list-element"),
        pytest.param(
            lambda c: c["_sources"].__setitem__(
                "federal.x", f"https://www.irs.gov/l?c={FAKE_SSN}"),
            id="a-url-query-string"),
        pytest.param(lambda c: c.__setitem__(
            "_provenance", {"federal.salt_cap": {"source": f"https://x.gov/{FAKE_SSN}"}}),
            id="under-provenance"),
        pytest.param(lambda c: c["federal"].__setitem__("k", FAKE_AWS_KEY),
                     id="a-credential"),
    ])
    def test_ssn_shaped_text_anywhere_blocks_the_copy(self, tmp_path, plant):
        cache = _minimal()
        plant(cache)
        ws = _workspace(tmp_path, cache)
        with pytest.raises(ExportError) as e:
            build_audit_copy(2026, ws, dest=_dest(tmp_path))
        assert FAKE_SSN not in str(e.value) and FAKE_AWS_KEY not in str(e.value), (
            "the refusal echoed the value it exists to keep off the wire")
        assert not _dest(tmp_path).exists() or not any(_dest(tmp_path).iterdir()), (
            "a refusal must leave no copy behind")

    def test_ordinary_cache_content_does_not_block(self, tmp_path):
        """The counterweight, and it has to be the RIGHT one. `.pii-allowlist`
        is read by the shell gate, not by `secure_fs.redact`, so a synthetic
        `000-00-0000` is refused here like any other — that is a real property
        of using the redactor as a detector and pretending otherwise would put a
        false claim in a test name. What must not trip it is what a cache
        actually holds: six-figure thresholds, rates, years, and IRS URLs."""
        cache = _minimal()
        cache["federal"]["irmaa"] = {"tiers": [
            {"magi_threshold": 206000, "annual_surcharge_per_person": 1038.0}]}
        cache["federal"]["underpayment_rates"] = [
            {"annual_rate": 0.08, "effective_from": "2026-01-01"}]
        cache["_sources"]["federal.irmaa"] = (
            "https://www.irs.gov/pub/irs-drop/rp-24-40.pdf")
        ws = _workspace(tmp_path, cache)
        assert build_audit_copy(2026, ws, dest=_dest(tmp_path)).is_file()

    def test_a_synthetic_ssn_is_refused_like_any_other(self, tmp_path):
        """Stated rather than discovered: the redactor has no allowlist, so this
        is a refusal a contributor can hit with a fixture. The message says the
        cache is fetched from .gov and has no business holding one, which is the
        actionable form of the same fact."""
        cache = _minimal()
        cache["state"]["name"] = "California 000-00-0000"
        ws = _workspace(tmp_path, cache)
        with pytest.raises(ExportError, match="re-run /tax-setup"):
            build_audit_copy(2026, ws, dest=_dest(tmp_path))

    def test_a_symlinked_cache_is_refused(self, tmp_path):
        """`tax_law_<year>.json` pointed at `taxpayer_profile.json` would be
        copied by the one command whose job is keeping that file away from a
        network-capable agent."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "taxpayer_profile.json").write_text('{"name": "Jane"}',
                                                  encoding="utf-8")
        try:
            (ws / "tax_law_2026.json").symlink_to(ws / "taxpayer_profile.json")
        except (OSError, NotImplementedError):  # pragma: no cover - Windows
            pytest.skip("symlinks unavailable")
        with pytest.raises(ExportError, match="symlink or junction"):
            build_audit_copy(2026, ws, dest=_dest(tmp_path))

    def test_a_non_finite_number_is_refused(self, tmp_path):
        """`json.loads` admits NaN and Infinity, so re-emitting one produces a
        file a strict reader rejects — a clean-looking export the auditor cannot
        open, or opens and reads `Infinity` as a rate."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "tax_law_2026.json").write_text(
            '{"tax_year": 2026, "federal": {"salt_cap": {"amount": NaN}}}',
            encoding="utf-8")
        with pytest.raises(ExportError, match="non-finite"):
            build_audit_copy(2026, ws, dest=_dest(tmp_path))

    def test_a_missing_cache_is_an_error_not_an_empty_copy(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        with pytest.raises(ExportError):
            build_audit_copy(2026, ws, dest=_dest(tmp_path))


# --- Where the copy goes ----------------------------------------------------

class TestTheDestination:
    def test_a_dest_inside_the_workspace_is_refused(self, tmp_path):
        """The first draft's `mkdir(exist_ok=True)` plus a same-basename target
        OVERWROTE the live cache when `--dest` was the workspace, and then
        printed the workspace as the directory to point the auditor at."""
        ws = _workspace(tmp_path, _minimal())
        before = (ws / "tax_law_2026.json").read_text(encoding="utf-8")
        with pytest.raises(ExportError, match="inside the workspace"):
            build_audit_copy(2026, ws, dest=ws)
        assert (ws / "tax_law_2026.json").read_text(encoding="utf-8") == before

    def test_an_empty_dest_is_refused(self, tmp_path):
        """`Path("")` is `.` — it chmodded the current directory 0700 and
        printed "point the auditor at your CWD"."""
        ws = _workspace(tmp_path, _minimal())
        with pytest.raises(ExportError, match="empty"):
            build_audit_copy(2026, ws, dest="")

    def test_a_dest_that_already_holds_files_is_refused(self, tmp_path):
        """`review-subagent-acceptance.md` records this in its own words: one
        directory, one file, named. Two caches in a directory and the auditor
        reports on whichever it opened."""
        ws = _workspace(tmp_path, _minimal())
        dest = _dest(tmp_path)
        dest.mkdir()
        (dest / "tax_law_2030.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ExportError, match="already holds files"):
            build_audit_copy(2026, ws, dest=dest)

    def test_the_default_destination_is_fresh_each_time(self, tmp_path):
        """Unpredictable by name, so two exports of the same year from two
        workspaces cannot collide in a shared directory."""
        ws = _workspace(tmp_path, _minimal())
        first = build_audit_copy(2026, ws)
        second = build_audit_copy(2026, ws)
        try:
            assert first.parent != second.parent
        finally:
            for t in (first, second):
                for p in t.parent.iterdir():
                    p.unlink()
                t.parent.rmdir()

    @posix_only
    def test_a_caller_supplied_destination_is_secured(self, tmp_path):
        """`mkdtemp` is 0700 already, so the default path proves nothing about
        the call that makes it so. A `--dest` is created with the ambient umask,
        and it holds a file named after the year of somebody's return."""
        ws = _workspace(tmp_path, _minimal())
        dest = _dest(tmp_path)
        dest.mkdir(mode=0o755)
        target = build_audit_copy(2026, ws, dest=dest)
        assert stat.S_IMODE(dest.stat().st_mode) == 0o700
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_a_sentinel_taxpayer_file_is_not_in_the_copy(self, tmp_path):
        """V7's first acceptance criterion. What is not in the directory is not
        in reach — the enforceable half of a scope that is otherwise a sentence
        in a prompt."""
        ws = _workspace(tmp_path, _minimal())
        (ws / "taxpayer_profile.json").write_text('{"ssn_last4": "XXX-XX-6789"}',
                                                  encoding="utf-8")
        (ws / "result.json").write_text("{}", encoding="utf-8")
        target = build_audit_copy(2026, ws, dest=_dest(tmp_path))
        assert sorted(p.name for p in target.parent.iterdir()) == ["tax_law_2026.json"]


# --- The scanner itself -----------------------------------------------------

class TestTheScanner:
    def test_it_reports_a_path_and_never_a_value(self):
        found = identifier_findings({"a": {"b": f"SSN {FAKE_SSN}"}})
        assert found == ["a.b"]

    def test_a_key_is_reported_as_a_key_and_redacted(self):
        found = identifier_findings({"federal": {FAKE_SSN: 1}})
        assert found == ["federal.[REDACTED] (in the key)"]
        assert FAKE_SSN not in found[0]

    def test_it_walks_into_lists_by_index(self):
        found = identifier_findings({"xs": [{"ok": 1}, {"bad": FAKE_SSN}]})
        assert found == ["xs[1].bad"]

    def test_a_clean_tree_reports_nothing(self):
        assert identifier_findings(_minimal()) == []


# --- The CLI ----------------------------------------------------------------

def _cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "agents/lib/tax_law_export.py"), *args],
        capture_output=True, text=True)


def test_the_cli_exits_0_and_prints_the_directory(tmp_path):
    ws = _workspace(tmp_path, _minimal())
    r = _cli("2026", str(ws), f"--dest={_dest(tmp_path)}")
    assert r.returncode == 0, r.stderr
    printed = Path(r.stdout.strip().splitlines()[-1])
    assert (printed / "tax_law_2026.json").is_file()
    assert "rm -rf" in r.stderr, "the operator is never told to remove the copy"


def test_the_cli_exits_2_when_it_refuses(tmp_path):
    """2 is BLOCKED, the house meaning everywhere else here. The first draft
    used it for "copied, with findings", which is the opposite, so a wrapper
    written to the house rule would have discarded a valid copy."""
    cache = _minimal()
    cache["state"]["name"] = f"CA {FAKE_SSN}"
    ws = _workspace(tmp_path, cache)
    r = _cli("2026", str(ws), f"--dest={_dest(tmp_path)}")
    assert r.returncode == 2, r.stdout + r.stderr
    assert FAKE_SSN not in r.stdout + r.stderr


def test_the_cli_exits_1_when_it_cannot_run(tmp_path):
    """A missing cache is neither a block nor a pass. The `except` arm carrying
    WorkspaceError was never executed by any test until this one."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    r = _cli("2026", str(ws), f"--dest={_dest(tmp_path)}")
    assert r.returncode == 2, r.stdout + r.stderr


def test_the_cli_accepts_the_space_form_of_dest(tmp_path):
    """The hand-rolled parser silently IGNORED `--dest /tmp/x` and wrote to an
    unnamed temp directory instead, which is a copy nobody knows to delete."""
    ws = _workspace(tmp_path, _minimal())
    r = _cli("2026", str(ws), "--dest", str(_dest(tmp_path)))
    assert r.returncode == 0, r.stderr
    assert (_dest(tmp_path) / "tax_law_2026.json").is_file()


def test_a_year_out_of_range_is_a_line_not_a_traceback(tmp_path):
    ws = _workspace(tmp_path, _minimal())
    r = _cli("1999", str(ws), f"--dest={_dest(tmp_path)}")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr
