#!/usr/bin/env python3
"""Tests for agents/lib/pdf_render.py — the tracked summary renderer (V9).

The PDF used to be produced by a `generate_tax_pdf.py` the agent WROTE into the
workspace per return: gitignored, blocked by name at the PII gate because it
carried the taxpayer's name in its own source, and the one artifact in the
pipeline with no test. What these pin is what that arrangement could never
promise — that a null renders as absent rather than crashing, that page 1
carries the limitations box before any figure, that the metadata names the
toolkit and not an assistant, and that every name on the page came from
`result.json`.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import pdf_render, receipt  # noqa: E402
from tests.skill_text import skill_text  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = json.loads((REPO / "agents" / "templates" / "result_template.json").read_text())


def _text(path, pages=None):
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        chosen = pdf.pages if pages is None else [pdf.pages[i] for i in pages]
        return "\n".join(p.extract_text() or "" for p in chosen)


def _result(**over):
    """A populated return built from the shipped template, so a renamed
    template field shows up here rather than in a taxpayer's PDF."""
    result = json.loads(json.dumps(TEMPLATE))
    result["meta"] = dict(result["meta"], tax_year=2026, filing_status="MFJ",
                          limitations=over.pop("limitations", ["Form 8863 is a manual worksheet"]))
    result["taxpayers"] = {"primary": {"name": over.pop("name", "Pat Q. Filer"),
                                       "ssn_last4": "XXX-XX-6789"},
                           "spouse": {"name": None}, "address": "1 Main St"}
    result["form_1040"] = dict(result["form_1040"], line_1a_wages=125000.0,
                               line_11_agi=118000.0)
    result.update(over)
    return result


def _render(tmp_path, result, **kw):
    out = tmp_path / "summary.pdf"
    return pdf_render.render(result, out, **kw)


class TestNullIsAbsentNotAValue:

    def test_the_shipped_template_renders(self, tmp_path):
        """Every field in `result_template.json` is null. A renderer that treats
        null as a present value crashes here — on `Paragraph()`, on `float()`,
        or on an f-string `:,` — and would crash on any partially populated
        return. The skill has asked for this smoke test since the generator was
        prose; here it is a test."""
        out = _render(tmp_path, json.loads(json.dumps(TEMPLATE)))
        assert out.is_file() and out.stat().st_size > 0

    def test_an_unfilled_optional_section_does_not_print(self, tmp_path):
        """`/tax-setup` removes the sections a taxpayer does not need; one still
        carrying `_include_if` with every figure zero is scaffolding, and
        printing it puts pages of $0.00 between the reader and the figures."""
        text = _text(_render(tmp_path, _result()))
        assert "Form 8621 PFIC" not in text
        assert "Form 1040" in text

    def test_a_zero_that_IS_the_answer_still_prints(self, tmp_path):
        """`balance_due_summary` has no `_include_if`: a $0.00 refund is the
        answer, not the absence of one."""
        text = _text(_render(tmp_path, _result(
            balance_due_summary={"federal_refund": 0, "net_refund": 0, "note": None})))
        assert "Balance Due Summary" in text and "$0.00" in text


class TestPageOne:

    def test_the_limitations_box_precedes_every_figure(self, tmp_path):
        """C2's manifest: the taxpayer must see what the return does NOT cover
        with the same prominence as the results."""
        result = _result(limitations=["Form 8863 is a manual worksheet",
                                      "California only; no other state modeled"])
        rendered = _render(tmp_path, result)
        page1 = _text(rendered, pages=[0])
        assert "Scope & Limitations" in page1
        assert "Form 8863 is a manual worksheet" in page1
        whole = _text(rendered)
        assert whole.index("Scope & Limitations") < whole.index("$125,000.00")

    def test_a_long_manifest_keeps_every_line(self, tmp_path):
        limits = [f"Manual worksheet {i}: see the review notes" for i in range(1, 13)]
        rendered = _render(tmp_path, _result(limitations=limits))
        text = _text(rendered)
        for line in limits:
            assert line in text
        assert "Scope & Limitations" in _text(rendered, pages=[0])

    def test_a_draft_says_so_in_the_same_box(self, tmp_path):
        page1 = _text(_render(tmp_path, _result(),
                              draft_reasons=["the tie_out gate has not run in this workspace"]),
                      pages=[0])
        assert "NOT VALIDATED — DRAFT ONLY" in page1
        assert "the tie_out gate has not run in this workspace" in page1

    def test_a_validated_return_carries_no_draft_banner(self, tmp_path):
        assert "NOT VALIDATED" not in _text(_render(tmp_path, _result()))


class TestWhatIsOnThePageCameFromTheResult:

    @pytest.mark.parametrize("name", ["Pat Q. Filer", "Alex Q. Other"])
    def test_the_filer_is_read_not_hardcoded(self, tmp_path, name):
        """A generator written for one filer printed that filer's name on
        somebody else's return — and put PII in a file the PII scan cannot vet,
        since it matches SSN and credential shapes, not names."""
        text = _text(_render(tmp_path, _result(name=name)))
        assert name in text

    def test_the_module_carries_no_taxpayer_data(self):
        source = (REPO / "agents" / "lib" / "pdf_render.py").read_text()
        for probe in ("Pat Q. Filer", "Alex Q. Other", "1 Main St"):
            assert probe not in source

    def test_line_numbers_survive_into_the_labels(self, tmp_path):
        """The field names encode the official line numbers, and a summary that
        drops them makes every figure unfindable on the real form."""
        text = _text(_render(tmp_path, _result()))
        assert "Line 1a — wages" in text and "Line 11 — agi" in text

    @pytest.mark.parametrize("case,probe", [
        ({"federal_refund": 2400.0, "net_refund": 2400.0}, "$2,400.00"),
        ({"federal_refund": -1800.0, "net_refund": -1800.0}, "($1,800.00)"),
        ({"federal_refund": 0, "net_refund": 0}, "$0.00"),
    ])
    def test_a_refund_a_balance_due_and_a_zero_all_render(self, tmp_path, case, probe):
        """A balance due is a NEGATIVE refund in this schema, and it prints in
        accounting parentheses rather than as a minus sign a reader can miss."""
        text = _text(_render(tmp_path, _result(balance_due_summary=dict(case, note=None))))
        assert probe in text


class TestTheDocumentItself:

    def test_no_assistant_or_vendor_is_named(self, tmp_path):
        """Every generated return inherits this metadata."""
        import pdfplumber
        out = _render(tmp_path, _result())
        with pdfplumber.open(str(out)) as pdf:
            meta = {k.lower(): str(v).lower() for k, v in pdf.metadata.items()}
        stamped = " ".join(meta.values()) + _text(out).lower()
        for vendor in ("claude", "anthropic", "assistant", "openai", "gpt", "copilot"):
            assert vendor not in stamped, vendor
        assert pdf_render.PRODUCER in " ".join(
            v for k, v in meta.items() if k in ("author", "creator"))

    def test_the_pdf_is_owner_only(self, tmp_path):
        if os.name == "nt":  # pragma: no cover - POSIX bits are meaningless here
            pytest.skip("POSIX mode bits")
        old = os.umask(0o022)   # without this the assertion is vacuous
        try:
            out = _render(tmp_path, _result())
        finally:
            os.umask(old)
        assert (out.stat().st_mode & 0o777) == 0o600

    def test_it_says_it_is_not_an_efile_artifact(self, tmp_path):
        assert "NOT an e-file artifact" in _text(_render(tmp_path, _result()), pages=[0])


class TestTheCli:

    def _workspace(self, tmp_path, *, validated):
        (tmp_path / "taxpayer_profile.json").write_text("{}")
        (tmp_path / "parse_ledger.json").write_text("{}")
        (tmp_path / "result.json").write_text(json.dumps(_result()))
        if validated:
            receipt.record(tmp_path, "capabilities", receipt.PASS)
            receipt.record(tmp_path, "tie_out", receipt.PASS)
        return tmp_path

    def _run(self, tmp_path, *flags):
        return subprocess.run(
            [sys.executable, "-m", "agents.lib.pdf_render",
             "--result", str(tmp_path / "result.json"),
             "--workspace", str(tmp_path), *flags],
            capture_output=True, text=True, cwd=REPO)

    def test_a_validated_return_renders(self, tmp_path):
        out = self._run(self._workspace(tmp_path, validated=True))
        assert out.returncode == 0, out.stderr
        assert (tmp_path / "Tax_Return_2026_Summary.pdf").is_file()

    def test_an_unvalidated_return_is_refused_and_writes_nothing(self, tmp_path):
        """Generation and validation consume the same input generation, or the
        PDF is marked as what it is."""
        out = self._run(self._workspace(tmp_path, validated=False))
        assert out.returncode == 2
        assert "REFUSED" in out.stderr and "--draft" in out.stderr
        assert not list(tmp_path.glob("*.pdf"))

    def test_draft_renders_the_refusal_onto_page_one(self, tmp_path):
        out = self._run(self._workspace(tmp_path, validated=False), "--draft")
        assert out.returncode == 0, out.stderr
        page1 = _text(tmp_path / "Tax_Return_2026_Summary.pdf", pages=[0])
        assert "NOT VALIDATED" in page1 and "has not run" in page1

    def test_an_edited_result_is_refused_after_the_gates_ran(self, tmp_path):
        ws = self._workspace(tmp_path, validated=True)
        (ws / "result.json").write_text(json.dumps(_result(name="Alex Q. Other")))
        out = self._run(ws)
        assert out.returncode == 2
        assert "result.json changed since the tie-out gate ran" in out.stderr

    def test_a_path_outside_the_workspace_is_refused(self, tmp_path):
        ws = self._workspace(tmp_path, validated=True)
        out = self._run(ws, "--out", str(tmp_path.parent / "escape.pdf"))
        assert out.returncode == 1
        assert "outside workspace" in out.stderr and "Traceback" not in out.stderr


class TestTheCodexRoundRules:
    """The local `codex review --base main` pass. All three findings are things
    a POPULATED return loses that the template's own emptiness hid."""

    LONG = ("Form 8863 (education credits) is computed on a MANUAL worksheet "
            "outside this toolkit; the figure here was entered by hand and is "
            "not re-derived from the 1098-T.")

    def _within_the_frame(self, path):
        """Every drawn word inside the page, with the frame's own margin.

        reportlab draws a plain string in a table cell on ONE line and lets it
        run past the frame — the sentence is still in the PDF and still
        extractable, so a text assertion cannot see this at all. Positions can.
        """
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                for word in page.extract_words():
                    if word["x1"] > page.width - 36:   # the 0.5in margin
                        return False, word["text"]
        return True, ""

    def test_a_real_limitations_sentence_stays_on_the_page(self, tmp_path):
        out = _render(tmp_path, _result(limitations=[self.LONG]))
        ok, spilled = self._within_the_frame(out)
        assert ok, f"{spilled!r} is drawn past the right margin"
        assert self.LONG.split(";")[0] in " ".join(_text(out).split())

    def test_a_long_section_value_stays_on_the_page(self, tmp_path):
        """Florida's no-income-tax statement is a paragraph, not a figure."""
        statement = ("Florida imposes no individual income tax, so no state "
                     "return is required for this taxpayer; the sales-tax "
                     "deduction is the only Florida figure on the federal return.")
        out = _render(tmp_path, _result(florida_state={
            "no_income_tax_statement": statement, "state_return_required": False}))
        ok, spilled = self._within_the_frame(out)
        assert ok, f"{spilled!r} is drawn past the right margin"
        # ...and it is all there: an unwrapped cell that does not overflow the
        # page has been CLIPPED instead, which loses the half nobody can see.
        assert statement in " ".join(_text(out).split())

    def test_a_section_that_is_a_list_still_renders(self, tmp_path):
        """`dependents` IS a list. A walker that only descended into dicts
        dropped the whole section without a word."""
        text = _text(_render(tmp_path, _result(dependents=[
            {"name": "Robin Filer", "relationship": "daughter", "age": 7},
            {"name": "Sam Filer", "relationship": "son", "age": 11}])))
        assert "Dependents" in text
        assert "Robin Filer" in text and "Sam Filer" in text

    def test_a_computation_warning_is_not_dropped_as_commentary(self, tmp_path):
        """`compute_form_2210` returns a ZERO penalty with a note saying a
        penalty applies but the rate is unfetched. Dropping the note left
        `$0.00` standing alone — a loud degrade turned quiet, which is the
        direction AGENTS.md §4 says must never be silent."""
        text = " ".join(_text(_render(tmp_path, _result(form_2210={
            "penalty": 0, "penalty_computed": False,
            "notes": ["Underpayment rates are unfetched: a penalty applies but "
                      "its amount could not be computed."]}))).split())
        assert "$0.00" in text
        assert "a penalty applies but its amount could not be computed" in text

    def test_a_field_name_that_is_not_the_official_line_says_so(self, tmp_path):
        """`line_1c_1099b_box_c` is Schedule D line 3 and
        `line_14c_qualified_medical` is Form 8889 line 15. A filer transcribing
        by our labels would put the figure on the wrong line of a real form,
        which is worse than an unlabelled row — /tax-generate's own caveat list
        says so, and this is that list in code."""
        text = " ".join(_text(_render(tmp_path, _result(
            schedule_d={"line_1c_1099b_box_c": 4200.0},
            form_8889_hsa={"line_14c_qualified_medical": 900.0}))).split())
        assert "Schedule D line 3" in text and "Form 8889 line 15" in text
        assert "Line 1c" not in text and "Line 14c" not in text

    def test_a_local_currency_amount_is_not_printed_as_dollars(self, tmp_path):
        """`form_1116_ftc` carries the payer's own currency beside the converted
        USD figure; one dollar sign over both is two different amounts wearing
        one unit."""
        text = " ".join(_text(_render(tmp_path, _result(form_1116_ftc={
            "foreign_tax_paid_local_currency": 1500.0,
            "foreign_tax_paid_usd": 1620.0}))).split())
        assert "1,500.00" in text and "$1,620.00" in text
        assert "$1,500.00" not in text

    def test_a_name_with_angle_brackets_survives(self, tmp_path):
        """The heading goes through reportlab's markup parser too: an unknown
        tag swallowed the text, and a tag it knows raised a ValueError carrying
        the whole name into a traceback — identity data in terminal output."""
        # An unmatched tag reportlab RECOGNISES is the sharp case: `<Jr>` is
        # merely swallowed, `<b` raises.
        name = "Pat <b O'Brien & Co"
        text = _text(_render(tmp_path, _result(name=name)))
        assert name in " ".join(text.split())

    def test_a_narrative_longer_than_a_page_does_not_take_the_document_down(
            self, tmp_path):
        """A table splits BETWEEN rows by default, so one worksheet note pasted
        into `notes` made a row taller than the frame and raised LayoutError for
        the whole summary."""
        essay = " ".join(f"sentence {i} about an unusual position" for i in range(400))
        out = _render(tmp_path, _result(interest_detail={"notes": [essay], "total": 12.0}))
        assert out.is_file()
        assert "sentence 399 about an unusual position" in " ".join(_text(out).split())

    def test_a_local_currency_amount_keeps_its_cents(self, tmp_path):
        """Dropping the dollar SYMBOL must not drop the amount: `:g` rounded
        12345.67 to 12345.7, and larger amounts lost whole units."""
        text = " ".join(_text(_render(tmp_path, _result(form_1116_ftc={
            "foreign_tax_paid_local_currency": 12345.67}))).split())
        assert "12,345.67" in text

    def test_an_override_belongs_to_its_own_form(self, tmp_path):
        """`line_15_taxable_distributions` exists in `form_8606` too, where an
        IRA distribution labelled "Form 8889 line 16" sends the reader to an
        unrelated HSA form."""
        text = " ".join(_text(_render(tmp_path, _result(
            form_8606={"line_15_taxable_distributions": 2500.0}))).split())
        assert "Form 8889" not in text
        assert "Line 15" in text

    def test_a_failed_render_leaves_nothing_behind(self, tmp_path, monkeypatch):
        """reportlab creates the file and THEN writes it, so a failure part way
        through left a partial PDF of return data at whatever the umask allowed
        — and the securing step below it never ran."""
        import agents.lib.pdf_render as module
        from agents.lib import secure_fs

        # The securing step is what used to run LAST, on a file reportlab had
        # already published: when it failed, the return data stayed on disk at
        # whatever the umask allowed. Since V10 it runs inside
        # `secure_fs.atomic_path`, on the temp file and before the rename, so a
        # failure there leaves nothing at all — patched at that boundary.
        monkeypatch.setattr(secure_fs, "secure_path",
                            lambda p: (_ for _ in ()).throw(OSError("denied")))
        with pytest.raises(module.PdfRenderError, match="could not write"):
            _render(tmp_path, _result())
        assert not list(tmp_path.iterdir())

    def test_a_carryforward_is_an_amount_however_its_key_ends(self, tmp_path):
        """`carryforward_to_next_year` ends in "year" and is dollars. The suffix
        rule printed it bare and rounded — 12345.67 as 12345.7."""
        text = " ".join(_text(_render(tmp_path, _result(form_6251={
            "carryforward_to_next_year": 12345.67}))).split())
        assert "$12,345.67" in text

    def test_box_a_and_box_d_name_both_destinations(self, tmp_path):
        """A Box A transaction goes to Schedule D line 1a only when it carries
        no adjustment; an adjusted one routes through Form 8949 to line 1b. A
        label that states one destination is wrong half the time."""
        text = " ".join(_text(_render(tmp_path, _result(schedule_d={
            "line_1a_1099b_box_a": 1000.0, "line_8a_1099b_box_d": 2000.0}))).split())
        # The value column interleaves with a label that wraps, so the assertion
        # is on the part of each label that stays on one line.
        assert "1099-B Box A (Schedule D line 1a, or 1b via Form 8949" in text
        assert "1099-B Box D (Schedule D line 8a, or 8b via Form 8949" in text

    @pytest.mark.parametrize("name", ["Анна Петрова", "李 明"])
    def test_an_alphabet_the_font_lacks_is_declared_on_page_one(self, tmp_path, name):
        """The embedded face covers Latin and Greek; a Cyrillic or CJK name
        still prints as empty boxes, and shipping a font that covers them is a
        licence-and-size decision recorded as Q6 rather than taken here. What is
        NOT acceptable is losing a name in silence — §4 says a degrade is loud,
        and a name replaced by boxes is the quietest possible way to lose one.

        The characters themselves are not echoed into the message: they are the
        ones the font cannot draw, so printing them would print more boxes."""
        page1 = " ".join(_text(_render(tmp_path, _result(name=name)),
                               pages=[0]).split())
        assert "outside the built-in font" in page1
        assert "read those fields from result.json" in page1
        assert name.split()[0] not in page1


class TestTheFourthRoundRules:
    """The pass Codex owed this branch after its usage limit reset, taken
    against the merged commit."""

    def test_a_non_finite_figure_is_refused(self, tmp_path):
        """`json.loads` admits the NaN and Infinity literals, both pass
        `isinstance(x, (int, float))`, and this module would have formatted
        them — `($nan)`, `$inf` — into a document that looks like every other
        one, receipt and all, because the receipt checks freshness and not
        arithmetic."""
        for bad in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(pdf_render.PdfRenderError, match="finite"):
                _render(tmp_path, _result(form_1040=dict(
                    TEMPLATE["form_1040"], line_1a_wages=bad)))
        assert not list(tmp_path.iterdir())

    def test_the_exchange_rates_survive_the_skipped_meta_block(self, tmp_path):
        """They live in `meta`, which is rendered as the header and the
        limitations box rather than as a table — and skipping the block whole
        took them with it. Nothing else in the PDF states them, so a foreign
        amount could not be reconciled with its USD figure at all."""
        result = _result()
        result["meta"] = dict(result["meta"], exchange_rates={"EUR": 1.08, "RUB": 0.011})
        text = " ".join(_text(_render(tmp_path, result)).split())
        assert "Exchange rates" in text
        assert "EUR 1.08" in text and "RUB 0.011" in text

    def test_the_skill_does_not_require_the_retired_generator(self):
        """A fresh workspace cannot satisfy a prerequisite naming a file this
        skill no longer creates, and an agent following the list stops before
        it ever reaches the working renderer."""
        flat = " ".join(skill_text("tax-generate").split())
        head = flat[flat.index("## Prerequisites"):flat.index("## Steps")]
        assert "generate_tax_pdf.py must exist" not in head
        assert "pdf_render.py" in head

