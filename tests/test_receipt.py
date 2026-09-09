#!/usr/bin/env python3
"""Tests for agents/lib/receipt.py — the validation receipt (V8).

The gates pass against a workspace as it stood when they ran. What these pin is
the BINDING: each gate's verdict is tied to the digests of the files that gate
read, so a document arriving afterwards, a figure edited afterwards, or a law
re-fetched afterwards makes the receipt stale — and a filing package assembled
on it is refused rather than quietly describing a return nobody validated.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import receipt  # noqa: E402
from agents.lib.receipt import ReceiptError, verify  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def _workspace(tmp_path, *, documents=("w2.pdf",)):
    """A workspace with everything the three gates read."""
    (tmp_path / "taxpayer_profile.json").write_text(json.dumps({"filing_status": "MFJ"}))
    (tmp_path / "parse_ledger.json").write_text(json.dumps({"documents": []}))
    (tmp_path / "result.json").write_text(json.dumps({"meta": {"tax_year": 2026}}))
    (tmp_path / "tax_law_2026.json").write_text(json.dumps({"tax_year": 2026}))
    if documents:
        (tmp_path / "documents").mkdir(exist_ok=True)
        for i, name in enumerate(documents):
            (tmp_path / "documents" / name).write_bytes(b"%PDF-1.4 " + str(i).encode())
    return tmp_path


def _passing(tmp_path):
    """Every REQUIRED gate recorded as a pass — read from the constant, so a
    gate added without a thought about this file makes these tests fail rather
    than quietly certify a workspace two of three gates have seen."""
    for gate in receipt.REQUIRED_GATES:
        receipt.record(tmp_path, gate, receipt.PASS)
    return tmp_path


def _run(*args):
    return subprocess.run([sys.executable, "-m", "agents.lib.receipt", *args],
                          capture_output=True, text=True, cwd=REPO)


class TestWhatARecordHolds:

    def test_a_gate_records_the_digest_of_every_file_it_read(self, tmp_path):
        _workspace(tmp_path)
        receipt.record(tmp_path, "tie_out", receipt.PASS)
        data = json.loads((tmp_path / receipt.RECEIPT_NAME).read_text())
        entry = data["gates"]["tie_out"]
        assert entry["outcome"] == "PASS"
        # Keyed by a DIGEST of the name — a document can be called `W2_<ssn>.pdf`
        # and this file is written to disk, where §1 forbids a full identifier —
        # with the redacted display name beside it for the messages.
        assert {v["name"] for v in entry["inputs"].values()} == {
            "taxpayer_profile.json", "parse_ledger.json", "result.json",
            "tax_law_2026.json", str(Path("documents") / "w2.pdf")}
        assert all(len(k) == 64 for k in entry["inputs"])
        assert entry["engine"] == receipt.engine_digest()
        assert data["receipt_version"] == receipt.RECEIPT_VERSION

    def test_the_capability_gate_records_only_the_profile(self, tmp_path):
        """It runs at Step 0.7, before result.json exists, and it reads the
        profile's flags and nothing else. A gate invalidated by a file it never
        read is the cry-wolf shape this receipt is written to avoid."""
        _workspace(tmp_path)
        receipt.record(tmp_path, "capabilities", receipt.PASS)
        entry = json.loads((tmp_path / receipt.RECEIPT_NAME).read_text())["gates"]["capabilities"]
        assert [v["name"] for v in entry["inputs"].values()] == ["taxpayer_profile.json"]

    def test_a_later_gate_does_not_erase_an_earlier_one(self, tmp_path):
        """They run at opposite ends of /tax-compute — Step 0.7, then 12.6 and
        12.7 — and each entry keeps the inputs ITS own run saw."""
        _passing(_workspace(tmp_path))
        gates = json.loads((tmp_path / receipt.RECEIPT_NAME).read_text())["gates"]
        assert set(gates) == set(receipt.REQUIRED_GATES)

    def test_the_receipt_is_owner_only(self, tmp_path):
        """It names the taxpayer's own documents — `documents/W2_Acme.pdf` names
        an employer before anyone opens it (AGENTS.md §1's listing rule)."""
        if os.name == "nt":  # pragma: no cover - POSIX bits are meaningless here
            pytest.skip("POSIX mode bits")
        old = os.umask(0o022)   # without this the assertion is vacuous
        try:
            _workspace(tmp_path)
            path = receipt.record(tmp_path, "tie_out", receipt.PASS)
        finally:
            os.umask(old)
        assert (path.stat().st_mode & 0o777) == 0o600

    def test_an_unknown_gate_is_a_refusal(self, tmp_path):
        with pytest.raises(ReceiptError, match="unknown gate"):
            receipt.record(tmp_path, "spelling", receipt.PASS)

    def test_a_hand_edited_receipt_is_a_refusal_not_a_guess(self, tmp_path):
        _workspace(tmp_path)
        (tmp_path / receipt.RECEIPT_NAME).write_text("{not json")
        with pytest.raises(ReceiptError, match="readable JSON"):
            receipt.record(tmp_path, "tie_out", receipt.PASS)


class TestWhatMakesAReceiptStale:

    def test_a_workspace_where_the_gates_ran_and_nothing_moved_is_valid(self, tmp_path):
        assert verify(_passing(_workspace(tmp_path))).valid

    def test_no_receipt_at_all_names_the_gates_that_write_one(self, tmp_path):
        report = verify(_workspace(tmp_path))
        assert not report.valid
        assert "Step 0.7" in report.reasons[0] and "Step 12.7" in report.reasons[0]

    def test_a_gate_that_did_not_pass_is_recorded_and_refused(self, tmp_path):
        """A FAILED gate is written down rather than left unwritten: "this gate
        failed" and "this gate never ran" are both refusals, and the difference
        is what the user is told."""
        _workspace(tmp_path)
        receipt.record(tmp_path, "capabilities", "BLOCKED")
        receipt.record(tmp_path, "tie_out", receipt.PASS)
        report = verify(tmp_path)
        assert not report.valid
        assert any("'BLOCKED', not PASS" in r for r in report.reasons)

    def test_the_out_of_scope_verdict_is_accepted_for_one_gate_only(self, tmp_path):
        """`return_builder` may stand on it — a return whose lines 1-38 it does
        not govern leaves it nothing to assert. No other gate may: the tie-out
        and the capability gate apply to every return there is, so the same
        string from either is a gate that did not do its job."""
        _workspace(tmp_path)
        for gate in receipt.REQUIRED_GATES:
            receipt.record(tmp_path, gate, receipt.NOT_APPLICABLE)
        stale = [r for r in verify(tmp_path).reasons if "not PASS" in r]
        assert len(stale) == 2
        assert not any("return_builder" in r for r in stale)

    def test_an_accepted_non_pass_is_still_a_NOTE_a_reader_gets(self, tmp_path):
        """The other half of accepting it. A gate that ran and had no verdict
        to give did not verify what its name says it verifies, and "never
        silently out-of-scope" is not satisfied by a line printed on stderr
        several steps earlier — it rides onto page 1 with the manifest."""
        _passing(_workspace(tmp_path))
        receipt.record(tmp_path, "return_builder", receipt.NOT_APPLICABLE)
        report = verify(tmp_path)
        assert report.valid and report.reasons == []
        assert len(report.notes) == 1
        assert "return_builder" in report.notes[0]
        assert "checked by nothing" in report.notes[0]

    def test_a_workspace_where_every_gate_passed_has_no_notes(self, tmp_path):
        """A note on a clean return would be the banner that means nothing."""
        assert verify(_passing(_workspace(tmp_path))).notes == []

    def test_one_gate_alone_is_not_enough(self, tmp_path):
        _workspace(tmp_path)
        receipt.record(tmp_path, "tie_out", receipt.PASS)
        report = verify(tmp_path)
        assert not report.valid
        assert any("capabilities gate has not run" in r for r in report.reasons)

    @pytest.mark.parametrize("name", [
        "result.json", "parse_ledger.json", "tax_law_2026.json",
        "documents/w2.pdf"])
    def test_an_input_edited_after_the_gate_is_named(self, tmp_path, name):
        _passing(_workspace(tmp_path))
        target = tmp_path / name
        target.write_bytes(target.read_bytes() + b" edited")
        report = verify(tmp_path)
        assert not report.valid
        assert any(Path(name).name in r and "changed" in r for r in report.reasons), report.reasons

    def test_a_document_that_appears_afterwards_is_named(self, tmp_path):
        """The second 1099 arriving on Thursday: the gate passed on a set that
        did not contain it, and re-hashing the RECORDED paths alone could never
        see it."""
        _passing(_workspace(tmp_path))
        (tmp_path / "documents" / "1099int.pdf").write_bytes(b"%PDF-1.4 late")
        report = verify(tmp_path)
        assert not report.valid
        assert any("1099int.pdf appeared" in r for r in report.reasons)

    def test_a_document_that_goes_away_afterwards_is_named(self, tmp_path):
        _passing(_workspace(tmp_path))
        (tmp_path / "documents" / "w2.pdf").unlink()
        report = verify(tmp_path)
        assert not report.valid
        assert any("w2.pdf is gone" in r for r in report.reasons)

    def test_every_reason_is_stated_not_only_the_first(self, tmp_path):
        """An agent handed one drift at a time re-runs the gates once per file."""
        _passing(_workspace(tmp_path))
        (tmp_path / "result.json").write_text("{}")
        (tmp_path / "documents" / "1099b.pdf").write_bytes(b"%PDF-1.4 late")
        assert len(verify(tmp_path).reasons) >= 2

    def test_an_engine_that_moved_invalidates_the_verdict(self, tmp_path):
        """A verdict is about the code that produced it."""
        _passing(_workspace(tmp_path))
        path = tmp_path / receipt.RECEIPT_NAME
        data = json.loads(path.read_text())
        data["gates"]["tie_out"]["engine"] = "0" * 64
        path.write_text(json.dumps(data))
        report = verify(tmp_path)
        assert not report.valid
        assert any("different engine" in r for r in report.reasons)

    def test_a_receipt_from_another_format_version_is_refused(self, tmp_path):
        _passing(_workspace(tmp_path))
        path = tmp_path / receipt.RECEIPT_NAME
        data = json.loads(path.read_text())
        data["receipt_version"] = receipt.RECEIPT_VERSION + 1
        path.write_text(json.dumps(data))
        assert not verify(tmp_path).valid


class TestAReceiptThatCannotBeWrittenNeverLowersAVerdict:
    """`record_after_verdict` is where all three gates end, and this is the one
    rule it holds: a permissions fault must not be able to mask a
    tax-correctness fault. /tax-compute documents exit 1 as "the input is
    missing", so a 2 downgraded to a 1 sends the agent to fix a file that is
    fine while the block it was told to read goes unprinted."""

    def _unwritable(self, tmp_path):
        """A DIRECTORY where the receipt goes. `os.replace` onto one fails on
        POSIX and on Windows alike, without a mode bit that means nothing on
        one of them."""
        _workspace(tmp_path)
        (tmp_path / receipt.RECEIPT_NAME).mkdir()
        return tmp_path

    def test_a_failed_verdict_still_exits_two(self, tmp_path, capsys):
        ws = self._unwritable(tmp_path)
        assert receipt.record_after_verdict(ws, "tie_out", "FAILED", 2) == 2
        assert "ERROR" in capsys.readouterr().err

    def test_a_pass_becomes_a_one_because_the_write_really_did_fail(self, tmp_path):
        """Not a clean pass: the gate agreed, and the record of it is not on
        disk, so the next reader cannot tell this run happened."""
        ws = self._unwritable(tmp_path)
        assert receipt.record_after_verdict(ws, "tie_out", receipt.PASS, 0) == 1

    def test_a_receipt_that_lands_leaves_the_verdict_alone(self, tmp_path):
        _workspace(tmp_path)
        assert receipt.record_after_verdict(tmp_path, "tie_out", "FAILED", 2) == 2
        assert receipt.record_after_verdict(tmp_path, "tie_out", receipt.PASS, 0) == 0


class TestTheInvalidationSetIsTheInputSetAndNothingWider:
    """The other half of the contract. A receipt that fires on a harmless edit
    teaches the agent to re-run gates it has no reason to re-run, which is the
    gates-that-cry-wolf failure PR #69 already paid for."""

    def test_the_cache_is_bound_even_when_only_the_profile_names_the_year(
            self, tmp_path):
        """`result_template.json` ships `meta.tax_year` as null, and
        `return_builder.load` falls back to the profile to pick the cache it
        PRICES LINE 16 FROM. Reading only the meta here left that cache out of
        the map: a re-fetched bracket would not invalidate a gate whose verdict
        depends on it, and AGENTS.md §2 says nothing downstream catches that."""
        _workspace(tmp_path)
        (tmp_path / "result.json").write_text(json.dumps({"meta": {"tax_year": None}}))
        (tmp_path / "taxpayer_profile.json").write_text(
            json.dumps({"filing_status": "MFJ", "tax_year": 2026}))
        names = {v["name"] for v
                 in receipt.gate_inputs(tmp_path, "return_builder").values()}
        assert "tax_law_2026.json" in names
        receipt.record(tmp_path, "return_builder", receipt.PASS)
        (tmp_path / "tax_law_2026.json").write_text(json.dumps({"tax_year": 2026,
                                                               "refetched": True}))
        report = verify(tmp_path, required=("return_builder",))
        assert not report.valid
        assert any("tax_law_2026.json changed" in r for r in report.reasons)

    def test_every_gate_declares_an_input_set(self):
        """The table is the whole of what invalidates a gate, so a gate missing
        from it is a gate whose verdict nothing can stale. It replaced an
        if/elif ladder keyed by name, where a new gate fell into whichever
        branch happened to be the `else` and got a set nobody chose."""
        assert set(receipt._INPUTS) == set(receipt.REQUIRED_GATES)

    def test_only_the_tie_out_reads_the_source_documents(self, tmp_path):
        """Each gate's input set is the files IT reads. The tie-out's question
        is whether every document reached the return, so one arriving later is
        its drift; the derived lines are a function of the four workspace files
        and cannot move on an unparsed document."""
        _workspace(tmp_path, documents=("w2.pdf", "1099int.pdf"))
        shared = {"parse_ledger.json", "result.json", "taxpayer_profile.json",
                  "tax_law_2026.json"}
        def names(gate):
            return {v["name"] for v in receipt.gate_inputs(tmp_path, gate).values()}

        assert names("return_builder") == shared
        assert names("tie_out") == shared | {"documents/w2.pdf",
                                             "documents/1099int.pdf"}

    def test_appending_to_answers_md_leaves_the_receipt_valid(self, tmp_path):
        """/tax-review appends to answers.md AFTER the return is computed; no
        gate reads it, and no figure comes from it that is not already in the
        profile or the result."""
        _passing(_workspace(tmp_path))
        (tmp_path / "answers.md").write_text("## Resolved\n- asked and answered\n")
        assert verify(tmp_path).valid

    def test_a_new_summary_or_review_file_leaves_the_receipt_valid(self, tmp_path):
        _passing(_workspace(tmp_path))
        (tmp_path / "summaries").mkdir(exist_ok=True)
        (tmp_path / "summaries" / "w2.md").write_text("parsed")
        (tmp_path / "reviews").mkdir(exist_ok=True)
        (tmp_path / "reviews" / "2026-review.md").write_text("reviewed")
        assert verify(tmp_path).valid

    def test_a_result_edit_leaves_the_capability_gate_alone(self, tmp_path):
        """Per gate, not per workspace: the capability gate never read
        result.json, so its verdict still stands on what it did read."""
        _passing(_workspace(tmp_path))
        (tmp_path / "result.json").write_text(json.dumps({"meta": {"tax_year": 2026}, "x": 1}))
        reasons = verify(tmp_path).reasons
        assert any(r.startswith("tie_out:") for r in reasons)
        assert not [r for r in reasons if r.startswith("capabilities:")]


class TestTheCli:

    def test_no_arguments_prints_usage(self):
        out = _run()
        assert out.returncode == 0 and "usage:" in out.stdout

    def test_verify_exits_two_and_names_every_reason(self, tmp_path):
        _passing(_workspace(tmp_path))
        (tmp_path / "result.json").write_text("{}")
        out = _run("verify", "--workspace", str(tmp_path))
        assert out.returncode == 2
        assert "STALE RECEIPT" in out.stdout and "result.json changed" in out.stdout

    def test_verify_exits_zero_on_a_workspace_nothing_moved_in(self, tmp_path):
        _passing(_workspace(tmp_path))
        out = _run("verify", "--workspace", str(tmp_path), "--json")
        assert out.returncode == 0
        assert json.loads(out.stdout) == {"valid": True, "reasons": [],
                                          "notes": []}

    def test_a_missing_workspace_is_one_error_line(self, tmp_path):
        out = _run("record", "--workspace", str(tmp_path / "nope"),
                   "--gate", "tie_out", "--outcome", "PASS")
        assert out.returncode == 1
        assert out.stdout == "" and out.stderr.startswith("ERROR:")
        assert "Traceback" not in out.stderr


class TestTheCodexRoundRules:
    """The local `codex review --base main` pass over this branch. Three of its
    four findings are about the WINDOW between a gate reading its inputs and the
    receipt describing them, and the fourth is what is left on disk when the
    write half fails."""

    def test_the_digests_are_the_ones_the_gate_read_not_the_ones_here_now(
            self, tmp_path):
        """A gate reads its inputs, computes a verdict, prints it and writes its
        report. Hashing at the END of that bound a PASS to a document that
        arrived during it — a gate certifying a file it never saw, which is the
        exact failure this module exists to catch."""
        _workspace(tmp_path)
        captured = receipt.gate_inputs(tmp_path, "tie_out")
        (tmp_path / "documents" / "1099int.pdf").write_bytes(b"%PDF-1.4 mid-run")
        receipt.record(tmp_path, "capabilities", receipt.PASS)
        receipt.record(tmp_path, "tie_out", receipt.PASS, inputs=captured)
        report = verify(tmp_path)
        assert not report.valid
        assert any("1099int.pdf appeared" in r for r in report.reasons)

    def test_an_unreadable_input_is_a_receipt_error_not_a_bare_oserror(
            self, tmp_path):
        """Both gates call this inside the handler that protects their verdict,
        and an exception class that handler does not know turns a FAILED hard
        gate into a traceback and exit 1 — which Step 12.7 documents as "the
        ledger is missing", sending the agent to fix a ledger that is fine."""
        _workspace(tmp_path)
        with pytest.raises(ReceiptError, match="could not read"):
            receipt._file_digest(tmp_path / "documents")   # a directory, not a file

    def test_nothing_unsecured_is_ever_published(self, tmp_path, monkeypatch):
        """The permissions half failing must not leave the receipt on disk: it
        names the taxpayer's own documents, and `mkstemp` establishes no
        owner-only ACL on Windows, which AGENTS.md §1 covers too."""
        from agents.lib import secure_fs
        _workspace(tmp_path)
        # Patched where the securing LIVES since V10 — one writer for every
        # runtime artifact, so this invariant is tested at its own boundary.
        monkeypatch.setattr(secure_fs, "secure_path",
                            lambda p: (_ for _ in ()).throw(OSError("denied")))
        with pytest.raises(ReceiptError, match="could not write"):
            receipt.record(tmp_path, "tie_out", receipt.PASS)
        assert not (tmp_path / receipt.RECEIPT_NAME).exists()
        assert not list(tmp_path.glob("*.tmp"))


class TestOnlyTheValidatedResultIsPackaged:

    def test_another_result_file_is_not_covered(self, tmp_path):
        _passing(_workspace(tmp_path))
        other = tmp_path / "result_draft.json"
        other.write_text((tmp_path / "result.json").read_text())
        assert "no gate has seen this file" in receipt.covers_result(tmp_path, other)
        assert receipt.covers_result(tmp_path, tmp_path / "result.json") == ""

    def test_an_edited_result_is_not_covered_under_its_own_name(self, tmp_path):
        _passing(_workspace(tmp_path))
        (tmp_path / "result.json").write_text('{"meta": {"tax_year": 2026}, "x": 1}')
        assert "changed since the tie-out gate ran" in receipt.covers_result(
            tmp_path, tmp_path / "result.json")

    def test_a_document_named_after_an_identifier_is_not_written_down(
            self, tmp_path):
        """A source document CAN be called `W2_<ssn>.pdf` — that is why
        `scan_workspace` refuses one — and this receipt is a file on disk, where
        AGENTS.md §1 forbids a full identifier in any intermediate. The key is a
        digest of the name and the display name is redacted, so the number is in
        neither. (The synthetic SSN is assembled at runtime, from area 000, for
        the same reason the parse tests do it.)"""
        ssn = "-".join(("000", "00", "6789"))
        _workspace(tmp_path, documents=(f"W2_{ssn}.pdf",))
        receipt.record(tmp_path, "tie_out", receipt.PASS)
        written = (tmp_path / receipt.RECEIPT_NAME).read_text()
        assert ssn not in written and ssn.replace("-", "") not in written
        assert "[REDACTED]" in written
        assert verify(tmp_path).reasons == [
            f"the {gate} gate has not run in this workspace"
            for gate in receipt.REQUIRED_GATES if gate != "tie_out"]

    def test_a_second_document_symlinked_to_the_first_is_still_a_new_document(
            self, tmp_path):
        """`_validate_subpath` returns the RESOLVED target, and keying on that
        collapsed two entries into one: the tie-out gate would go on to demand a
        ledger record for a document this receipt could not see arrive."""
        if os.name == "nt":  # pragma: no cover - symlinks need a privilege there
            pytest.skip("symlink creation is privileged on Windows")
        _passing(_workspace(tmp_path))
        (tmp_path / "documents" / "duplicate.pdf").symlink_to(
            tmp_path / "documents" / "w2.pdf")
        report = verify(tmp_path)
        assert not report.valid
        assert any("duplicate.pdf appeared" in r for r in report.reasons)

    def test_a_caller_that_already_read_a_file_records_those_bytes(self, tmp_path):
        """The capability gate parses the profile and records it afterwards.
        Re-reading to hash evaluated one profile and recorded another, so a flag
        flipped in that window produced a PASS receipt for a situation the gate
        would have blocked."""
        _workspace(tmp_path)
        evaluated = (tmp_path / "taxpayer_profile.json").read_bytes()
        mapped = receipt.gate_inputs(tmp_path, "capabilities",
                                     contents={"taxpayer_profile.json": evaluated})
        (tmp_path / "taxpayer_profile.json").write_text(
            json.dumps({"filing_status": "MFJ", "flags": {"has_rental_income": True}}))
        assert mapped == receipt.gate_inputs(
            tmp_path, "capabilities", contents={"taxpayer_profile.json": evaluated})
        assert mapped != receipt.gate_inputs(tmp_path, "capabilities")

    def test_the_bytes_being_packaged_are_the_ones_checked(self, tmp_path):
        """A recompute landing between the package's read and this check
        validated the REPLACEMENT while the package was still being assembled
        from the older result in memory."""
        _passing(_workspace(tmp_path))
        packaged = (tmp_path / "result.json").read_bytes()
        (tmp_path / "result.json").write_text('{"meta": {"tax_year": 2026}, "recomputed": 1}')
        receipt.record(tmp_path, "tie_out", receipt.PASS)   # the new result passes
        assert receipt.covers_result(tmp_path, tmp_path / "result.json") == ""
        assert "changed since the tie-out gate ran" in receipt.covers_result(
            tmp_path, tmp_path / "result.json", contents=packaged)

