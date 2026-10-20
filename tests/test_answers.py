"""The required-answer contract (V5) — `agents/lib/answers.py`.

WHAT THIS MODULE EXISTS TO PIN, in the order the failures actually happened.

1. THE PARSER MUST NOT INVENT AN ANSWER. The first version's marker regex used
   `\\s*`, which matches a newline: a blank marker swallowed the blank line under
   it and captured the next heading as its answer. The shipped template parsed as
   fully answered and the gate exited 0 on a workspace where nothing had been
   asked. The tests missed it because every blank-body case was a one-line string
   with nothing after it. So the parser cases here always put something AFTER the
   marker, and `TestTheShippedTemplateIsTheHardestCase` runs the real file.

2. EVERY REGISTRY ENTRY MUST BE REACHABLE. `_TRIGGERS` names a workspace that
   makes each entry owed, and a test asserts it covers every entry that has a
   `when`. An entry nothing can trigger is a question that is silently never
   asked, which is worse than not registering it.

3. THE REGISTRY AND THE SKILLS MUST AGREE. Each entry's id has to appear in the
   skill `asked_by` names, so an answer given at the mid-run stop is recorded in
   the form the next run reads.

4. AN ACKNOWLEDGEMENT MUST BE RE-ASKED AND MUST STILL TERMINATE. Never satisfied
   by the file; cleared only by `--acknowledge`, for one invocation.
"""

import io
import itertools
import os
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents.lib import answers as A  # noqa: E402
from skill_text import SKILLS_DIR, skill_text  # noqa: E402

TEMPLATE = REPO / "agents" / "templates" / "answers_template.md"
_COUNTER = itertools.count()

# The always-on FACTs, so an acknowledgement test measures the
# acknowledgement rather than the questions every workspace owes.
_ALL_FACTS_ANSWERED = ("- **[document_set_complete]** yes\n"
                       "- **[corrected_documents]** none\n"
                       "- **[charitable_gifts]** none\n"
                       "- **[state_local_taxes_paid_outside_withholding]** none\n")


def _workspace(tmp_path, *, profile=None, result=None, tax_law=None, answers=None):
    """A fresh workspace directory holding exactly the files named."""
    ws = tmp_path / f"ws{next(_COUNTER)}"
    ws.mkdir(parents=True)
    if tax_law is not None:
        # No question reads the cache any more (AA59); a caller passing one
        # proves that a year's regime leaves the answers owed unchanged.
        profile = dict(profile or {})
        profile.setdefault("tax_year", 2026)
    if profile is not None:
        (ws / "taxpayer_profile.json").write_text(json.dumps(profile))
    if result is not None:
        (ws / "result.json").write_text(json.dumps(result))
    if tax_law is not None:
        (ws / "tax_law_2026.json").write_text(json.dumps(tax_law))
    if answers is not None:
        (ws / "answers.md").write_text(answers)
    return ws


# One workspace per entry that has a `when`, built so that entry IS owed. The
# test below asserts this covers the registry: a new entry with no trigger here
# fails rather than becoming a question nothing ever asks.
_TRIGGERS = {
    "ca_investment_sources": dict(profile={"address": {"state": "CA"}}),
    "ca_state_refund_worksheet": dict(profile={"address": {"state": "CA"},
        "flags": {"has_1099g_state_refund": True}}),
    "ca_state_payments": dict(profile={"address": {"state": "CA"}}),
    "ca_overpayment_election": dict(profile={"address": {"state": "CA"}}),
    "ca_amt_credit_worksheet": dict(profile={"address": {"state": "CA"},
        "flags": {"ca_has_amt_credit": True}}),
    "document_set_complete": dict(),
    "corrected_documents": dict(),
    "charitable_gifts": dict(),
    "charitable_carryovers": dict(profile={"prior_year": {
        "charitable_carryforward_ledger": {"federal": {"verified": False}}}}),
    "capital_loss_carryforward_split": dict(
        profile={"prior_year": {"capital_loss_carryforward": 9000,
                                "capital_loss_carryforward_short_term": 0,
                                "capital_loss_carryforward_long_term": 0}}),
    "state_local_taxes_paid_outside_withholding": dict(),
    "mfs_spouse_itemizes": dict(profile={"filing_status": "MFS"}),
    "foreign_account_completeness": dict(
        profile={"flags": {"has_foreign_accounts": True}}),
    "crypto_disposals_without_basis": dict(
        result={"crypto_summary": {"needs_basis": [{"asset": "BTC"}]}}),
}


def _run(ws, *args):
    """`(exit code, stdout)` for one CLI invocation, in-process."""
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = A._main(["--workspace", str(ws), "--check-required", *args])
    return code, out.getvalue() + err.getvalue()


class TestTheRegistryIsTheSurveyAndNotAGuess:
    def test_ids_are_unique_and_stable_shaped(self):
        ids = [a.id for a in A.REQUIRED_ANSWERS]
        assert len(ids) == len(set(ids))
        assert all(i.replace("_", "").isalnum() and i.islower() for i in ids)

    def test_every_entry_is_one_of_the_two_kinds(self):
        assert {a.kind for a in A.REQUIRED_ANSWERS} <= {A.FACT, A.ACKNOWLEDGEMENT}

    @pytest.mark.parametrize("answer", A.REQUIRED_ANSWERS, ids=lambda a: a.id)
    def test_the_skill_named_by_asked_by_records_the_id(self, answer):
        """The registry and the skill must agree on the RECORD FORMAT.

        The skills' mid-run stops used to say only "record the answer in
        answers.md", and the parser reads `- **[id]**`. An answer given at the
        stop was therefore invisible to the next run's check, which re-reported
        it as owed for ever. Pinning the id INTO the skill text is what makes
        the two halves one contract.
        """
        skill = answer.asked_by.split()[0]
        assert (SKILLS_DIR / skill).is_dir(), f"{skill} is not a skill directory"
        assert answer.id in skill_text(skill), (
            f"{skill} never names {answer.id}, so an answer recorded at its stop "
            "is not in the form --check-required reads")

    def test_every_entry_with_a_condition_has_a_workspace_that_triggers_it(self):
        """No entry may be unreachable.

        The condition used to live in an `if/elif` ladder keyed by id, away from
        the entry. An entry added without its branch was silently never owed —
        the failure the registry exists to prevent, with the registry itself as
        the blind spot.
        """
        conditioned = {a.id for a in A.REQUIRED_ANSWERS if a.when is not None}
        assert conditioned == set(_TRIGGERS)

    @pytest.mark.parametrize("answer_id", sorted(_TRIGGERS))
    def test_the_trigger_workspace_actually_owes_that_answer(self, answer_id, tmp_path):
        ws = _workspace(tmp_path, **_TRIGGERS[answer_id])
        assert answer_id in {a.id for a in A.applicable(ws)}

    def test_an_entry_raised_by_the_run_is_named_as_such_and_never_owed(self, tmp_path):
        ws = _workspace(tmp_path)
        assert A.RAISED_BY_THE_RUN, "the in-flight category is documented; keep one"
        raised = {a.id for a in A.RAISED_BY_THE_RUN}
        assert raised & {a.id for a in A.applicable(ws)} == set()
        assert all(a.when is None for a in A.RAISED_BY_THE_RUN)


class TestTheTriggersMatchWhatTheSkillActuallySays:
    def test_the_capital_loss_split_is_not_asked_when_the_split_is_known(self, tmp_path):
        ws = _workspace(tmp_path, profile={"prior_year": {
            "capital_loss_carryforward": 9000,
            "capital_loss_carryforward_short_term": 4000,
            "capital_loss_carryforward_long_term": 5000}})
        assert "capital_loss_carryforward_split" not in {a.id for a in A.applicable(ws)}

    @pytest.mark.parametrize("state", [{"type": "none", "code": "FL"},
                                       {"code": "NJ"},  # a generic state's cache has no type
                                       {"type": "graduated_income", "code": "CA"}])
    def test_every_filer_is_asked_for_state_payments(self, tmp_path, state):
        """The question was once asked only where the resident state taxes
        income: a generic state's cache carries no type and read as no tax, and a filer
        who moved to Florida last year still pays the old state's balance this
        year, which no profile field records. Asking costs a no-tax filer one
        'none'; not asking drops a deduction, the hard-error direction."""
        ws = _workspace(tmp_path, tax_law={"state": state})
        assert "state_local_taxes_paid_outside_withholding" in {a.id for a in A.applicable(ws)}

    def test_the_line_5a_question_carries_the_recipe_s_limits(self):
        """The recipe's limits are pinned; the question the taxpayer answers
        has to carry them too, or the answer brings in what the recipe drops."""
        (question,) = [a.question for a in A.REQUIRED_ANSWERS
                       if a.id == "state_local_taxes_paid_outside_withholding"]
        for phrase in ("State and local estimated payments made in good faith",
                       "prior-year overpayment credited",
                       "without its penalties or interest", "Filing separately, only your own share"):
            assert phrase in question, phrase

    def test_a_state_only_answer_does_not_answer_local_payments(self, tmp_path):
        """A pre-AA59 fact answered only state estimates and balances. It
        cannot answer NYC estimates or other local payments never asked for."""
        old_answers = _ALL_FACTS_ANSWERED.replace(
            "- **[state_local_taxes_paid_outside_withholding]** none\n", "")
        old_answers += ("- **[state_taxes_paid_outside_withholding]** "
                        "State estimates: none; prior-year state balance: none.\n")
        ws = _workspace(tmp_path, answers=old_answers)
        owed, _ = A.missing(ws)
        assert {answer.id for answer in owed} == {"state_local_taxes_paid_outside_withholding"}
        assert _run(ws)[0] == 2
        (ws / "answers.md").write_text(
            old_answers + "- **[state_local_taxes_paid_outside_withholding]** "
            "No state or local estimates, credited overpayments or prior-year balances.\n")
        assert _run(ws)[0] == 0

    def test_a_no_income_tax_state_IS_still_asked_for_charitable_gifts(self, tmp_path):
        """§170(p) is a NON-itemizer deduction.

        Keying charitable gifts on state withholding — the first version — left
        every standard-deduction filer in FL/TX/WA/NV unasked, and the line-13c
        deduction silently zero, which OVERSTATES the tax.
        """
        ws = _workspace(tmp_path, tax_law={"state": {"type": "none", "code": "FL"}})
        assert "charitable_gifts" in {a.id for a in A.applicable(ws)}

    def test_a_joint_filer_is_not_asked_whether_the_spouse_itemizes(self, tmp_path):
        ws = _workspace(tmp_path, profile={"filing_status": "MFJ"})
        assert "mfs_spouse_itemizes" not in {a.id for a in A.applicable(ws)}

    def test_signature_authority_alone_asks_the_fbar_question(self, tmp_path):
        ws = _workspace(tmp_path, profile={
            "flags": {"signature_authority_foreign_accounts": True}})
        assert "foreign_account_completeness" in {a.id for a in A.applicable(ws)}

    def test_the_crypto_acknowledgement_reads_where_needs_basis_IS_written(self, tmp_path):
        """`needs_basis` is merged into `result.json` `crypto_summary`.

        The first version globbed `summaries/crypto_*.json`, whose schema is
        `{"transactions": [...]}` — a shape that never carries the key. The only
        ACKNOWLEDGEMENT in the registry was therefore dead in every real run,
        and the test that "covered" it fabricated the wrong file.
        """
        empty = _workspace(tmp_path, result={"crypto_summary": {"needs_basis": []}})
        assert "crypto_disposals_without_basis" not in {a.id for a in A.applicable(empty)}
        stale_shape = _workspace(tmp_path)
        (stale_shape / "summaries").mkdir()
        (stale_shape / "summaries" / "crypto_coinbase.json").write_text(
            json.dumps({"needs_basis": [{"asset": "BTC"}]}))
        assert "crypto_disposals_without_basis" not in {
            a.id for a in A.applicable(stale_shape)}, (
            "summaries/ is not where needs_basis lives; reading it there is the "
            "dead-code failure this test replaced")

    def test_a_zeroed_result_template_does_not_drive_any_trigger(self, tmp_path):
        """The check runs BEFORE compute, where `result.json` is the template.

        Three triggers used to read figures the run itself produces
        (`line_24_total_tax`, `box_17_state_tax`), so they reported nothing on
        the first pass — the pass the contract exists for.
        """
        template = json.loads(
            (REPO / "agents" / "templates" / "result_template.json").read_text())
        ws = _workspace(tmp_path, result=template)
        owed = {a.id for a in A.applicable(ws)}
        assert {"document_set_complete", "corrected_documents", "charitable_gifts",
                "state_local_taxes_paid_outside_withholding"} <= owed


class TestReadingAnswersFromTheFile:
    def test_an_answer_on_the_marker_line_is_read(self):
        assert A.parse_answers("- **[charitable_gifts]** $2,400 cash, receipts held") == {
            "charitable_gifts": "$2,400 cash, receipts held"}

    @pytest.mark.parametrize("blank", ["", " ", "   ", "\t", " __ "])
    def test_a_marker_with_nothing_after_it_is_unanswered(self, blank):
        assert A.parse_answers(f"- **[charitable_gifts]**{blank}\n") == {}

    def test_a_blank_marker_does_NOT_absorb_the_next_line(self):
        """THE P0. `\\s*` matched the newline, so the heading below became the
        answer and the shipped template read as fully answered."""
        text = ("- **[document_set_complete]** \n"
                "\n"
                "### Is any document a CORRECTED form?\n"
                "\n"
                "- **[corrected_documents]** \n")
        assert A.parse_answers(text) == {}

    def test_a_dash_is_an_answer_because_that_is_how_none_is_written(self):
        """"—" is a taxpayer saying "no charitable gifts". Reading it as
        unanswered blocks a return that answered every question."""
        for dash in ("-", "–", "—", "None"):
            assert A.parse_answers(f"- **[charitable_gifts]** {dash}\n") == {
                "charitable_gifts": dash}

    def test_an_answer_may_run_onto_indented_lines_under_the_marker(self):
        text = ("- **[charitable_gifts]**\n"
                "  - Cash: $2,400 to the food bank, receipt held\n"
                "  - Non-cash: none\n")
        assert A.parse_answers(text) == {
            "charitable_gifts": "- Cash: $2,400 to the food bank, receipt held "
                                "- Non-cash: none"}

    def test_a_continuation_stops_at_a_blank_line(self):
        text = ("- **[charitable_gifts]**\n"
                "\n"
                "  this paragraph belongs to something else\n")
        assert A.parse_answers(text) == {}

    def test_an_id_inside_a_code_fence_is_an_example_not_an_answer(self):
        """Both skills tell an agent to record `- **[id]** answer`, and the
        natural way to show that is a fenced block. Reading it would let the
        instruction satisfy the gate it describes."""
        text = ("Record it like this:\n"
                "\n"
                "```\n"
                "- **[document_set_complete]** yes, all present\n"
                "```\n")
        assert A.parse_answers(text) == {}

    def test_a_fenced_example_does_not_hide_a_real_answer_after_it(self):
        text = ("```\n- **[document_set_complete]** example\n```\n"
                "- **[document_set_complete]** yes, all present\n")
        assert A.parse_answers(text) == {"document_set_complete": "yes, all present"}

    def test_crlf_line_endings_read_the_same(self):
        text = "- **[charitable_gifts]** $500 cash\r\n- **[corrected_documents]**\r\n"
        assert A.parse_answers(text) == {"charitable_gifts": "$500 cash"}

    def test_an_inner_fence_does_not_close_an_OUTER_longer_one(self):
        """Toggling on any fence-looking line let the inner ``` end the outer
        ````, handing back the marker between them as an answer — the example
        satisfying the gate it is an example of."""
        text = ("````\n"
                "```\n"
                "- **[document_set_complete]** example\n"
                "````\n")
        assert A.parse_answers(text) == {}

    def test_a_tilde_line_does_not_close_a_backtick_fence(self):
        text = ("```\n~~~\n- **[document_set_complete]** example\n```\n")
        assert A.parse_answers(text) == {}

    def test_a_four_space_indented_line_is_code_not_a_fence(self):
        """Four spaces is an indented code block, not a fence opener. Reading it
        as one swallowed the rest of the file, so a real answer below it was
        lost and the gate blocked a workspace that had answered."""
        assert A.parse_answers("    ```\n\n- **[document_set_complete]** yes\n") == {
            "document_set_complete": "yes"}

    def test_a_tab_indented_continuation_is_read(self):
        assert A.parse_answers("- **[document_set_complete]**\n\tyes, all present\n") == {
            "document_set_complete": "yes, all present"}

    def test_a_star_bullet_and_leading_indent_are_both_accepted(self):
        assert A.parse_answers("  * **[corrected_documents]** none\n") == {
            "corrected_documents": "none"}


class TestAnInputThatCannotBeReadIsNeverACleanBill:
    """Absence is the ordinary first-run state; a FAILED read is a fault.

    One revision read both as `{}`, so a truncated `taxpayer_profile.json`
    reported a workspace owing nothing — exit 0 — with the MFS and
    foreign-account questions silently dropped. Absence is where the tolerance
    stops.
    """

    def test_an_absent_input_is_not_a_fault(self, tmp_path):
        ws = _workspace(tmp_path)
        assert not (ws / "taxpayer_profile.json").exists()
        assert _run(ws)[0] == 2, "a bare workspace owes the always-on questions"

    @pytest.mark.parametrize("name", ["taxpayer_profile.json", "result.json"])
    def test_a_truncated_input_is_could_not_run_not_clean(self, tmp_path, name):
        ws = _workspace(tmp_path, answers=_ALL_FACTS_ANSWERED)
        (ws / name).write_text('{"filing_status": "MFS",')
        code, out = _run(ws)
        assert code == 1, "a JSON fault must never read as a clean bill"
        assert "readable JSON" in out

    def test_a_json_document_that_is_not_an_object_is_a_fault(self, tmp_path):
        ws = _workspace(tmp_path, answers=_ALL_FACTS_ANSWERED)
        (ws / "result.json").write_text("[1, 2, 3]")
        assert _run(ws)[0] == 1

    def test_an_unreadable_input_names_no_path(self, tmp_path):
        ws = _workspace(tmp_path / "return-000-00-0000", answers=_ALL_FACTS_ANSWERED,
                        profile={"filing_status": "MFJ"})
        (ws / "taxpayer_profile.json").chmod(0o000)
        try:
            if os.access(ws / "taxpayer_profile.json", os.R_OK):
                pytest.skip("this user can read a 000 file (root); nothing to test")
            code, out = _run(ws)
        finally:
            (ws / "taxpayer_profile.json").chmod(0o600)
        assert code == 1
        assert "000-00-0000" not in out


class TestABadFlagIsCouldNotRunAndNotBlocked:
    def test_an_unknown_skill_scope_exits_one(self, tmp_path):
        assert _run(_workspace(tmp_path), "--for", "tax-comptue")[0] == 1

    def test_a_flag_wanting_a_value_and_given_none_exits_one(self, tmp_path):
        assert _run(_workspace(tmp_path), "--acknowledge")[0] == 1


class TestTheShippedTemplateIsTheHardestCase:
    """The template is what `tax-setup` copies into a new workspace, so it is
    the exact input the gate sees first. Both P0s were visible here and no test
    ran it."""

    def test_the_template_records_no_answers(self):
        assert A.parse_answers(TEMPLATE.read_text()) == {}

    def test_the_template_does_carry_a_marker_for_every_checkable_id(self):
        recorded, markers = A._scan(TEMPLATE.read_text())
        assert recorded == {}
        assert markers == len([a for a in A.REQUIRED_ANSWERS if a.when is not None])

    def test_a_workspace_seeded_from_the_template_owes_everything(self, tmp_path):
        ws = _workspace(tmp_path, answers=TEMPLATE.read_text(),
                        result=json.loads((REPO / "agents" / "templates"
                                           / "result_template.json").read_text()))
        code, out = _run(ws)
        assert code == 2, out
        assert "document_set_complete" in out

    def test_the_template_section_is_generated_from_the_registry(self):
        """One copy. The section was hand-written, which put every question in
        two places and left 21 sentences free to drift."""
        assert A.template_section() in TEMPLATE.read_text()


class TestAnAcknowledgementIsNeverSatisfiedByTheFile:
    def test_recording_it_does_not_clear_it(self, tmp_path):
        ws = _workspace(tmp_path, answers="- **[crypto_disposals_without_basis]** accepted\n",
                        **_TRIGGERS["crypto_disposals_without_basis"])
        owed, answers = A.missing(ws)
        assert "crypto_disposals_without_basis" in {a.id for a in owed}
        assert "crypto_disposals_without_basis" in answers.recorded

    def test_the_report_says_why_it_is_asked_again(self, tmp_path):
        ws = _workspace(tmp_path, answers="- **[crypto_disposals_without_basis]** accepted\n",
                        **_TRIGGERS["crypto_disposals_without_basis"])
        assert "re-asked every run" in A.render(ws)

    def test_acknowledging_it_clears_it_for_THIS_invocation(self, tmp_path):
        ws = _workspace(tmp_path, answers=_ALL_FACTS_ANSWERED,
                        **_TRIGGERS["crypto_disposals_without_basis"])
        owed, _ = A.missing(ws, acknowledged={"crypto_disposals_without_basis"})
        assert "crypto_disposals_without_basis" not in {a.id for a in owed}

    def test_the_acknowledgement_is_written_nowhere(self, tmp_path):
        """A gate that can never be cleared teaches an agent to reason past a 2,
        and one that persists the acceptance is the thing the kind exists to
        forbid. So: cleared for one run, and the next run asks again."""
        ws = _workspace(tmp_path, answers=_ALL_FACTS_ANSWERED,
                        **_TRIGGERS["crypto_disposals_without_basis"])
        # CONTENTS, not names: appending the acknowledgement to the existing
        # answers.md leaves every filename unchanged, so a name comparison
        # passes on exactly the persistence this test forbids.
        before = {p.name: p.read_bytes() for p in ws.iterdir() if p.is_file()}
        assert _run(ws, "--acknowledge", "crypto_disposals_without_basis")[0] == 0
        assert {p.name: p.read_bytes() for p in ws.iterdir() if p.is_file()} == before
        assert _run(ws)[0] == 2

    def test_a_fact_cannot_be_acknowledged_away(self, tmp_path):
        ws = _workspace(tmp_path)
        code, out = _run(ws, "--acknowledge", "document_set_complete")
        assert code == 1
        assert "FACT" in out and "answers.md" in out

    def test_an_unknown_id_is_a_hard_error_not_a_silent_no_op(self, tmp_path):
        ws = _workspace(tmp_path)
        code, out = _run(ws, "--acknowledge", "crypto_disposals_without_bass")
        assert code == 1
        assert "unknown required-answer ids" in out


class TestScopingToOneSkill:
    def test_for_tax_parse_reports_only_what_tax_parse_asks(self, tmp_path):
        ws = _workspace(tmp_path, **_TRIGGERS["foreign_account_completeness"])
        owed = {a.id for a in A.applicable(ws, for_skill="tax-parse")}
        assert owed == {"document_set_complete", "corrected_documents"}

    def test_a_question_another_skill_owns_cannot_block_the_parse(self, tmp_path):
        """Each gate acquires the other's false-positive surface otherwise."""
        ws = _workspace(tmp_path, answers=("- **[document_set_complete]** yes\n"
                                           "- **[corrected_documents]** none\n"),
                        **_TRIGGERS["foreign_account_completeness"])
        assert _run(ws, "--for", "tax-parse")[0] == 0
        assert _run(ws)[0] == 2

    def test_every_asking_skill_is_a_choice_the_cli_accepts(self, tmp_path):
        """Read back what argparse ACCEPTS, not the constant that built it — a
        choice dropped from the parser leaves the constant comparison green."""
        ws = _workspace(tmp_path)
        for skill in {a.asked_by.split()[0] for a in A.REQUIRED_ANSWERS}:
            assert _run(ws, "--for", skill)[0] in (0, 2), skill
        assert _run(ws, "--for", "tax-comptue")[0] == 1


class TestTheCli:
    def test_a_workspace_owing_nothing_exits_clean(self, tmp_path):
        ws = _workspace(tmp_path, answers=_ALL_FACTS_ANSWERED)
        code, out = _run(ws)
        assert code == 0, out
        assert "Every required answer this workspace owes is recorded." in out

    def test_a_workspace_owing_an_answer_is_BLOCKED(self, tmp_path):
        code, out = _run(_workspace(tmp_path))
        assert code == 2
        assert "document_set_complete" in out

    def test_a_missing_workspace_is_could_not_run(self, tmp_path):
        code, out = _run(tmp_path / "nope")
        assert code == 1
        assert "no workspace" in out

    def test_json_output_names_ids_and_never_an_answer(self, tmp_path):
        ws = _workspace(tmp_path, answers="- **[document_set_complete]** yes, all 11 forms\n")
        out = io.StringIO()
        with redirect_stdout(out):
            A._main(["--workspace", str(ws), "--check-required", "--json"])
        payload = json.loads(out.getvalue())
        assert payload["recorded"] == ["document_set_complete"]
        assert "all 11 forms" not in out.getvalue(), (
            "the report references answers; it never prints one")

    def test_the_in_flight_ids_are_named_even_on_the_clean_path(self, tmp_path):
        ws = _workspace(tmp_path, answers=("- **[document_set_complete]** yes\n"
                                           "- **[corrected_documents]** none\n"
                                           "- **[charitable_gifts]** none\n"))
        assert "software_filing_status_mismatch" in _run(ws)[1]

    def test_a_file_predating_the_contract_is_said_to_be_that(self, tmp_path):
        ws = _workspace(tmp_path, answers="## Confirmed Assumptions\n\nAll documents received.\n")
        assert "predates the required-answers contract" in A.render(ws)

    def test_an_unreadable_answers_file_is_could_not_run_and_names_no_path(self, tmp_path):
        """Two things at once. `OSError.filename` is the workspace path, which is
        named for the taxpayer, so an unhandled read puts it in a traceback
        unredacted. And the code is 1: reporting a file nobody could read as
        "you owe every answer" invites them to be typed again into a file that
        already has them, and under `--for` a scope with nothing applicable
        would have reported CLEAN on it."""
        ws = _workspace(tmp_path / "return-000-00-0000", answers=_ALL_FACTS_ANSWERED)
        (ws / "answers.md").chmod(0o000)
        try:
            if os.access(ws / "answers.md", os.R_OK):
                pytest.skip("this user can read a 000 file (root); nothing to test")
            code, out = _run(ws)
            scoped, _ = _run(ws, "--for", "tax-fbar")
        finally:
            (ws / "answers.md").chmod(0o600)
        assert code == 1
        assert scoped == 1, "a scope with nothing applicable must not read CLEAN"
        assert "000-00-0000" not in out

    def test_the_documented_module_invocation_works(self, tmp_path):
        """Both skills document `-m agents.lib.answers`; that is what is run."""
        ws = _workspace(tmp_path)
        proc = subprocess.run(
            [sys.executable, "-m", "agents.lib.answers",
             "--workspace", str(ws), "--check-required"],
            cwd=REPO, capture_output=True, text=True)
        assert proc.returncode == 2, proc.stderr
        assert "document_set_complete" in proc.stdout


class TestTheSkillsActuallyRunIt:
    @pytest.mark.parametrize("skill,later_step", [("tax-parse", "### 3."),
                                                  ("tax-compute", "Step 0.7")])
    def test_the_skill_runs_the_check_BEFORE_it_works(self, skill, later_step):
        """Position, not presence. The whole claim of V5 is that the questions
        are asked up FRONT; the command moved below the work would satisfy a
        substring check while satisfying nothing else."""
        body = (SKILLS_DIR / skill / "SKILL.md").read_text()
        assert "-m agents.lib.answers" in body
        assert "--check-required" in body
        assert f"--for {skill}" in body
        assert body.index("-m agents.lib.answers") < body.index(later_step)

    @pytest.mark.parametrize("skill", ["tax-parse", "tax-compute"])
    def test_the_skill_states_the_record_format(self, skill):
        assert "- **[" in skill_text(skill)

    def test_tax_compute_says_what_exit_1_obliges(self):
        """A could-not-run must block exactly as a missing engine does; read as
        "nothing owed" it is worse than no gate. So the sentence has to SAY
        that — "Exit 1" followed by anything at all would pass a bare substring
        check, including "Exit 1 — continue"."""
        text = skill_text("tax-compute")
        clause = text[text.index("**Exit 1**"):][:400]
        assert "do not proceed" in clause.lower()
