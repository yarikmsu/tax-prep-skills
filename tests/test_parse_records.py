"""Per-document parse records and the ledger derived from them (V2).

`parse_ledger.json` is the tie-out gate's evidence that every document landed on
a return line, and the AGENT used to write it: one hand-assembled object holding
every document at once, produced at the end of a parse from summaries written
earlier. A transcription step across documents, at the one place whose whole
purpose is to prove no transcription was lost.

What these pin is the two properties that makes deriving it worth anything:

1. **The rules are the GATE'S rules, called rather than copied.** Every check
   here is `tie_out.load_ledger`. A second implementation of "what a record must
   look like" would be a second thing to keep true, and the copy that drifts is
   always the one the gate does not read — so a test drives a rule only
   `tie_out` knows and requires it to be enforced at write time.
2. **Coverage is checked while the agent still has the document open.** The gate
   already fails on a document with no record, at `tax-compute` step 12.7, after
   the parse is over and the arithmetic is done. Failing at build time is the
   entire reason to derive rather than assemble.
"""

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents.lib import parse_records as PR  # noqa: E402
from agents.lib import tie_out as T  # noqa: E402


def _workspace(tmp_path, *documents):
    ws = tmp_path / "ws"
    (ws / "documents").mkdir(parents=True)
    (ws / "summaries").mkdir()
    for name in documents:
        (ws / "documents" / name).write_bytes(b"%PDF-1.4 synthetic\n")
    return ws


def _record(doc_id="w2-acme", file="documents/W2.pdf", **overrides):
    record = {"doc_id": doc_id, "file": file, "form_type": "W-2",
              "payer": "Acme", "person": "primary",
              "key_boxes": {"wages": 100000.0}}
    record.update(overrides)
    return record


class TestARecordIsCheckedByTheGatesOwnRules:
    def test_a_valid_record_is_written_and_is_owner_only(self, tmp_path):
        ws = _workspace(tmp_path, "W2.pdf")
        path = PR.write_record(ws, _record())
        assert path.is_file()
        assert oct(os.stat(path).st_mode & 0o777) == "0o600", (
            "it carries dollar figures and a payer; AGENTS.md §1 applies")

    def test_a_skipped_record_carrying_amounts_is_refused(self, tmp_path):
        """A `tie_out` rule, enforced here. Amounts on a skipped record earn
        coverage credit while vanishing from the sum checks — the exact
        intake-drop the gate exists to catch."""
        ws = _workspace(tmp_path, "W2.pdf")
        with pytest.raises(PR.ParseRecordError) as raised:
            PR.write_record(ws, _record(skipped="duplicate scan"))
        assert "SKIPPED" in str(raised.value)

    def test_an_empty_parse_is_refused(self, tmp_path):
        ws = _workspace(tmp_path, "W2.pdf")
        with pytest.raises(PR.ParseRecordError):
            PR.write_record(ws, _record(key_boxes={}))

    def test_an_unknown_CATEGORY_is_refused_at_write_time(self, tmp_path):
        """The rule only `tie_out` knows, which is why it is the one worth
        driving: if this module re-implemented the checks instead of calling the
        gate's loader, this is the assertion that would go red first."""
        ws = _workspace(tmp_path, "W2.pdf")
        with pytest.raises(PR.ParseRecordError) as raised:
            PR.write_record(ws, _record(key_boxes={"wagez": 100000.0}))
        assert "wagez" in str(raised.value)

    @pytest.mark.parametrize("field", ["key_box", "form", "notez"])
    def test_a_MISSPELT_field_is_refused_rather_than_ignored(self, tmp_path, field):
        """Ignoring it would leave a record with no amounts at all, which reads
        as an empty parse — and an empty parse nobody typed on purpose is the
        drop this chain exists to catch."""
        ws = _workspace(tmp_path, "W2.pdf")
        with pytest.raises(PR.ParseRecordError) as raised:
            PR.write_record(ws, _record(**{field: "x"}))
        assert field in str(raised.value)

    def test_a_bad_record_never_reaches_the_disk(self, tmp_path):
        ws = _workspace(tmp_path, "W2.pdf")
        with pytest.raises(PR.ParseRecordError):
            PR.write_record(ws, _record(key_boxes={}))
        assert not list((ws / "summaries").glob("*.record.json")), (
            "a malformed record must not sit on disk waiting to fail the gate")


class TestTheLedgerIsDerivedAndNotTranscribed:
    def _full(self, tmp_path):
        ws = _workspace(tmp_path, "W2.pdf", "1099INT.pdf")
        PR.write_record(ws, _record())
        PR.write_record(ws, _record(doc_id="int-bank", file="documents/1099INT.pdf",
                                    form_type="1099-INT", payer="Bank",
                                    key_boxes={"taxable_interest": 1250.0}))
        return ws

    def test_it_writes_a_ledger_the_real_gate_accepts(self, tmp_path):
        ws = self._full(tmp_path)
        path = PR.write_ledger(ws)
        loaded = T.load_ledger(json.loads(path.read_text(encoding="utf-8")))
        assert {r.doc_id for r in loaded} == {"w2-acme", "int-bank"}

    def test_the_ledger_is_owner_only(self, tmp_path):
        path = PR.write_ledger(self._full(tmp_path))
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"

    def test_the_human_and_second_read_fields_do_NOT_reach_the_ledger(self, tmp_path):
        """`boxes` is the labels as read and `notes` is prose for a person. The
        gate's loader rejects an unknown field, so carrying them through would
        make every derived ledger invalid — and they are not evidence about a
        return line either way."""
        ws = _workspace(tmp_path, "W2.pdf")
        PR.write_record(ws, _record(boxes={"1": "100,000.00"}, notes="clean"))
        ledger = PR.build_ledger(ws)
        assert ledger["records"][0].keys() <= PR.LEDGER_FIELDS
        assert "boxes" not in ledger["records"][0]

    def test_the_record_KEEPS_them(self, tmp_path):
        """They are why the record exists as well as the ledger: two independent
        extractions are diffed on `boxes` with `==`, where comparing two prose
        summaries asks a reader to spot a moved digit."""
        ws = _workspace(tmp_path, "W2.pdf")
        path = PR.write_record(ws, _record(boxes={"1": "100,000.00"}, notes="clean"))
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["boxes"] == {"1": "100,000.00"}
        assert on_disk["notes"] == "clean"

    def test_the_records_are_read_in_a_stable_order(self, tmp_path):
        ws = self._full(tmp_path)
        assert [r["doc_id"] for r in PR.build_ledger(ws)["records"]] == \
            [r["doc_id"] for r in PR.build_ledger(ws)["records"]]


class TestCoverageIsCheckedWhileTheDocumentIsStillOpen:
    def test_a_document_with_no_record_is_refused_and_NAMED(self, tmp_path):
        ws = _workspace(tmp_path, "W2.pdf", "1099INT.pdf")
        PR.write_record(ws, _record())
        with pytest.raises(PR.ParseRecordError) as raised:
            PR.build_ledger(ws)
        assert "1099INT.pdf" in str(raised.value)
        assert "no record" in str(raised.value)

    def test_a_record_naming_a_document_that_is_not_there_is_refused(self, tmp_path):
        """Coverage credit for amounts with no source. The gate catches it too,
        after the arithmetic."""
        ws = _workspace(tmp_path, "W2.pdf")
        PR.write_record(ws, _record())
        PR.write_record(ws, _record(doc_id="ghost", file="documents/Nope.pdf",
                                    key_boxes={"taxable_interest": 1.0}))
        with pytest.raises(PR.ParseRecordError) as raised:
            PR.build_ledger(ws)
        assert "Nope.pdf" in str(raised.value)

    def test_a_SKIPPED_document_counts_as_covered(self, tmp_path):
        """Parsed or skipped-with-a-reason. A skip is an answer; only a file
        with neither is the drop."""
        ws = _workspace(tmp_path, "W2.pdf", "Duplicate.pdf")
        PR.write_record(ws, _record())
        PR.write_record(ws, _record(doc_id="dupe", file="documents/Duplicate.pdf",
                                    key_boxes={}, skipped="duplicate scan"))
        assert len(PR.build_ledger(ws)["records"]) == 2

    def test_two_records_for_ONE_document_are_refused(self, tmp_path):
        """The check a per-record pass structurally cannot make. Coverage still
        passes with a duplicate, and the document's amounts are counted twice."""
        ws = _workspace(tmp_path, "W2.pdf")
        PR.write_record(ws, _record(doc_id="a"))
        PR.write_record(ws, _record(doc_id="b"))
        with pytest.raises(PR.ParseRecordError) as raised:
            PR.build_ledger(ws)
        assert "duplicate file" in str(raised.value).lower()

    def test_a_dotfile_in_documents_is_not_a_document(self, tmp_path):
        ws = _workspace(tmp_path, "W2.pdf")
        (ws / "documents" / ".DS_Store").write_bytes(b"junk")
        PR.write_record(ws, _record())
        assert len(PR.build_ledger(ws)["records"]) == 1


class TestTheCli:
    def _run(self, ws, *args):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = PR._main(["--workspace", str(ws), *args])
        return code, out.getvalue() + err.getvalue()

    def test_a_complete_set_builds_clean(self, tmp_path):
        ws = _workspace(tmp_path, "W2.pdf")
        PR.write_record(ws, _record())
        code, out = self._run(ws, "--build-ledger")
        assert code == 0
        assert "parse_ledger.json" in out
        assert (ws / "parse_ledger.json").is_file()

    def test_a_gap_is_BLOCKED_on_the_gates_own_code(self, tmp_path):
        """2, because this IS the tie-out's coverage check arriving early — the
        same failure, named at the document rather than after the arithmetic."""
        ws = _workspace(tmp_path, "W2.pdf")
        code, out = self._run(ws, "--build-ledger")
        assert code == 2
        assert "BLOCKED" in out
        assert not (ws / "parse_ledger.json").exists(), (
            "a refused build must not leave a partial ledger behind")

    def test_check_writes_nothing(self, tmp_path):
        ws = _workspace(tmp_path, "W2.pdf")
        PR.write_record(ws, _record())
        assert self._run(ws, "--check")[0] == 0
        assert not (ws / "parse_ledger.json").exists()

    def test_a_missing_workspace_is_could_not_run(self, tmp_path):
        assert self._run(tmp_path / "nope", "--build-ledger")[0] == 1

    def test_a_bad_flag_is_could_not_run_and_not_blocked(self, tmp_path):
        assert self._run(_workspace(tmp_path), "--buildledger")[0] == 1

    def test_no_argument_reports_usage(self, tmp_path):
        assert self._run(_workspace(tmp_path))[0] == 1

    def test_an_unreadable_record_is_reported_without_a_traceback(self, tmp_path):
        ws = _workspace(tmp_path, "W2.pdf")
        (ws / "summaries" / "broken.record.json").write_text("{not json",
                                                             encoding="utf-8")
        code, out = self._run(ws, "--check")
        assert code == 2
        assert "broken" in out


class TestItDerivesTheEvalFixturesLedger:
    """End to end, against the workspace the eval harness builds — which is the
    only place in this repository with real synthetic documents to cover."""

    def test_every_fixture_document_can_be_covered_and_the_gate_accepts_it(self, tmp_path):
        sys.path.insert(0, str(REPO))
        from evals import fixture as F
        ws = tmp_path / "fixture"
        F.build_workspace(ws, "ca_mfj_2026")
        boxes = {
            "W2_Acme.pdf": ("w2", "W-2", {"wages": 185000.0,
                                          "federal_withholding_w2": 28000.0}),
            "1099INT_Meridian.pdf": ("int", "1099-INT", {"taxable_interest": 1250.0}),
            "1099DIV_Northgate.pdf": ("div", "1099-DIV",
                                      {"ordinary_dividends": 3400.0}),
            "1099B_Northgate.pdf": ("b", "1099-B", {"capital_proceeds": 21000.0}),
        }
        for name, (doc_id, form, key_boxes) in boxes.items():
            PR.write_record(ws, {"doc_id": doc_id, "file": f"documents/{name}",
                                 "form_type": form, "payer": "synthetic",
                                 "person": "joint", "key_boxes": key_boxes})
        path = PR.write_ledger(ws)
        assert len(T.load_ledger(json.loads(path.read_text(encoding="utf-8")))) == 4
