"""Form 1040 lines 1-24, derived rather than transcribed (V1, first slice).

The 1040's own arithmetic had no check anywhere: `tie_out` proves each document
reached a line and `capabilities` proves the engine can model the situation, but
nothing recomputed line 9 from its eight terms or line 24 from lines 22 and 23.
The agent did that in prose, and the skill's reference file carries a written
warning about forgetting line 6b — a warning that exists because a human is
doing the sum.

What these pin is the three properties that make deriving it worth anything:

1. **The derived lines reproduce the HAND-COMPUTED golden**, from the components
   alone, end to end through the eval fixture's own documents and law. That is
   the acceptance test for the whole slice: a builder scored against the old
   agent's output would only prove it copied the old agent.
2. **The line between a derived line and a component is DECLARED**, and covers
   the form. A field that is neither is a field nobody owns.
3. **Out of scope REFUSES.** A PFIC fund moves amounts out of lines 3a and 3b
   after the broker reported them; building those lines from the ledger would
   restore exactly the qualified treatment §1291 forbids.
"""

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents.lib import parse_records as PR  # noqa: E402
from agents.lib import return_builder as RB  # noqa: E402
from agents.lib import tie_out as T  # noqa: E402

GOLDEN_DIR = REPO / "evals" / "goldens" / "ca_mfj_2026"


def _law():
    """A minimal but complete federal block: round brackets, one status."""
    return {
        "federal": {
            "filing_statuses": {
                "mfj": {"standard_deduction": 30000,
                        "brackets": [{"rate": 0.10, "min": 0, "max": 20000},
                                     {"rate": 0.20, "min": 20000, "max": None}]},
            },
            "capital_gains_rates": {
                "zero_pct_limit": {"mfj": 90000},
                "fifteen_pct_limit": {"mfj": 550000},
            },
        },
    }


def _template_1040():
    """The shipped `form_1040`, zeros and all.

    Tests start from it rather than from the two or three lines each one cares
    about, because a COMPONENT the builder cannot find is now a refusal: an
    absent line is a line nobody has decided, and reading it as zero drops that
    income out of every total below it. `tax-setup` copies this template into
    every workspace, so a complete section is the state a real run is in.
    """
    doc = json.loads(
        (REPO / "agents" / "templates" / "result_template.json").read_text(
            encoding="utf-8"))["form_1040"]
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def _workspace(tmp_path, form_1040=None, *, records=(), law=None, extra=None,
               schedule_d=None, name="ws"):
    """A workspace the builder will read.

    `schedule_d` is a `(net_short, net_long)` pair. Line 7 is cross-checked
    against Schedule D, so a case that sets one must set the other — and it
    defaults to a long-term net EQUAL to line 7, which is what makes every case
    that does not care about Schedule D consistent without saying so.
    """
    ws = tmp_path / name
    (ws / "documents").mkdir(parents=True)
    (ws / "summaries").mkdir()
    lines = _template_1040()
    lines.update(form_1040 or {})
    net_st, net_lt = schedule_d if schedule_d else (0, lines["line_7_capital_gain"])
    result = {"meta": {"tax_year": 2026, "filing_status": "MFJ"},
              "form_1040": lines,
              "schedule_d": {
                  "part_i_short_term": {"line_7_net_short_term": net_st},
                  "part_ii_long_term": {"line_15_net_long_term": net_lt}}}
    result.update(extra or {})
    (ws / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (ws / "taxpayer_profile.json").write_text(
        json.dumps({"filing_status": "MFJ", "tax_year": 2026}), encoding="utf-8")
    (ws / "tax_law_2026.json").write_text(
        json.dumps(law if law is not None else _law()), encoding="utf-8")
    for i, key_boxes in enumerate(records):
        document = f"doc{i}.pdf"
        (ws / "documents" / document).write_bytes(b"%PDF-1.4 synthetic\n")
        PR.write_record(ws, {"doc_id": f"d{i}", "file": f"documents/{document}",
                             "form_type": "1099-MISC", "payer": "synthetic",
                             "person": "primary", "key_boxes": key_boxes})
    PR.write_ledger(ws)
    return ws


def _build(ws):
    result, profile, ledger, tax_law = RB.load(ws)
    return result, RB.build(result, tax_law, ledger,
                            RB._filing_status(profile, result))


class TestItReproducesTheHandComputedGolden:
    """The acceptance test for the slice.

    `evals/goldens/ca_mfj_2026` is derived from IRS worksheets in
    `derivation.md`, not captured from a run — which is the only reason it can
    score a builder at all. Feed this module the fixture's documents and the
    fixture's law, strip every derived line from the golden so nothing is
    copied, and require it to put all of them back.
    """

    def _fixture_workspace(self, tmp_path):
        sys.path.insert(0, str(REPO))
        from evals import fixture as F
        ws = tmp_path / "fixture"
        F.build_workspace(ws, "ca_mfj_2026")
        boxes = {
            "W2_Acme.pdf": ("w2", "W-2",
                            {"wages": 185000.0, "federal_withholding_w2": 28000.0}),
            "1099INT_Meridian.pdf": ("int", "1099-INT", {"taxable_interest": 1200.0}),
            "1099DIV_Northgate.pdf": ("div", "1099-DIV",
                                      {"ordinary_dividends": 3000.0,
                                       "qualified_dividends": 2600.0}),
            "1099B_Northgate.pdf": ("b", "1099-B", {"capital_proceeds": 22000.0}),
        }
        for name, (doc_id, form, key_boxes) in boxes.items():
            PR.write_record(ws, {"doc_id": doc_id, "file": f"documents/{name}",
                                 "form_type": form, "payer": "synthetic",
                                 "person": "joint", "key_boxes": key_boxes})
        PR.write_ledger(ws)
        golden = json.loads((GOLDEN_DIR / "golden_result.json").read_text(
            encoding="utf-8"))
        components = json.loads(json.dumps(golden))
        for field in RB.DERIVED_FIELDS:
            components["form_1040"].pop(field, None)
        (ws / "result.json").write_text(json.dumps(components, indent=2),
                                        encoding="utf-8")
        return ws, golden

    def test_every_derived_line_comes_back_at_the_golden_figure(self, tmp_path):
        ws, golden = self._fixture_workspace(tmp_path)
        _, derived = _build(ws)
        assert derived == {f: golden["form_1040"][f] for f in derived}

    def test_it_puts_back_every_line_it_stripped(self, tmp_path):
        """A builder that quietly derived FEWER lines would pass the test above
        on the ones it kept. The two together are what make it total."""
        ws, golden = self._fixture_workspace(tmp_path)
        _, derived = _build(ws)
        assert set(derived) == set(RB.DERIVED_FIELDS)
        assert RB.DERIVED_FIELDS <= set(golden["form_1040"])

    def test_the_worksheet_and_not_the_brackets_priced_this_return(self, tmp_path):
        """The golden's own point: ordinary income (155,100) is already above
        the MFJ 0% breakpoint, so none of the 9,600 preferential stack is at 0%
        and the worksheet's 34,122 beats a straight bracket run's 34,794. A
        builder that fell through to `compute_bracket_tax` would produce the
        higher figure, which is the direction that looks conservative and is
        still wrong."""
        ws, _ = self._fixture_workspace(tmp_path)
        _, derived = _build(ws)
        from agents.lib.tax_computations import compute_bracket_tax
        result, _p, _l, tax_law = RB.load(ws)
        brackets = tax_law["federal"]["filing_statuses"]["mfj"]["brackets"]
        straight, _ = compute_bracket_tax(derived["line_15_taxable_income"], brackets)
        assert derived["line_16_tax"] == 34122.0
        assert round(straight, 2) == 34794.0

    def test_the_cli_writes_the_stripped_return_back_to_the_golden(self, tmp_path):
        ws, golden = self._fixture_workspace(tmp_path)
        assert RB._main(["--workspace", str(ws)]) == 0
        written = json.loads((ws / "result.json").read_text(encoding="utf-8"))
        assert written["form_1040"] == golden["form_1040"]


class TestTheSliceBoundaryIsDeclaredAndCoversTheForm:
    """A field that is neither derived nor a declared component is one nobody
    owns — and the way a new line ends up silently outside every check."""

    def _template_1040(self):
        return json.loads(
            (REPO / "agents" / "templates" / "result_template.json").read_text(
                encoding="utf-8"))["form_1040"]

    def test_every_slice_field_exists_in_the_template(self):
        fields = {k for k in self._template_1040() if not k.startswith("_")}
        assert RB.SLICE_FIELDS <= fields, RB.SLICE_FIELDS - fields

    def test_every_template_line_1_to_24_is_in_the_slice(self):
        """The other direction, which is the one that catches an ADDED line.
        Parsed from the field NAME rather than listed again: `line_<n>` is the
        form's own numbering, and a new `line_20b_...` lands in the slice
        without anybody remembering to add it here."""
        outside = set()
        for field in self._template_1040():
            if not field.startswith("line_"):
                continue
            number = ""
            for ch in field[len("line_"):]:
                if not ch.isdigit():
                    break
                number += ch
            if number and int(number) <= 24 and field not in RB.SLICE_FIELDS:
                outside.add(field)
        assert not outside, f"1040 lines 1-24 outside the slice: {sorted(outside)}"

    def test_derived_and_component_partition_the_slice(self):
        assert RB.DERIVED_FIELDS | RB.COMPONENT_FIELDS == RB.SLICE_FIELDS
        assert not (RB.DERIVED_FIELDS & RB.COMPONENT_FIELDS)

    def test_the_judgement_lines_are_components(self):
        """Named individually because this is the product decision, not an
        implementation detail: whether a distribution is taxable, whether to
        itemize, what the CTC worksheet allows. If one of these ever becomes
        derived it must be a deliberate edit here."""
        for field in ("line_4b_taxable_ira", "line_5b_taxable_pensions",
                      "line_6b_taxable_ss", "line_7_capital_gain",
                      "line_8_schedule1_other", "line_10_adjustments",
                      "line_12_standard_deduction", "line_13a_qbi_deduction",
                      "line_19_ctc", "line_20_schedule3_line8",
                      "line_23_other_taxes"):
            assert field in RB.COMPONENT_FIELDS, field

    def test_line_1z_sums_every_line_1_field_the_schema_has(self):
        """1z is the sum of lines 1a THROUGH 1h. The template models only two of
        the eight today; listing those two by hand would leave a third silently
        out of the total on the day somebody adds it, which is the failure this
        module exists to stop, one level up."""
        template = {k for k in self._template_1040() if not k.startswith("_")}
        expected = {f for f in template
                    if f.startswith("line_1") and f != "line_1z_total_wages"
                    and not f[len("line_1")].isdigit()}
        assert set(RB._LINE_1Z_TERMS) == expected
        assert "line_1a_wages" in expected and "line_1h_other_earned_income" in expected

    def test_the_ledger_lines_are_read_out_of_the_gates_own_table(self):
        """Not listed twice. The mapping from a canonical category to the line
        it lands on belongs to `tie_out`, and a second copy here is a second
        chance for the builder to put wages somewhere the gate does not look."""
        assert RB.ledger_lines() == {
            "wages": "line_1a_wages",
            "tax_exempt_interest": "line_2a_tax_exempt_interest",
            "taxable_interest": "line_2b_taxable_interest",
            "qualified_dividends": "line_3a_qualified_dividends",
            "ordinary_dividends": "line_3b_ordinary_dividends",
            "ss_benefits": "line_6a_social_security",
        }

    def test_a_category_naming_two_lines_is_not_derived(self):
        """`retirement_gross` ties to lines 4a and 5a TOGETHER. The split
        between an IRA and a pension is not recoverable from their sum, so
        deriving either from it would invent one of the two."""
        assert T.CATEGORY_TARGETS["retirement_gross"][1] == [
            "line_4a_ira_distributions", "line_5a_pensions"]
        assert "line_4a_ira_distributions" not in RB.DERIVED_FIELDS
        assert "line_5a_pensions" not in RB.DERIVED_FIELDS

    def test_the_withholding_lines_stay_with_the_agent(self):
        """Lines 25a and 25b are categories with a single 1040 target, so only
        the slice boundary keeps them out. Half a payments section is worse than
        none: the agent cannot tell which half it still owns."""
        assert T.CATEGORY_TARGETS["federal_withholding_w2"][1] == [
            "line_25a_w2_withholding"]
        assert "line_25a_w2_withholding" not in RB.DERIVED_FIELDS
        assert "line_25b_1099_withholding" not in RB.DERIVED_FIELDS


class TestTheArithmeticIsTheFormsArithmetic:

    def test_line_9_includes_the_taxable_social_security(self, tmp_path):
        """The one the reference file warns about in prose. 6b is the TAXABLE
        benefit and it is in the sum; 6a is gross and is not."""
        ws = _workspace(tmp_path, {
            "line_1a_wages": 0, "line_6b_taxable_ss": 8500.0,
        }, records=[{"ss_benefits": 20000.0}])
        _, derived = _build(ws)
        assert derived["line_6a_social_security"] == 20000.0
        assert derived["line_9_total_income"] == 8500.0

    def test_line_9_sums_all_eight_terms(self, tmp_path):
        ws = _workspace(tmp_path, {
            "line_1h_other_earned_income": 500.0,
            "line_4b_taxable_ira": 100.0, "line_5b_taxable_pensions": 200.0,
            "line_6b_taxable_ss": 300.0, "line_7_capital_gain": 400.0,
            "line_8_schedule1_other": 600.0,
        }, records=[{"wages": 1000.0, "taxable_interest": 10.0,
                     "ordinary_dividends": 20.0}])
        _, derived = _build(ws)
        assert derived["line_1z_total_wages"] == 1500.0
        assert derived["line_9_total_income"] == 3130.0

    def test_tax_exempt_interest_raises_no_line_but_is_still_recorded(self, tmp_path):
        """Line 2a is a memo line: it is not in line 9. It is derived anyway
        because the taxable-SS worksheet reads it, and an unrecorded 2a is the
        classic muni surprise."""
        ws = _workspace(tmp_path, records=[{"tax_exempt_interest": 5000.0}])
        _, derived = _build(ws)
        assert derived["line_2a_tax_exempt_interest"] == 5000.0
        assert derived["line_9_total_income"] == 0.0

    def test_taxable_income_floors_at_zero(self, tmp_path):
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 30000.0},
                        records=[{"wages": 10000.0}])
        _, derived = _build(ws)
        assert derived["line_11_agi"] == 10000.0
        assert derived["line_15_taxable_income"] == 0.0
        assert derived["line_16_tax"] == 0.0

    def test_credits_cannot_drive_the_tax_below_zero(self, tmp_path):
        """Line 22 floors at zero: lines 19 and 20 are NONREFUNDABLE. A
        negative line 22 would flow into line 24 and come back as a refund the
        taxpayer is not owed."""
        ws = _workspace(tmp_path, {
            "line_12_standard_deduction": 0, "line_19_ctc": 9000.0,
        }, records=[{"wages": 25000.0}])
        _, derived = _build(ws)
        assert derived["line_16_tax"] == 3000.0
        assert derived["line_21_total_credits"] == 9000.0
        assert derived["line_22_tax_minus_credits"] == 0.0
        assert derived["line_24_total_tax"] == 0.0

    def test_other_taxes_are_added_after_the_credits(self, tmp_path):
        """Line 23 is on the far side of line 22 — self-employment tax is not
        reduced by the child tax credit."""
        ws = _workspace(tmp_path, {
            "line_12_standard_deduction": 0, "line_19_ctc": 9000.0,
            "line_23_other_taxes": 4000.0,
        }, records=[{"wages": 25000.0}])
        _, derived = _build(ws)
        assert derived["line_24_total_tax"] == 4000.0


class TestTheCapitalGainCharacterRuleIsTheSharedOne:
    """`net_capital_gains` decides what is preferential, and every other
    consumer of the QDCG worksheet nets through it. Two of its cases change the
    tax in opposite directions and neither is visible on a gains-only return."""

    def _ws(self, tmp_path, net_st, net_lt, qualified=4000.0, wages=60000.0,
            line_7=None, name="ws"):
        return _workspace(
            tmp_path,
            {"line_12_standard_deduction": 0,
             "line_7_capital_gain": (net_st + net_lt if line_7 is None
                                     else line_7)},
            name=name, schedule_d=(net_st, net_lt),
            records=[{"wages": wages, "qualified_dividends": qualified,
                      "ordinary_dividends": qualified}])

    def test_a_net_capital_LOSS_does_not_shrink_the_preferential_stack(
            self, tmp_path):
        """A capital loss reduces taxable income; it does not make a qualified
        dividend ordinary. Netting it INTO the preferential stack instead of
        flooring it at zero moves 3,000 of dividend from the 15% band to the
        ordinary brackets, over-stating the tax.

        The case has to be built to bite, and two earlier drafts of it did not.
        The income is above the 0% breakpoint, because below it the whole stack
        is priced at 0% either way. And the Schedule D loss EXCEEDS the
        dividends, because `compute_qdcg_tax` floors a negative `net_ltcg`
        itself — so a smaller loss reaches the same tax through the worksheet
        and only a stack driven NEGATIVE skips the worksheet entirely. Line 7
        carries the §1211 capped 3,000; Schedule D nets the full 5,000, which is
        the shape a real return has."""
        _, derived = _build(self._ws(tmp_path, 0.0, -5000.0, line_7=-3000.0,
                                     wages=200000.0))
        # 200,000 wages + 4,000 ordinary dividends - 3,000 allowed = 201,000.
        assert derived["line_15_taxable_income"] == 201000.0
        # Ordinary income (197,000) is already past the 90,000 breakpoint, so
        # the 4,000 preferential stack is priced at 15%: 37,400 + 600. Taxed at
        # ordinary rates instead it would be 38,200.
        assert derived["line_16_tax"] == 38000.0

    def test_a_short_term_gain_is_ordinary_and_not_preferential(self, tmp_path):
        """A gain that survives as SHORT-term keeps ordinary character. Treating
        Schedule D line 16 as preferential would price it at 0/15/20%, which is
        the under-statement direction."""
        _, short = _build(self._ws(tmp_path, 10000.0, 0.0, name="st"))
        _, long = _build(self._ws(tmp_path, 0.0, 10000.0, name="lt"))
        assert short["line_15_taxable_income"] == long["line_15_taxable_income"]
        assert short["line_16_tax"] > long["line_16_tax"]


class TestTheAmtReachesTotalTax:
    """The gap this slice closes. `form_6251.amt` was computed and recorded, and
    the 1040 had no line 17 field for it to land on — so an ISO exerciser's
    line 22 and line 24 were the regular tax alone, understating total tax by
    the whole AMT. That is the direction AGENTS.md §4 makes a hard error, on
    exactly the taxpayer Form 6251 is included for."""

    def _ws(self, tmp_path, amt):
        return _workspace(
            tmp_path,
            {"line_12_standard_deduction": 0, "line_19_ctc": 1000.0},
            records=[{"wages": 100000.0}],
            extra={"form_6251": {"amt": amt}})

    def test_the_amt_lands_on_line_17_and_raises_line_24(self, tmp_path):
        _, derived = _build(self._ws(tmp_path, 7500.0))
        assert derived["line_16_tax"] == 18000.0
        assert derived["line_17_schedule2_part1"] == 7500.0
        assert derived["line_18_tax_plus_schedule2"] == 25500.0
        assert derived["line_22_tax_minus_credits"] == 24500.0
        assert derived["line_24_total_tax"] == 24500.0

    def test_without_the_amt_line_18_is_line_16(self, tmp_path):
        _, derived = _build(self._ws(tmp_path, 0))
        assert derived["line_17_schedule2_part1"] == 0.0
        assert derived["line_18_tax_plus_schedule2"] == derived["line_16_tax"]

    def test_the_credits_are_netted_against_the_amt_inclusive_base(self, tmp_path):
        """Line 22 is 18 minus 21, not 16 minus 21 with the AMT added after.

        The credit has to EXCEED the regular tax for the two to differ, and the
        first version of this test used a credit smaller than it — where
        `max(0, 18 - 21)` and the wrong `max(0, 16 - 21) + 17` agree exactly,
        so the test passed on the defect it names. Here regular tax is 18,000,
        the AMT 7,500 and the credit 20,000: the form gives 5,500, and netting
        first floors at zero and hands back the whole 7,500."""
        ws = _workspace(
            tmp_path,
            {"line_12_standard_deduction": 0, "line_19_ctc": 20000.0},
            records=[{"wages": 100000.0}],
            extra={"form_6251": {"amt": 7500.0}})
        _, derived = _build(ws)
        assert derived["line_16_tax"] == 18000.0
        assert derived["line_18_tax_plus_schedule2"] == 25500.0
        assert derived["line_21_total_credits"] == 20000.0
        assert derived["line_22_tax_minus_credits"] == 5500.0

    def test_a_present_form_6251_with_a_null_amt_blocks(self, tmp_path):
        """The section is written when the form applies, so a null AMT is an
        unfinished computation — and read as zero it drops out of total tax
        exactly as the missing line-17 field used to."""
        ws = _workspace(tmp_path, records=[{"wages": 100000.0}],
                        extra={"form_6251": {"amt": None}})
        with pytest.raises(RB.Blocked, match="null"):
            _build(ws)

    def test_a_line_17_above_the_amt_blocks_instead_of_being_overwritten(
            self, tmp_path):
        """Schedule 2 Part I is the AMT plus excess advance premium tax credit,
        and the second is a capability-gate block. A LARGER line 17 is something
        this slice does not model, and rebuilding it from the AMT alone would
        take the difference out of total tax."""
        ws = _workspace(tmp_path,
                        {"line_17_schedule2_part1": 9000.0},
                        records=[{"wages": 100000.0}],
                        extra={"form_6251": {"amt": 7500.0}})
        with pytest.raises(RB.Blocked, match="line 17"):
            _build(ws)

    def test_a_stale_line_17_below_the_amt_is_simply_rebuilt(self, tmp_path):
        """The ordinary case: the first build pass ran before Step 6.5, so
        line 17 is the 0 it was then. Blocking on that would be a gate firing
        on correct in-progress work."""
        ws = _workspace(tmp_path,
                        {"line_12_standard_deduction": 0,
                         "line_17_schedule2_part1": 0},
                        records=[{"wages": 100000.0}],
                        extra={"form_6251": {"amt": 7500.0}})
        _, derived = _build(ws)
        assert derived["line_17_schedule2_part1"] == 7500.0


class TestAComponentIsNeverAssumed:
    """Every derived line reads its components and believes them, so what the
    components CANNOT be is where the arithmetic's honesty actually lives."""

    def test_an_absent_component_blocks_rather_than_reading_as_zero(self, tmp_path):
        """An absent line is a line nobody has decided. Read as zero it drops
        that income or that tax out of every total below it, while the totals
        still look computed."""
        ws = _workspace(tmp_path, records=[{"wages": 30000.0}])
        result = json.loads((ws / "result.json").read_text(encoding="utf-8"))
        del result["form_1040"]["line_8_schedule1_other"]
        (ws / "result.json").write_text(json.dumps(result), encoding="utf-8")
        with pytest.raises(RB.Blocked, match="line_8_schedule1_other"):
            _build(ws)

    def test_the_absent_component_block_is_the_blocked_exit_code(self, tmp_path):
        ws = _workspace(tmp_path, records=[{"wages": 30000.0}])
        result = json.loads((ws / "result.json").read_text(encoding="utf-8"))
        del result["form_1040"]["line_12_standard_deduction"]
        (ws / "result.json").write_text(json.dumps(result), encoding="utf-8")
        assert RB._main(["--workspace", str(ws)]) == 2

    @pytest.mark.parametrize("field", [
        "line_10_adjustments", "line_12_standard_deduction", "line_19_ctc",
        "line_23_other_taxes", "line_6b_taxable_ss"])
    def test_a_negative_magnitude_blocks(self, tmp_path, field):
        """A negative tax or deduction flows through the sums into a total tax
        below zero, which comes back as a refund nobody is owed. Type and
        finiteness do not catch it — -50,000 is a perfectly good float."""
        ws = _workspace(tmp_path, {field: -50000.0},
                        records=[{"wages": 30000.0}])
        with pytest.raises(RB.Blocked, match="negative"):
            _build(ws)

    def test_the_two_lines_that_MAY_be_negative_are(self, tmp_path):
        """A capital loss and a Schedule 1 net loss are real figures. Refusing
        them would be the gate crying wolf on a correct return."""
        ws = _workspace(tmp_path,
                        {"line_12_standard_deduction": 0,
                         "line_7_capital_gain": -3000.0,
                         "line_8_schedule1_other": -2000.0},
                        records=[{"wages": 30000.0}])
        _, derived = _build(ws)
        assert derived["line_9_total_income"] == 25000.0

    def test_the_negative_set_is_exactly_those_two(self):
        assert RB.MAY_BE_NEGATIVE == {"line_7_capital_gain",
                                      "line_8_schedule1_other"}

    def test_a_DERIVED_line_may_go_negative_and_the_build_stands(self, tmp_path):
        """The sign rule is on the INPUTS only. Line 9 is negative for a large
        enough Schedule 1 loss and line 11 for a taxpayer whose business lost
        money — both returns the IRS accepts. Checking the sign inside the
        reader every rule calls refused them at the moment the arithmetic
        produced the figure, which is the gate firing on correct data."""
        ws = _workspace(tmp_path,
                        {"line_12_standard_deduction": 31500.0,
                         "line_8_schedule1_other": -90000.0},
                        records=[{"wages": 30000.0}])
        _, derived = _build(ws)
        assert derived["line_9_total_income"] == -60000.0
        assert derived["line_11_agi"] == -60000.0
        # Line 15 is where a negative stops mattering, and it has the floor.
        assert derived["line_15_taxable_income"] == 0.0
        assert derived["line_24_total_tax"] == 0.0


class TestLine7IsCheckedAgainstScheduleD:
    """Line 7 is the ONE component with an independent second source in the
    file, so it is the one that can be checked rather than trusted — and the
    failure it catches is the worst-shaped one there is: the gain drops out of
    income while the QDCG worksheet, which reads Schedule D directly, still
    prices it and shrinks the ordinary band by it. Both halves look computed
    and the tax comes out low."""

    def _ws(self, tmp_path, line_7, net_st, net_lt):
        return _workspace(
            tmp_path, {"line_12_standard_deduction": 0,
                       "line_7_capital_gain": line_7},
            schedule_d=(net_st, net_lt), records=[{"wages": 100000.0}])

    def test_a_dropped_gain_blocks(self, tmp_path):
        with pytest.raises(RB.Blocked, match="line 7"):
            _build(self._ws(tmp_path, 0.0, 0.0, 20000.0))

    def test_a_matching_gain_builds(self, tmp_path):
        _, derived = _build(self._ws(tmp_path, 20000.0, 0.0, 20000.0))
        assert derived["line_9_total_income"] == 120000.0

    def test_the_1211_capped_loss_builds(self, tmp_path):
        """A net loss may not equal line 7: §1211 caps the deduction at 3,000
        and the Carryover Worksheet decides how much is used, which is the
        agent's judgement. The check is only that line 7 lies between the full
        loss and zero — the widest band the statute allows."""
        _, derived = _build(self._ws(tmp_path, -3000.0, 0.0, -9000.0))
        assert derived["line_9_total_income"] == 97000.0

    def test_a_loss_deeper_than_schedule_d_blocks(self, tmp_path):
        with pytest.raises(RB.Blocked, match="line 7"):
            _build(self._ws(tmp_path, -9000.0, 0.0, -3000.0))

    def test_a_sign_flipped_line_7_blocks(self, tmp_path):
        with pytest.raises(RB.Blocked, match="line 7"):
            _build(self._ws(tmp_path, 20000.0, 0.0, -20000.0))

    def test_a_line_7_with_no_schedule_d_at_all_blocks(self, tmp_path):
        ws = _workspace(tmp_path, {"line_7_capital_gain": 5000.0},
                        records=[{"wages": 100000.0}], extra={"schedule_d": {}})
        with pytest.raises(RB.Blocked, match="line 7"):
            _build(ws)


class TestOutOfScopeRefuses:

    def test_a_pfic_fund_blocks_the_build(self, tmp_path):
        ws = _workspace(tmp_path, records=[{"ordinary_dividends": 4000.0}],
                        extra={"form_8621_pfic": {"funds": [{"fund_id": "f1"}]}})
        with pytest.raises(RB.OutOfScope, match="PFIC"):
            _build(ws)

    def test_an_empty_pfic_section_does_not(self, tmp_path):
        """The template ships the section, and `_include_if` prose is not a
        gate. An all-zero section with no funds is a return with no PFIC."""
        ws = _workspace(tmp_path, records=[{"ordinary_dividends": 4000.0}],
                        extra={"form_8621_pfic": {"funds": []}})
        _, derived = _build(ws)
        assert derived["line_3b_ordinary_dividends"] == 4000.0

    def test_the_pfic_refusal_is_the_blocked_exit_code(self, tmp_path):
        ws = _workspace(tmp_path, records=[{"ordinary_dividends": 4000.0}],
                        extra={"form_8621_pfic": {"funds": [{"fund_id": "f1"}]}})
        assert RB._main(["--workspace", str(ws)]) == 2

    def test_preferential_income_with_no_breakpoints_refuses(self, tmp_path):
        """The shared guard: a cache whose `capital_gains_rates` was never
        fetched must not price qualified dividends at ordinary rates."""
        law = _law()
        law["federal"]["capital_gains_rates"] = {}
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"qualified_dividends": 5000.0,
                                  "ordinary_dividends": 5000.0}], law=law)
        with pytest.raises(RB.OutOfScope, match="tax_law|Capital gains"):
            _build(ws)

    def test_no_preferential_income_still_builds_without_breakpoints(self, tmp_path):
        """A wages-only return has nothing for a breakpoint to price, and
        refusing it would be the gate crying wolf on correct data."""
        law = _law()
        law["federal"]["capital_gains_rates"] = {}
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"wages": 30000.0}], law=law)
        _, derived = _build(ws)
        assert derived["line_16_tax"] == 4000.0

    def test_missing_brackets_refuse(self, tmp_path):
        law = _law()
        law["federal"]["filing_statuses"] = {}
        ws = _workspace(tmp_path, records=[{"wages": 30000.0}], law=law)
        with pytest.raises(RB.OutOfScope, match="brackets"):
            _build(ws)

    def test_a_net_negative_social_security_total_refuses(self, tmp_path):
        """Repayments exceeding gross benefits: the tie-out already records
        that line 6a has no convention for it. Writing a negative gross benefit
        would be inventing one."""
        ws = _workspace(tmp_path, records=[{"ss_benefits": -4000.0}])
        with pytest.raises(RB.OutOfScope, match="NEGATIVE"):
            _build(ws)

    @pytest.mark.parametrize("value", [float("nan"), float("inf")])
    def test_a_non_finite_component_refuses(self, tmp_path, value):
        """`json.loads` accepts NaN and Infinity, both pass an isinstance
        check, and then every comparison they touch is False — `max(0, nan)` is
        nan and the whole return prices at nan without raising anywhere."""
        ws = _workspace(tmp_path, records=[{"wages": 1000.0}])
        result = json.loads((ws / "result.json").read_text(encoding="utf-8"))
        result["form_1040"]["line_8_schedule1_other"] = value
        # `json.dumps` emits the bare NaN / Infinity literals that `json.loads`
        # then accepts — which is the whole reason the guard has to exist.
        (ws / "result.json").write_text(json.dumps(result), encoding="utf-8")
        with pytest.raises(RB.ReturnBuilderError, match="finite"):
            _build(ws)

    def test_a_string_component_refuses(self, tmp_path):
        ws = _workspace(tmp_path, {"line_10_adjustments": "1,200"},
                        records=[{"wages": 1000.0}])
        with pytest.raises(RB.ReturnBuilderError, match="finite number"):
            _build(ws)


class TestItDoesNotRepublishAnUnchangedReturn:
    """V8's validation receipt binds each gate's verdict to the DIGEST of the
    files it read, so rewriting an identical `result.json` invalidates a pass
    that is still true and `/tax-generate --filing-package` then refuses a
    package whose gates did run. This is a contract, not an optimization."""

    def test_a_second_run_leaves_the_file_untouched(self, tmp_path):
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"wages": 30000.0}])
        assert RB._main(["--workspace", str(ws)]) == 0
        first = (ws / "result.json").read_bytes()
        stamp = (ws / "result.json").stat().st_mtime_ns
        assert RB._main(["--workspace", str(ws)]) == 0
        assert (ws / "result.json").read_bytes() == first
        assert (ws / "result.json").stat().st_mtime_ns == stamp

    def test_the_second_run_says_it_wrote_nothing(self, tmp_path, capsys):
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"wages": 30000.0}])
        RB._main(["--workspace", str(ws)])
        capsys.readouterr()
        RB._main(["--workspace", str(ws)])
        assert "nothing written" in capsys.readouterr().out

    def test_it_is_idempotent_so_the_skill_may_run_it_twice(self, tmp_path):
        """The skill invokes it once before the credits and once after: the
        second pass is the same command over a fuller return."""
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"wages": 30000.0}])
        RB._main(["--workspace", str(ws)])
        after_first = json.loads((ws / "result.json").read_text(encoding="utf-8"))
        after_first["form_1040"]["line_19_ctc"] = 1000.0
        (ws / "result.json").write_text(json.dumps(after_first), encoding="utf-8")
        RB._main(["--workspace", str(ws)])
        final = json.loads((ws / "result.json").read_text(encoding="utf-8"))
        assert final["form_1040"]["line_21_total_credits"] == 1000.0
        assert final["form_1040"]["line_24_total_tax"] == 3000.0

    @pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
    def test_the_written_file_is_owner_only(self, tmp_path):
        """The one privacy property of this module a test can hold, and it is
        pinned to 0600 exactly rather than to "no group or other bits". The
        write goes through `secure_fs.write_secure`, which secures the
        descriptor before the first byte; a later refactor to a plain
        `path.write_text` would publish the whole return at the umask default
        and nothing else in the suite would notice."""
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"wages": 30000.0}])
        RB._main(["--workspace", str(ws)])
        assert (ws / "result.json").stat().st_mode & 0o777 == 0o600

    def test_the_bytes_match_the_other_writer_of_this_file(self, tmp_path):
        """`rollover.apply_changes` also publishes `result.json`, as
        `json.dumps(document, indent=2)` with no trailing newline. Two writers
        with two byte-formats churn the digest V8's receipt binds each gate's
        verdict to — which is the thing this whole class is about."""
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"wages": 30000.0}])
        RB._main(["--workspace", str(ws)])
        raw = (ws / "result.json").read_bytes()
        assert raw == json.dumps(json.loads(raw), indent=2).encode()

    def test_nothing_outside_form_1040_is_disturbed(self, tmp_path):
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"wages": 30000.0}],
                        extra={"california_540": {"ca_tax": 1234.0},
                               "meta": {"tax_year": 2026, "filing_status": "MFJ",
                                        "limitations": ["one"]}})
        RB._main(["--workspace", str(ws)])
        written = json.loads((ws / "result.json").read_text(encoding="utf-8"))
        assert written["california_540"] == {"ca_tax": 1234.0}
        assert written["meta"]["limitations"] == ["one"]


class TestTheCheckMode:

    def test_a_disagreeing_line_is_the_blocked_exit_code(self, tmp_path):
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0,
                                   "line_24_total_tax": 99.0},
                        records=[{"wages": 30000.0}])
        assert RB._main(["--workspace", str(ws), "--check"]) == 2

    def test_check_writes_nothing(self, tmp_path):
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0,
                                   "line_24_total_tax": 99.0},
                        records=[{"wages": 30000.0}])
        before = (ws / "result.json").read_bytes()
        RB._main(["--workspace", str(ws), "--check"])
        assert (ws / "result.json").read_bytes() == before

    def test_check_names_the_line_and_both_figures(self, tmp_path, capsys):
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0,
                                   "line_24_total_tax": 99.0},
                        records=[{"wages": 30000.0}])
        RB._main(["--workspace", str(ws), "--check"])
        err = capsys.readouterr().err
        assert "line 24" in err and "99.00" in err and "4,000.00" in err

    def test_a_built_return_passes_its_own_check(self, tmp_path):
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"wages": 30000.0}])
        assert RB._main(["--workspace", str(ws)]) == 0
        assert RB._main(["--workspace", str(ws), "--check"]) == 0


class TestTheReportAndTheOrder:

    def test_the_lines_are_reported_in_the_forms_own_order(self):
        """Sorted from the field names, so 9 comes before 11 and 13a before
        13b. A hand-kept order would be a third copy of the form's numbering,
        after the field name and the sort key, and the one nothing checks."""
        assert RB.DERIVED_ORDER == (
            "line_1a_wages", "line_1z_total_wages",
            "line_2a_tax_exempt_interest", "line_2b_taxable_interest",
            "line_3a_qualified_dividends", "line_3b_ordinary_dividends",
            "line_6a_social_security", "line_9_total_income", "line_11_agi",
            "line_14_total_deductions", "line_15_taxable_income",
            "line_16_tax", "line_17_schedule2_part1",
            "line_18_tax_plus_schedule2", "line_21_total_credits",
            "line_22_tax_minus_credits", "line_24_total_tax")

    def test_every_derived_line_has_a_stated_rule(self):
        assert set(RB.RULES) == set(RB.DERIVED_ORDER)
        assert all(RB.RULES[f].strip() for f in RB.DERIVED_ORDER)

    def test_the_report_names_the_rule_not_just_the_figures(self, tmp_path, capsys):
        """The agent is told to fix the COMPONENT rather than the line, so a
        bare `line 24: 99.00 -> 4,000.00` leaves it guessing at what to look
        at."""
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0,
                                   "line_24_total_tax": 99.0},
                        records=[{"wages": 30000.0}])
        RB._main(["--workspace", str(ws), "--check"])
        err = capsys.readouterr().err
        assert "(22 + 23)" in err

    def test_an_unusable_recorded_line_is_simply_rebuilt(self, tmp_path):
        """A line already on the return that is not a usable number — a bool, a
        string, a NaN — is one the build REPLACES, not one it refuses over. The
        refusal is for a COMPONENT: the difference is between reading a line and
        reporting on one."""
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0,
                                   "line_24_total_tax": True},
                        records=[{"wages": 30000.0}])
        _, derived = _build(ws)
        assert derived["line_24_total_tax"] == 4000.0
        changes = {c.field: c for c in RB.differences(
            json.loads((ws / "result.json").read_text(encoding="utf-8")), derived)}
        assert changes["line_24_total_tax"].was is None


class TestTheCacheGoesThroughTheSharedReader:
    """`tax_law_fetcher.load_tax_law` is the ONE read site every consumer
    shares, and it refuses two things a plain JSON read does not."""

    def test_a_non_finite_figure_anywhere_in_the_cache_refuses(self, tmp_path):
        """A NaN bracket rate passes the "is this a non-empty list" check and
        then defeats every comparison inside the worksheet — which is how a
        corrupt cache prices a whole preferential stack at 0%."""
        law = _law()
        law["federal"]["filing_statuses"]["mfj"]["brackets"][0]["rate"] = float("nan")
        ws = _workspace(tmp_path, records=[{"wages": 30000.0}], law=law)
        with pytest.raises(RB.ReturnBuilderError, match="non-finite"):
            _build(ws)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink")
    def test_a_cache_reached_through_a_symlink_refuses(self, tmp_path):
        """`is_reparse_point`, not `is_symlink`: the latter reads False for an
        NTFS junction. The target is kept INSIDE the workspace on purpose —
        one pointing out is refused a step earlier, by the path check, so only
        this shape exercises the reparse-point rule."""
        ws = _workspace(tmp_path, records=[{"wages": 30000.0}])
        real = ws / "cache_real.json"
        real.write_text(json.dumps(_law()), encoding="utf-8")
        (ws / "tax_law_2026.json").unlink()
        (ws / "tax_law_2026.json").symlink_to(real.name)
        with pytest.raises(RB.ReturnBuilderError, match="symlink|junction"):
            _build(ws)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink")
    def test_a_cache_pointing_OUT_of_the_workspace_refuses(self, tmp_path):
        ws = _workspace(tmp_path, records=[{"wages": 30000.0}])
        real = tmp_path / "elsewhere.json"
        real.write_text(json.dumps(_law()), encoding="utf-8")
        (ws / "tax_law_2026.json").unlink()
        (ws / "tax_law_2026.json").symlink_to(real)
        with pytest.raises(RB.ReturnBuilderError, match="traversal"):
            _build(ws)


class TestTheCouldNotRunCode:
    """1 is "could not run", 2 is "blocked". A missing input is the first: the
    workspace is not ready, and reporting it as a block would tell the agent a
    return was refused when nothing was ever read."""

    def test_a_missing_workspace_is_one(self, tmp_path):
        assert RB._main(["--workspace", str(tmp_path / "nope")]) == 1

    def test_a_missing_ledger_is_one(self, tmp_path):
        ws = _workspace(tmp_path, records=[{"wages": 1.0}])
        (ws / "parse_ledger.json").unlink()
        assert RB._main(["--workspace", str(ws)]) == 1

    def test_a_malformed_result_is_one(self, tmp_path):
        ws = _workspace(tmp_path, records=[{"wages": 1.0}])
        (ws / "result.json").write_text("{ not json", encoding="utf-8")
        assert RB._main(["--workspace", str(ws)]) == 1

    def test_a_missing_tax_law_cache_is_one(self, tmp_path):
        ws = _workspace(tmp_path, records=[{"wages": 1.0}])
        (ws / "tax_law_2026.json").unlink()
        assert RB._main(["--workspace", str(ws)]) == 1

    def test_a_ledger_the_gate_rejects_is_one(self, tmp_path):
        """Not 2. The ledger is `tax-parse`'s output and its own gate reports
        it; this module has no verdict to give about a return it never read."""
        ws = _workspace(tmp_path, records=[{"wages": 1.0}])
        (ws / "parse_ledger.json").write_text(
            json.dumps({"records": [{"doc_id": "x"}]}), encoding="utf-8")
        assert RB._main(["--workspace", str(ws)]) == 1

    def test_no_filing_status_anywhere_is_one(self, tmp_path):
        ws = _workspace(tmp_path, records=[{"wages": 1.0}])
        (ws / "result.json").write_text(
            json.dumps({"meta": {"tax_year": 2026}, "form_1040": {}}),
            encoding="utf-8")
        (ws / "taxpayer_profile.json").write_text(json.dumps({"tax_year": 2026}),
                                                  encoding="utf-8")
        assert RB._main(["--workspace", str(ws)]) == 1

    def test_the_return_meta_status_wins_over_a_later_profile_edit(self, tmp_path):
        """A profile edited after the return was computed must not silently
        re-price it under the new status."""
        ws = _workspace(tmp_path, records=[{"wages": 1.0}])
        profile = json.loads((ws / "taxpayer_profile.json").read_text(
            encoding="utf-8"))
        profile["filing_status"] = "Single"
        (ws / "taxpayer_profile.json").write_text(json.dumps(profile),
                                                  encoding="utf-8")
        result = json.loads((ws / "result.json").read_text(encoding="utf-8"))
        assert RB._filing_status(profile, result) == "MFJ"


class TestTheSkillTellsTheAgentToRunIt:

    def _skill(self, name):
        return (REPO / ".agents" / "skills" / name / "SKILL.md").read_text(
            encoding="utf-8")

    def test_tax_compute_names_the_module_and_all_three_exit_codes(self):
        text = self._skill("tax-compute")
        assert "-m agents.lib.return_builder --workspace ." in text
        section = text.split("return_builder")[1]
        for code in ("Exit 0", "Exit 2", "Exit 1"):
            assert code in section, code

    def test_the_skill_does_not_ask_the_agent_to_type_a_derived_line(self):
        """The reference file's line-9 formula is the instruction this module
        replaces. It may still SHOW the form's arithmetic — a reader needs to
        know what line 9 is — but it must name the builder as what does it."""
        text = (REPO / ".agents" / "skills" / "tax-compute" / "references"
                / "income-and-agi.md").read_text(encoding="utf-8")
        assert "return_builder" in text
