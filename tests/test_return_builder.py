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
from agents.lib import receipt as RC  # noqa: E402
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
    result, profile, ledger, tax_law, _bytes = RB.load(ws)
    return result, RB.build(result, tax_law, ledger,
                            RB._filing_status(profile, result), profile)


class TestItReproducesTheHandComputedGolden:
    """The acceptance test for the slice.

    `evals/goldens/ca_mfj_2026` is derived from IRS worksheets in
    `derivation.md`, not captured from a run — which is the only reason it can
    score a builder at all. Feed this module the fixture's documents and the
    fixture's law, strip every derived line from the golden so nothing is
    copied, and require it to put all of them back.
    """

    # The documents each case's ledger records. `zero_income` has none, and that
    # is the point of running this class over it: a builder is asked to derive
    # twenty-four lines from an EMPTY intake, which is where "absent is not
    # zero" and "zero is not absent" are the same code path read two ways.
    _RECORDS = {
        "ca_mfj_2026": {
            "W2_Acme.pdf": ("w2", "W-2",
                            {"wages": 185000.0, "federal_withholding_w2": 28000.0}),
            "1099INT_Meridian.pdf": ("int", "1099-INT", {"taxable_interest": 1200.0}),
            "1099DIV_Northgate.pdf": ("div", "1099-DIV",
                                      {"ordinary_dividends": 3000.0,
                                       "qualified_dividends": 2600.0}),
            "1099B_Northgate.pdf": ("b", "1099-B", {"capital_proceeds": 22000.0}),
        },
        "zero_income": {},
    }
    _GOLDEN_OF = {"ca_mfj_2026": "ca_mfj_2026", "zero_income": "zero_income_2026"}

    def _fixture_workspace(self, tmp_path, case="ca_mfj_2026"):
        sys.path.insert(0, str(REPO))
        from evals import fixture as F
        ws = tmp_path / "fixture"
        F.build_workspace(ws, case)
        for name, (doc_id, form, key_boxes) in self._RECORDS[case].items():
            PR.write_record(ws, {"doc_id": doc_id, "file": f"documents/{name}",
                                 "form_type": form, "payer": "synthetic",
                                 "person": "joint", "key_boxes": key_boxes})
        PR.write_ledger(ws)
        golden = json.loads(
            (REPO / "evals" / "goldens" / self._GOLDEN_OF[case]
             / "golden_result.json").read_text(encoding="utf-8"))
        components = json.loads(json.dumps(golden))
        for field in RB.DERIVED_FIELDS:
            components["form_1040"].pop(field, None)
        (ws / "result.json").write_text(json.dumps(components, indent=2),
                                        encoding="utf-8")
        return ws, golden

    @pytest.mark.parametrize("case", ["ca_mfj_2026", "zero_income"])
    def test_every_derived_line_comes_back_at_the_golden_figure(self, tmp_path, case):
        """Over BOTH goldens. This class is the only thing in the repo that
        re-prices a golden through the builder, and it was pinned to the first
        one — so seventeen of eighteen mutations of the second left every test
        that reads it green."""
        ws, golden = self._fixture_workspace(tmp_path, case)
        _, derived = _build(ws)
        assert derived == {f: golden["form_1040"][f] for f in derived}

    @pytest.mark.parametrize("case", ["ca_mfj_2026", "zero_income"])
    def test_it_puts_back_every_line_it_stripped(self, tmp_path, case):
        """A builder that quietly derived FEWER lines would pass the test above
        on the ones it kept. The two together are what make it total."""
        ws, golden = self._fixture_workspace(tmp_path, case)
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
        result, _p, _l, tax_law, _b = RB.load(ws)
        brackets = tax_law["federal"]["filing_statuses"]["mfj"]["brackets"]
        straight, _ = compute_bracket_tax(derived["line_15_taxable_income"], brackets)
        assert derived["line_16_tax"] == 34122.0
        assert round(straight, 2) == 34794.0

    @pytest.mark.parametrize("case", ["ca_mfj_2026", "zero_income"])
    def test_the_cli_writes_the_stripped_return_back_to_the_golden(self, tmp_path, case):
        ws, golden = self._fixture_workspace(tmp_path, case)
        # This golden covers Form1040 TY2026, not an unreviewed Form540.
        # Exercise the federal CLI in isolation; CA's own suite proves the
        # TY2026 refusal and its separately reviewed TY2025 arithmetic.
        result = json.loads((ws / "result.json").read_text())
        result.pop("california_540", None)
        (ws / "result.json").write_text(json.dumps(result))
        profile = json.loads((ws / "taxpayer_profile.json").read_text())
        profile["address"]["state"] = "TX"
        (ws / "taxpayer_profile.json").write_text(json.dumps(profile))
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

    def test_every_numbered_template_line_is_inside_the_boundary(self):
        """The other direction, which is the one that catches an ADDED line.
        Parsed from the field NAME rather than listed again: `line_<n>` is the
        form's own numbering, and a new `line_20b_...` lands inside the
        boundary without anybody remembering to add it here."""
        outside = {f for f in self._template_1040()
                   if f.startswith("line_") and f not in RB.SLICE_FIELDS}
        assert not outside, f"1040 lines outside the boundary: {sorted(outside)}"

    def test_the_1040_fields_that_are_NOT_numbered_lines_stay_outside(self):
        """`schedule_e_k1_income` is /tax-ptet's phantom-income base and
        `schedule_3_line_11_excess_ss` is a persisted audit trail for a figure
        that flows into line 31 — neither is a box on the form, so neither is a
        line this module owns or requires."""
        for field in ("schedule_e_k1_income", "schedule_3_line_11_excess_ss",
                      "digital_asset_question"):
            assert field in self._template_1040()
            assert field not in RB.SLICE_FIELDS

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
        chance for the builder to put wages somewhere the gate does not look.

        The two withholding lines joined this set when the boundary reached
        line 38 — without an edit, because the rule is "one `form_1040` field
        inside the boundary" and the boundary is what moved."""
        assert RB.ledger_lines() == {
            "wages": "line_1a_wages",
            "tax_exempt_interest": "line_2a_tax_exempt_interest",
            "taxable_interest": "line_2b_taxable_interest",
            "qualified_dividends": "line_3a_qualified_dividends",
            "ordinary_dividends": "line_3b_ordinary_dividends",
            "ss_benefits": "line_6a_social_security",
            "federal_withholding_w2": "line_25a_w2_withholding",
            "federal_withholding_1099": "line_25b_1099_withholding",
        }

    def test_a_category_naming_two_lines_is_not_derived(self):
        """`retirement_gross` ties to lines 4a and 5a TOGETHER. The split
        between an IRA and a pension is not recoverable from their sum, so
        deriving either from it would invent one of the two."""
        assert T.CATEGORY_TARGETS["retirement_gross"][1] == [
            "line_4a_ira_distributions", "line_5a_pensions"]
        assert "line_4a_ira_distributions" not in RB.DERIVED_FIELDS
        assert "line_5a_pensions" not in RB.DERIVED_FIELDS

    def test_the_withholding_lines_come_from_the_ledger_too(self):
        """Box 2 of a W-2 is a transcription exactly as box 1 is, and it is the
        one figure on the payments side with a parsed document behind it. The
        first slice left both out because half a payments section is worse than
        none — the agent cannot tell which half it still owns — and the second
        slice is where the other half arrived."""
        assert T.CATEGORY_TARGETS["federal_withholding_w2"][1] == [
            "line_25a_w2_withholding"]
        assert "line_25a_w2_withholding" in RB.DERIVED_FIELDS
        assert "line_25b_1099_withholding" in RB.DERIVED_FIELDS


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


class TestThePaymentsSideReachesTheRefund:
    """V1's second slice. Lines 25-38 had the same shape as lines 1-24 — a
    column of figures summed by hand — and two of them had no field at all."""

    def _ws(self, tmp_path, lines=None, extra=None, records=None, name="ws"):
        base = {"line_12_standard_deduction": 0}
        base.update(lines or {})
        return _workspace(
            tmp_path, base, extra=extra, name=name,
            records=records if records is not None else [
                {"wages": 30000.0, "federal_withholding_w2": 5000.0}])

    def test_the_withholding_lines_are_the_ledgers_own_sums(self, tmp_path):
        _, derived = _build(self._ws(tmp_path, records=[
            {"wages": 30000.0, "federal_withholding_w2": 5000.0},
            {"federal_withholding_1099": 700.0}]))
        assert derived["line_25a_w2_withholding"] == 5000.0
        assert derived["line_25b_1099_withholding"] == 700.0

    def test_line_25d_adds_the_other_withholding(self, tmp_path):
        """25c is the Form 8959 additional-Medicare over-withholding, and it is
        the agent's — but it is IN the total."""
        _, derived = _build(self._ws(tmp_path,
                                     {"line_25c_other_withholding": 450.0}))
        assert derived["line_25d_total_withholding"] == 5450.0

    def test_the_refundable_ACTC_reaches_line_33(self, tmp_path):
        """The defect this slice closes. `compute_child_tax_credit` splits into
        `nonrefundable_used` (line 19) and `refundable_actc`, and line 28 had no
        field — so the computed figure was thrown away and the refund was short
        by it, for exactly the families the credit exists for."""
        _, derived = _build(self._ws(tmp_path,
                                     {"line_28_refundable_actc": 1600.0}))
        assert derived["line_33_total_payments"] == 6600.0
        assert derived["line_34_overpayment"] == 2600.0
        assert derived["line_35a_refund"] == 2600.0

    def test_line_33_sums_every_term(self, tmp_path):
        _, derived = _build(self._ws(tmp_path, {
            "line_25c_other_withholding": 10.0,
            "line_26_estimated_payments": 20.0, "line_27_eic": 30.0,
            "line_28_refundable_actc": 40.0, "line_29_refundable_aotc": 50.0,
            "line_31_schedule3_part2": 60.0}))
        assert derived["line_33_total_payments"] == 5210.0

    def test_an_overpayment_and_an_amount_owed_are_mutually_exclusive(self, tmp_path):
        """Both are floored at zero and they are the two sides of one
        comparison. Deriving line 37 as the negation of line 34 would print a
        negative in the box on every refund."""
        _, big_tax = _build(self._ws(tmp_path, records=[{"wages": 30000.0}],
                                     name="owes"))
        assert big_tax["line_37_amount_owed"] == 4000.0
        assert big_tax["line_34_overpayment"] == 0.0
        _, refund = _build(self._ws(tmp_path, {"line_26_estimated_payments": 9000.0},
                                    records=[{"wages": 30000.0}], name="refund"))
        assert refund["line_34_overpayment"] == 5000.0
        assert refund["line_37_amount_owed"] == 0.0

    def test_an_amount_applied_to_next_year_comes_off_the_refund(self, tmp_path):
        _, derived = _build(self._ws(tmp_path, {
            "line_26_estimated_payments": 9000.0,
            "line_36_applied_to_next_year": 2000.0},
            records=[{"wages": 30000.0}], name="applied"))
        assert derived["line_34_overpayment"] == 5000.0
        assert derived["line_35a_refund"] == 3000.0

    def test_applying_more_than_the_overpayment_blocks(self, tmp_path):
        """The one check that has to run AFTER the build: line 36 is a
        component bounded by DERIVED figures, so there is nothing to compare it
        against until the arithmetic has run."""
        ws = self._ws(tmp_path, {"line_26_estimated_payments": 9000.0,
                                 "line_36_applied_to_next_year": 8000.0},
                      records=[{"wages": 30000.0}])
        with pytest.raises(RB.Blocked, match="line 36"):
            _build(ws)

    def test_a_balance_due_return_carrying_nothing_forward_is_not_blocked(
            self, tmp_path):
        """The bound is floored at zero. A return that OWES tax has a negative
        amount available and applies nothing forward, and comparing against the
        raw figure blocked every one of them on a line reading 0 — a gate
        firing on the most ordinary return there is."""
        _, derived = _build(self._ws(tmp_path, {
            "line_38_estimated_tax_penalty": 275.0},
            records=[{"wages": 30000.0}], name="plain"))
        assert derived["line_37_amount_owed"] == 4275.0

    def test_carrying_forward_with_nothing_available_still_blocks(self, tmp_path):
        ws = self._ws(tmp_path, {"line_36_applied_to_next_year": 100.0},
                      records=[{"wages": 30000.0}], name="nothing")
        with pytest.raises(RB.Blocked, match="available"):
            _build(ws)

    def test_the_penalty_narrows_what_may_be_carried_forward(self, tmp_path):
        """The bound is what is AVAILABLE, not line 34, and line 35a floors at
        zero — so carrying forward money the penalty has already consumed would
        not show up as a negative refund. It would VANISH, leaving the penalty
        unpaid on a return that shows nothing owed."""
        ws = self._ws(tmp_path, {"line_26_estimated_payments": 6000.0,
                                 "line_38_estimated_tax_penalty": 275.0,
                                 "line_36_applied_to_next_year": 2000.0},
                      records=[{"wages": 30000.0}], name="squeezed")
        with pytest.raises(RB.Blocked, match="available"):
            _build(ws)
        ok = self._ws(tmp_path, {"line_26_estimated_payments": 6000.0,
                                 "line_38_estimated_tax_penalty": 275.0,
                                 "line_36_applied_to_next_year": 1725.0},
                      records=[{"wages": 30000.0}], name="fits")
        _, derived = _build(ok)
        assert derived["line_35a_refund"] == 0.0
        assert derived["line_37_amount_owed"] == 0.0

    def test_the_estimated_tax_penalty_IS_inside_the_amount_owed(self, tmp_path):
        """The 1040's line-37 instruction says to add the line 38 penalty to
        the amount owed, and `references/tax-and-credits.md` step 9.5 has said
        so all along. The first draft of this slice left it out of both
        settlement lines and shipped a test asserting that was right: $4,000 of
        tax against a $275 penalty came back as $4,000 owed. That is the
        under-statement direction on a figure the taxpayer pays, certified by
        its own test."""
        _, derived = _build(self._ws(tmp_path,
                                     {"line_38_estimated_tax_penalty": 275.0},
                                     records=[{"wages": 30000.0}], name="owe"))
        assert derived["line_37_amount_owed"] == 4275.0
        assert derived["line_34_overpayment"] == 0.0

    def test_the_penalty_comes_out_of_a_refund(self, tmp_path):
        """Line 34 is the form's "amount overpaid" and is taken BEFORE the
        penalty — payments against tax, full stop. The penalty then reduces
        what is actually refunded."""
        _, derived = _build(self._ws(tmp_path, {
            "line_26_estimated_payments": 5000.0,
            "line_38_estimated_tax_penalty": 275.0},
            records=[{"wages": 30000.0}], name="refund"))
        assert derived["line_34_overpayment"] == 1000.0
        assert derived["line_35a_refund"] == 725.0
        assert derived["line_37_amount_owed"] == 0.0

    def test_a_penalty_larger_than_the_overpayment_becomes_a_balance_due(
            self, tmp_path):
        """The case the two formulas have to agree on: both are written from
        the same three figures, so the remainder lands on line 37 without a
        third branch deciding which side the return is on."""
        _, derived = _build(self._ws(tmp_path, {
            "line_26_estimated_payments": 4100.0,
            "line_38_estimated_tax_penalty": 275.0},
            records=[{"wages": 30000.0}], name="both"))
        assert derived["line_34_overpayment"] == 100.0
        assert derived["line_35a_refund"] == 0.0
        assert derived["line_37_amount_owed"] == 175.0

    def test_excess_social_security_larger_than_schedule_3_part_2_blocks(
            self, tmp_path):
        """`schedule_3_line_11_excess_ss` is persisted so the credit stays
        auditable, but it FLOWS INTO line 31, which is what line 33 adds. An
        excess larger than the total it belongs to means line 31 never picked
        it up (the credit is lost) or it is being counted twice."""
        ws = self._ws(tmp_path, {"line_31_schedule3_part2": 100.0},
                      extra=None)
        result = json.loads((ws / "result.json").read_text(encoding="utf-8"))
        result["form_1040"]["schedule_3_line_11_excess_ss"] = 900.0
        (ws / "result.json").write_text(json.dumps(result), encoding="utf-8")
        with pytest.raises(RB.Blocked, match="excess social security"):
            _build(ws)

    def test_an_excess_inside_part_2_is_fine(self, tmp_path):
        ws = self._ws(tmp_path, {"line_31_schedule3_part2": 900.0})
        result = json.loads((ws / "result.json").read_text(encoding="utf-8"))
        result["form_1040"]["schedule_3_line_11_excess_ss"] = 900.0
        (ws / "result.json").write_text(json.dumps(result), encoding="utf-8")
        _, derived = _build(ws)
        assert derived["line_33_total_payments"] == 5900.0

    def test_a_negative_excess_blocks(self, tmp_path):
        ws = self._ws(tmp_path)
        result = json.loads((ws / "result.json").read_text(encoding="utf-8"))
        result["form_1040"]["schedule_3_line_11_excess_ss"] = -50.0
        (ws / "result.json").write_text(json.dumps(result), encoding="utf-8")
        with pytest.raises(RB.Blocked, match="negative one reaches line 33"):
            _build(ws)


class TestOutOfScopeRefuses:

    def test_a_pfic_fund_blocks_the_build(self, tmp_path):
        ws = _workspace(tmp_path, records=[{"ordinary_dividends": 4000.0}],
                        extra={"form_8621_pfic": {"funds": [{"fund_id": "f1"}]}})
        with pytest.raises(RB.OutOfScope, match="PFIC"):
            _build(ws)

    def test_a_reclassified_total_with_no_funds_is_still_out_of_scope(self, tmp_path):
        """`reclassified_from_line_3b` is a SIBLING of `funds`, not an entry in
        it. Keyed on `funds` alone, a section §1291 has reworked reads as in
        scope, and the builder rebuilds line 3b from the ledger — restoring the
        ordinary/qualified treatment §1291 removed, which is the one outcome
        this refusal exists to prevent."""
        ws = _workspace(tmp_path, records=[{"ordinary_dividends": 4000.0}],
                        extra={"form_8621_pfic": {
                            "funds": [], "reclassified_from_line_3b": 8000.0}})
        with pytest.raises(RB.OutOfScope, match="PFIC"):
            _build(ws)

    def test_a_counterfactual_is_not_evidence_that_1291_ran(self, tmp_path):
        """`line_16_regular_tax` is what the return WOULD have been WITHOUT
        §1291 — an audit trail the PFIC computation records, not a sign it
        changed a line. This is the one predicate where a FALSE POSITIVE is the
        dangerous direction, because it grants the pass: read as evidence, a
        return with a typed refund and no PFIC at all came back NOT CHECKED."""
        ws = _workspace(tmp_path, {"line_35a_refund": 5000.0},
                        records=[{"ordinary_dividends": 4000.0}],
                        extra={"form_8621_pfic": {"funds": [],
                                                  "line_16_regular_tax": 100.0}})
        assert RB._main(["--workspace", str(ws), "--check"]) == 2

    def test_every_field_of_the_shipped_pfic_section_is_decided(self):
        """No gap between evidence and not-evidence. A field added to the
        section lands on one side or the other, and the side that grants a
        waiver is the one nothing else checks."""
        section = json.loads(
            (REPO / "agents" / "templates" / "result_template.json").read_text(
                encoding="utf-8"))["form_8621_pfic"]
        keys = {k for k in section if not k.startswith("_")}
        not_evidence = {
            "funds",                        # handled separately: a LIST, not a number
            "all_complete",                 # a completeness flag, not an amount
            "total_ordinary_income_current",  # components of the add-on below
            "total_nonexcess_ordinary",
            "total_deferred_tax",
            "total_interest",
            "line_15_pfic_ordinary_income",
            "line_9_regular_total_income",  # the counterfactual return, WITHOUT §1291
            "line_11_regular_agi",
            "line_15_regular_taxable_income",
            "line_16_regular_tax",
            "line_24_regular_total_tax",
            "line_24_with_addon",           # line 24 restated; `line_24_addon` is the move
        }
        assert keys == set(RB._PFIC_EVIDENCE) | not_evidence

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
        fetched must not price qualified dividends at ordinary rates.

        `Unpriceable`, not `OutOfScope`: an unfetched cache is a return nobody
        can compute yet, and the check-mode pass out-of-scope now carries would
        have let one into a filing package."""
        law = _law()
        law["federal"]["capital_gains_rates"] = {}
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"qualified_dividends": 5000.0,
                                  "ordinary_dividends": 5000.0}], law=law)
        with pytest.raises(RB.Unpriceable, match="tax_law|Capital gains"):
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
        with pytest.raises(RB.Unpriceable, match="brackets"):
            _build(ws)

    def test_a_negative_total_in_an_UNSIGNED_category_is_a_block_not_a_waiver(
            self, tmp_path):
        """Only `ss_benefits` may go negative, and only that one is a line with
        no convention to derive. Any other category summing negative is corrupt
        data — `load_ledger` refuses it record by record — and must not share
        the verdict that `--check` now records as a pass."""
        with pytest.raises(RB.Blocked, match="may be negative") as caught:
            RB.ledger_income([T.LedgerRecord(
                doc_id="d1", file="documents/a.pdf", form_type="1099-INT",
                payer="p", person="primary",
                key_boxes=(("taxable_interest", -100.0),),
                skipped=None, second_read=None)])
        assert not isinstance(caught.value, RB.OutOfScope)

    def test_a_net_negative_social_security_total_refuses(self, tmp_path):
        """Repayments exceeding gross benefits: the tie-out already records
        that line 6a has no convention for it. Writing a negative gross benefit
        would be inventing one.

        BLOCKED and not OUT OF SCOPE, because this refusal is about ONE LINE
        and fires mid-build: the pass would waive the comparison of every other
        line, and a hand-typed line 35a refund went through all three gates
        that way."""
        ws = _workspace(tmp_path, records=[{"ss_benefits": -4000.0}])
        with pytest.raises(RB.Blocked, match="NEGATIVE") as caught:
            _build(ws)
        assert not isinstance(caught.value, RB.OutOfScope)

    def test_a_typed_refund_is_not_waived_by_the_line_6a_refusal(self, tmp_path):
        """The failure that made the distinction: no payments, no tax, and a
        line 35a of $5,000 — waived by an out-of-scope pass, and a filing
        package assembled on it."""
        ws = _workspace(tmp_path, {"line_35a_refund": 5000.0},
                        records=[{"ss_benefits": -4000.0}])
        assert RB._main(["--workspace", str(ws), "--check"]) == 2

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


class TestTheCheckIsReceipted:
    """Step 12.6 writes its verdict into `validation_receipt.json`, so
    `/tax-generate --filing-package` can prove the derived lines were checked
    against the inputs that are here now (V8). Without it the check was a gate
    an agent could skip with nothing downstream able to tell."""

    def _entry(self, ws):
        data = json.loads((ws / RC.RECEIPT_NAME).read_text(encoding="utf-8"))
        return data["gates"].get(RB.GATE)

    def test_a_passing_check_records_a_pass_bound_to_what_it_read(self, tmp_path):
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"wages": 30000.0}])
        assert RB._main(["--workspace", str(ws)]) == 0
        assert RB._main(["--workspace", str(ws), "--check"]) == 0
        entry = self._entry(ws)
        assert entry["outcome"] == RC.PASS
        names = {v["name"] for v in entry["inputs"].values()}
        assert names == {"result.json", "parse_ledger.json",
                         "taxpayer_profile.json", "tax_law_2026.json"}

    def test_a_disagreeing_check_is_recorded_rather_than_left_unwritten(self, tmp_path):
        """"This gate disagreed" and "this gate never ran" are both refusals,
        and only one of them is answered by running the gate."""
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0,
                                   "line_24_total_tax": 99.0},
                        records=[{"wages": 30000.0}])
        assert RB._main(["--workspace", str(ws), "--check"]) == 2
        assert self._entry(ws)["outcome"] == "FAILED"

    def test_a_refused_return_is_recorded_as_blocked(self, tmp_path):
        """A return this slice will not build is a different remedy from one
        whose line is stale, so the receipt says which."""
        ws = _workspace(tmp_path, {"line_12_standard_deduction": -50000.0},
                        records=[{"wages": 30000.0}])
        assert RB._main(["--workspace", str(ws), "--check"]) == 2
        assert self._entry(ws)["outcome"] == "BLOCKED"

    def test_a_check_that_could_not_run_records_nothing(self, tmp_path):
        """Exit 1 is "no verdict". A receipt here would say the gate ran and
        disagreed, sending the agent after a line when the input is missing."""
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"wages": 30000.0}])
        (ws / "tax_law_2026.json").unlink()
        assert RB._main(["--workspace", str(ws), "--check"]) == 1
        assert not (ws / RC.RECEIPT_NAME).exists()

    def test_the_write_mode_certifies_nothing(self, tmp_path):
        """It rewrites the very file a receipt would be bound to; a verdict
        recorded there would be stale before the command returned."""
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0,
                                   "line_24_total_tax": 99.0},
                        records=[{"wages": 30000.0}])
        assert RB._main(["--workspace", str(ws)]) == 0
        assert not (ws / RC.RECEIPT_NAME).exists()

    def test_the_receipt_holds_the_bytes_the_builder_PARSED(self, tmp_path,
                                                            monkeypatch):
        """Hashing the paths in a second read left a window: a `result.json`
        replaced between the hash and the parse produced a PASS bound to bytes
        the builder never evaluated, and the same check re-run a second later
        exited 2 on the file the receipt said was fine.

        Asserted at the CALL, because the two orders agree on every workspace
        that holds still — which is exactly why the defect survived a suite
        that only ever looked at the result."""
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"wages": 30000.0}])
        RB._main(["--workspace", str(ws)])
        seen: dict = {}
        real = RC.gate_inputs

        def watching(workspace, gate, *, contents=None):
            seen["contents"] = contents
            return real(workspace, gate, contents=contents)

        monkeypatch.setattr(RB.receipt, "gate_inputs", watching)
        assert RB._main(["--workspace", str(ws), "--check"]) == 0
        passed = seen["contents"] or {}
        assert set(passed) == {"result.json", "parse_ledger.json",
                               "taxpayer_profile.json", "tax_law_2026.json"}
        assert passed["result.json"] == (ws / "result.json").read_bytes()

    def test_a_derived_line_edited_after_the_check_invalidates_it(self, tmp_path):
        """The failure mode the receipt closes: pass Step 12.6, then edit line
        24, then assemble a filing package on a check that never saw it."""
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"wages": 30000.0}])
        RB._main(["--workspace", str(ws)])
        assert RB._main(["--workspace", str(ws), "--check"]) == 0
        assert RC.verify(ws, required=(RB.GATE,)).valid
        result = json.loads((ws / "result.json").read_text(encoding="utf-8"))
        result["form_1040"]["line_24_total_tax"] = 99.0
        (ws / "result.json").write_text(json.dumps(result), encoding="utf-8")
        report = RC.verify(ws, required=(RB.GATE,))
        assert not report.valid
        assert any("result.json changed" in r for r in report.reasons)

    def test_a_return_this_module_does_not_govern_passes_the_check(
            self, tmp_path, capsys):
        """A computed PFIC fund keeps lines 1-38 with the agent, so there is no
        verdict here to fail. Refusing would put this gate between a correct
        return and its filing package for ever, with `--draft` — NOT VALIDATED
        stamped across a return that is fine — as the only way past."""
        ws = _workspace(tmp_path, records=[{"ordinary_dividends": 4000.0}],
                        extra={"form_8621_pfic": {"funds": [{"fund_id": "f1"}]}})
        assert RB._main(["--workspace", str(ws), "--check"]) == 0
        assert self._entry(ws)["outcome"] == RC.NOT_APPLICABLE
        assert RC.verify(ws, required=(RB.GATE,)).valid
        # On STDOUT: the run succeeded, and a reader that prints stderr only on
        # a non-zero exit would otherwise see an empty stdout and read it as a
        # clean check.
        out = capsys.readouterr()
        assert "NOT CHECKED" in out.out and "NOT CHECKED" not in out.err

    def test_a_return_that_is_out_of_scope_AND_wrong_is_still_blocked(self, tmp_path):
        """Scope is decided LAST, and this is why. Decided first, one field read
        before the return had been looked at at all waived every other refusal:
        a negative line 23 on a PFIC return came back NOT CHECKED, was recorded
        as a pass, and reached a filing package as a refund nobody is owed."""
        ws = _workspace(tmp_path, {"line_23_other_taxes": -4000.0},
                        records=[{"ordinary_dividends": 4000.0}],
                        extra={"form_8621_pfic": {"funds": [{"fund_id": "f1"}]}})
        assert RB._main(["--workspace", str(ws), "--check"]) == 2
        assert self._entry(ws)["outcome"] == "BLOCKED"

    def test_the_same_return_still_refuses_to_be_BUILT(self, tmp_path):
        """The point of the refusal is that this module must not WRITE those
        lines here — §1291 moves amounts out of 3a and 3b after the broker
        reported them, and rebuilding from the ledger restores the qualified
        treatment it forbids."""
        ws = _workspace(tmp_path, records=[{"ordinary_dividends": 4000.0}],
                        extra={"form_8621_pfic": {"funds": [{"fund_id": "f1"}]}})
        assert RB._main(["--workspace", str(ws)]) == 2
        assert not (ws / RC.RECEIPT_NAME).exists()

    def test_a_cache_that_cannot_price_the_return_outranks_the_waiver(self, tmp_path):
        """The waiver is granted after `_context`, so a return this module does
        not govern AND cannot price is BLOCKED. Asked earlier it came back NOT
        CHECKED — a cache with no brackets certified, which is the one thing
        AGENTS.md §2 says nothing downstream catches."""
        law = _law()
        law["federal"]["filing_statuses"] = {}
        ws = _workspace(tmp_path, records=[{"ordinary_dividends": 4000.0}], law=law,
                        extra={"form_8621_pfic": {"funds": [{"fund_id": "f1"}]}})
        assert RB._main(["--workspace", str(ws), "--check"]) == 2
        assert self._entry(ws)["outcome"] == "BLOCKED"

    def test_an_unfetched_cache_is_not_an_out_of_scope_pass(self, tmp_path):
        """The separation `Unpriceable` exists for. A cache with no capital-gains
        breakpoints is a return nobody can compute yet (AGENTS.md §2) — folded
        in with the returns this module declines to govern, it would have ridden
        into a filing package under that pass."""
        law = _law()
        law["federal"]["capital_gains_rates"] = {}
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"qualified_dividends": 5000.0,
                                  "ordinary_dividends": 5000.0}], law=law)
        assert RB._main(["--workspace", str(ws), "--check"]) == 2
        assert self._entry(ws)["outcome"] == "BLOCKED"
        assert not RC.verify(ws, required=(RB.GATE,)).valid

    def test_a_document_arriving_afterwards_does_not_fire_this_gate(self, tmp_path):
        """The derived lines are a function of the four files this gate reads.
        An unparsed document cannot have moved one, so invalidating here would
        fire on correct work — the tie-out's own entry is what covers the
        documents, and a filing package needs both."""
        ws = _workspace(tmp_path, {"line_12_standard_deduction": 0},
                        records=[{"wages": 30000.0}])
        RB._main(["--workspace", str(ws)])
        RB._main(["--workspace", str(ws), "--check"])
        (ws / "documents" / "late_1099.pdf").write_bytes(b"%PDF-1.4 thursday\n")
        assert RC.verify(ws, required=(RB.GATE,)).valid
        RC.record(ws, "tie_out", RC.PASS)   # ...and the gate that DOES cover them
        (ws / "documents" / "later_1099.pdf").write_bytes(b"%PDF-1.4 friday\n")
        assert not RC.verify(ws, required=("tie_out",)).valid


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
            "line_22_tax_minus_credits", "line_24_total_tax",
            "line_25a_w2_withholding", "line_25b_1099_withholding",
            "line_25d_total_withholding", "line_33_total_payments",
            "line_34_overpayment", "line_35a_refund", "line_37_amount_owed")

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
        assert "-m agents.lib.return_builder" in text
        section = text.split("return_builder")[1]
        for code in ("Exit 0", "Exit 2", "Exit 1"):
            assert code in section, code

    def test_the_skill_says_only_the_check_writes_the_receipt(self):
        """The build mode rewrites `result.json` and certifies nothing, so an
        agent that runs the two in the other order has a receipt bound to a
        file that changed underneath it — and /tax-generate refuses. The order
        is a rule the skill has to state, not one the exit codes imply."""
        text = self._skill("tax-compute")
        section = text.split("Step 12.6")[1].split("Step 12.7")[0]
        assert "receipt" in section
        assert "--filing-package" in section

    def test_tax_generate_names_all_three_gates_behind_the_refusal(self):
        """The refusal names which gate is stale; a skill that lists two of the
        three sends the agent to re-run the wrong step."""
        text = self._skill("tax-generate")
        for step in ("Step 0.7", "Step 12.6", "Step 12.7"):
            assert step in text, step

    def test_the_skill_does_not_ask_the_agent_to_type_a_derived_line(self):
        """The reference file's line-9 formula is the instruction this module
        replaces. It may still SHOW the form's arithmetic — a reader needs to
        know what line 9 is — but it must name the builder as what does it."""
        text = (REPO / ".agents" / "skills" / "tax-compute" / "references"
                / "income-and-agi.md").read_text(encoding="utf-8")
        assert "return_builder" in text


class TestLastYearsLine36IsThisYearsLine26:
    """Form 1040 line 26 is "estimated tax payments AND amount applied from the
    prior-year return" — one line carrying two things, and the second has no
    document behind it."""

    def _profile(self, applied):
        return {"filing_status": "MFJ", "tax_year": 2026,
                "prior_year": {"estimated_payments_applied": applied}}

    def _result(self, line_26):
        return {"form_1040": {"line_26_estimated_payments": line_26}}

    def test_a_line_26_below_the_applied_overpayment_is_REFUSED(self):
        """The direction that matters: omitting it under-states line 26, which
        under-states line 33's payments, which OVER-states line 37 — the
        taxpayer pays money they already paid."""
        with pytest.raises(RB.Blocked) as e:
            RB._check_the_prior_overpayment(self._result(0.0),
                                            self._profile(3000.0))
        assert "3,000.00" in str(e.value)
        assert "already paid" in str(e.value)

    def test_it_is_an_INEQUALITY_not_an_equality(self):
        """Line 26 is the applied overpayment PLUS any quarterly estimates
        paid during the year, so it can exceed this figure by any amount — an
        equality would refuse every taxpayer who also paid estimates."""
        RB._check_the_prior_overpayment(self._result(12000.0),
                                        self._profile(3000.0))

    def test_exactly_equal_passes(self):
        RB._check_the_prior_overpayment(self._result(3000.0),
                                        self._profile(3000.0))

    def test_a_negative_applied_figure_is_REFUSED(self):
        """An overpayment applied forward is money already paid; a negative one
        reaches line 26 as a payment being taken away."""
        with pytest.raises(RB.Blocked, match="already paid"):
            RB._check_the_prior_overpayment(self._result(0.0),
                                            self._profile(-500.0))

    def test_no_prior_overpayment_checks_nothing(self):
        """A gate firing on a taxpayer who applied nothing forward is a gate
        firing on correct data."""
        RB._check_the_prior_overpayment(self._result(0.0), self._profile(0))
        RB._check_the_prior_overpayment(self._result(0.0),
                                        {"filing_status": "MFJ"})

    def test_the_check_runs_inside_the_ordinary_check(self):
        """It is part of "everything wrong with the return", not a separate
        call a caller can forget.

        Built from the shipped template rather than the two-field stub the
        other cases use: `check` runs the component sweep first, and a sparse
        return is refused for the missing lines before it reaches this."""
        template = json.loads(
            (REPO / "agents/templates/result_template.json")
            .read_text(encoding="utf-8"))
        result = {k: v for k, v in template.items()
                  if k in ("form_1040", "schedule_d", "form_6251")}
        result["form_1040"] = {k: v for k, v in result["form_1040"].items()
                               if not k.startswith("_")}
        RB.check(result, self._profile(0))          # the fixture is otherwise sound
        with pytest.raises(RB.Blocked, match="already paid"):
            RB.check(result, self._profile(3000.0))

    def test_a_caller_that_passes_no_profile_does_not_silently_pass_it(self):
        """The figure lives in `taxpayer_profile.json` and nowhere on the
        return, so a caller without one cannot run this check at all. What it
        must not do is look like it ran."""
        import inspect
        source = inspect.getsource(RB._check_the_prior_overpayment)
        assert "profile is None" in source
        doc = inspect.getdoc(RB._check_the_prior_overpayment)
        assert "cannot run the check at all" in doc

    def test_build_passes_the_profile_through(self):
        import inspect
        assert "profile" in inspect.signature(RB.build).parameters
        assert "check(result, profile)" in inspect.getsource(RB.build)


class TestTheRolloverFillsTheFieldNothingUsedToWrite:
    """`prior_year.estimated_payments_applied` sat in the profile template
    written by nothing and read by nothing — dead scaffolding, the D1 lesson
    this repository already learned once."""

    def test_the_field_map_carries_line_36(self):
        from agents.lib.rollover import FIELD_MAP
        entry = FIELD_MAP["prior_overpayment_applied"]
        assert entry["source_path"] == "form_1040.line_36_applied_to_next_year"
        assert entry["target_path"] == "prior_year.estimated_payments_applied"
        assert entry["category"] == "carryforwards"

    def test_the_target_exists_in_the_profile_template(self):
        """A carryforward whose target is not a real field writes into a
        document nothing reads."""
        template = json.loads(
            (REPO / "agents/templates/taxpayer_profile_template.json")
            .read_text(encoding="utf-8"))
        assert "estimated_payments_applied" in template["prior_year"]

    def test_the_source_exists_in_the_result_template(self):
        template = json.loads(
            (REPO / "agents/templates/result_template.json")
            .read_text(encoding="utf-8"))
        assert "line_36_applied_to_next_year" in template["form_1040"]

    def test_both_ends_are_documented_as_a_pair(self):
        """The note used to say the carry was manual. A stale caveat is worse
        than none: it tells a reader to do by hand what the engine now does,
        and a figure entered twice is a payment counted twice."""
        template = json.loads(
            (REPO / "agents/templates/result_template.json")
            .read_text(encoding="utf-8"))
        note = template["form_1040"]["_note_line_36"]
        assert "still manual" not in note
        assert "CARRIES it" in note
        from tests.skill_text import skill_text
        body = skill_text("tax-compute")
        assert "prior_year.estimated_payments_applied" in body
