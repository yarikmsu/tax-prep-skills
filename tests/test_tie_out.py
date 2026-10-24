"""Tests for the parse-to-return tie-out (agents/lib/tie_out.py, Epic A).

The invariant under test: a parsed document that never landed on a return
line is DETERMINISTICALLY caught, not hopefully noticed.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import tie_out as T  # noqa: E402
from tests.skill_text import listed_boxes, skill_text  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# Quarterly §6621 underpayment rates spanning the PFIC recompute test horizon.
_PFIC_6621 = [{"effective_from": f"{y}-{m:02d}-01", "annual_rate": 0.08}
              for y in range(2014, 2027) for m in (1, 4, 7, 10)]
_HIST_RATE_ALL = {y: 0.37 for y in range(2014, 2026)}


def _record(doc_id="w2-acme", file="documents/W2_Acme.pdf", form_type="W-2",
            payer="Acme", person="primary", key_boxes=None, skipped=None,
            foreign=None):
    rec = {"doc_id": doc_id, "file": file, "form_type": form_type,
           "payer": payer, "person": person,
           "key_boxes": key_boxes if key_boxes is not None
           else {"wages": 100000.0, "federal_withholding_w2": 12000.0},
           "skipped": skipped}
    if foreign is not None:
        rec["foreign"] = foreign
    return rec


def _ledger(*records):
    return T.load_ledger({"records": list(records)})


def _result(wages=100000.0, w2_wh=12000.0, **extra_1040):
    # line 1a = W-2 box 1 (what the wages category ties to); line 1z = 1a–1h
    # sum. Equal here unless a test sets line_1h explicitly via extra_1040.
    f = {"line_1a_wages": wages, "line_1z_total_wages": wages,
         "line_25a_w2_withholding": w2_wh}
    f.update(extra_1040)
    return {"form_1040": f}


# --------------------------------------------------------------------------
# ledger validation
# --------------------------------------------------------------------------

def test_valid_ledger_loads():
    recs = _ledger(_record())
    assert len(recs) == 1
    assert dict(recs[0].key_boxes)["wages"] == 100000.0


def test_unknown_category_hard_errors():
    """A category typo would silently drop the amount from the sums."""
    with pytest.raises(T.TieOutError, match="unknown category"):
        _ledger(_record(key_boxes={"wagess": 100000.0}))


def test_duplicate_doc_id_rejected():
    with pytest.raises(T.TieOutError, match="duplicate doc_id"):
        _ledger(_record(), _record(file="documents/other.pdf"))


def test_duplicate_file_rejected():
    """Codex P2 round 4: two records for one physical document double-count
    its amounts while still passing coverage — hard error."""
    with pytest.raises(T.TieOutError, match="duplicate file"):
        _ledger(_record(), _record(doc_id="w2-acme-again"))


def test_non_skipped_record_needs_key_boxes():
    """An empty parse is either an explicit skip or a bug — never a silent
    empty record that trivially passes the sums."""
    with pytest.raises(T.TieOutError, match="must carry key_boxes"):
        _ledger(_record(key_boxes={}))


def test_skipped_record_needs_reason():
    with pytest.raises(T.TieOutError, match="non-empty reason"):
        _ledger(_record(key_boxes={}, skipped="  "))


def test_skipped_record_with_reason_ok():
    recs = _ledger(_record(key_boxes={}, skipped="duplicate of W2_Acme_copy.pdf"))
    assert recs[0].skipped.startswith("duplicate")


def test_skipped_record_with_key_boxes_rejected():
    """Codex P2 round 3: a skipped record carrying amounts earns coverage
    credit while its dollars vanish from the sums — the exact intake-drop the
    gate exists to catch. Hard error."""
    with pytest.raises(T.TieOutError, match="SKIPPED record must not carry"):
        _ledger(_record(key_boxes={"wages": 50000.0}, skipped="oops"))


def test_negative_amount_rejected_for_unsigned_category():
    with pytest.raises(T.TieOutError, match="cannot be negative"):
        _ledger(_record(key_boxes={"wages": -5.0}))


def test_nan_amount_rejected():
    with pytest.raises(T.TieOutError, match="finite"):
        _ledger(_record(key_boxes={"wages": float("nan")}))


def test_bool_amount_rejected():
    with pytest.raises(T.TieOutError, match="must be a number"):
        _ledger(_record(key_boxes={"wages": True}))


def test_bad_person_rejected():
    with pytest.raises(T.TieOutError, match="primary/spouse/joint"):
        _ledger(_record(person="taxpayer"))


def test_wages_tie_to_line_1a_not_1z_with_line_1h_income():
    """Codex P1 (PR #47): the W-2 ledger total ties to line 1a, not line 1z.
    A return with $100K W-2 wages plus $350 of line-1h income (a J2 ESPP
    disqualifying-disposition ordinary component the former employer omitted)
    has line_1z = 100350 but line_1a = 100000 — it must still PASS."""
    recs = _ledger(_record(key_boxes={"wages": 100000.0}))
    result = _result(wages=100000.0, line_1z_total_wages=100350.0,
                     line_1h_other_earned_income=350.0)
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"])
    wage_check = next(c for c in report.sum_checks if c.category == "wages")
    assert wage_check.result_total == 100000.0 and wage_check.ok


def test_malformed_payload_rejected():
    with pytest.raises(T.TieOutError, match="'records' list"):
        T.load_ledger({"docs": []})


# --------------------------------------------------------------------------
# foreign marker → FBAR set-difference (G2)
# --------------------------------------------------------------------------

def test_foreign_payers_collected_including_skips():
    """A skipped duplicate scan of a foreign bank still evidences the
    account — /tax-fbar's set-difference must see it."""
    recs = _ledger(
        _record(),  # domestic W-2, no foreign field
        _record(doc_id="fb", file="documents/ForeignBank.pdf",
                form_type="1099-INT-equivalent", payer="Deutsche Bank",
                key_boxes={"taxable_interest": 300.0}, foreign=True),
        _record(doc_id="fb2", file="documents/ForeignBank_copy.pdf",
                form_type="1099-INT-equivalent", payer="Deutsche Bank",
                key_boxes={}, skipped="duplicate scan", foreign=True))
    assert T.foreign_payers(recs) == ["Deutsche Bank"]
    assert recs[0].foreign is False


def test_non_bool_foreign_rejected():
    with pytest.raises(T.TieOutError, match="foreign must be a boolean"):
        _ledger(_record(foreign="yes"))


# --------------------------------------------------------------------------
# A3 — second-read verification for high-value records
# --------------------------------------------------------------------------

def test_high_value_record_without_second_read_advisory():
    """Any key box ≥ $10K without a second_read outcome draws an advisory —
    visible, never a silent skip, never a hard failure."""
    recs = _ledger(_record())  # wages 100k, no second_read
    report = T.evaluate(recs, _result(), ["documents/W2_Acme.pdf"])
    assert not report.failed
    assert any("second-read" in a and "w2-acme" in a for a in report.advisories)


def test_schedule_1a_tips_overtime_draw_advisory():
    """A parsed tips/overtime figure feeds the Schedule 1-A deduction (line
    13b) — it must draw a to-verify advisory so a parsed statement with line
    13b left at $0 (a dropped deduction) can't silently report 'Tied out'."""
    recs = _ledger(_record(
        doc_id="w2-diner", file="documents/W2_Diner.pdf",
        key_boxes={"wages": 40000.0, "qualified_tips": 8000.0,
                   "qualified_overtime_premium": 3000.0}))
    report = T.evaluate(recs, _result(wages=40000.0, w2_wh=0.0),
                        ["documents/W2_Diner.pdf"])
    assert not report.failed  # informational — advisory, not a gate failure
    assert any("qualified_tips" in a and "line 13b" in a
               for a in report.advisories)
    assert any("qualified_overtime_premium" in a and "line 13b" in a
               for a in report.advisories)


def test_second_read_match_clears_advisory():
    rec = _record()
    rec["second_read"] = "match"
    report = T.evaluate(T.load_ledger({"records": [rec]}), _result(),
                        ["documents/W2_Acme.pdf"])
    assert not any("second-read" in a for a in report.advisories)


def test_low_value_record_needs_no_second_read():
    recs = _ledger(_record(key_boxes={"taxable_interest": 500.0}))
    report = T.evaluate(recs, {"form_1040": {"line_2b_taxable_interest": 500.0}},
                        ["documents/W2_Acme.pdf"])
    assert not any("second-read" in a for a in report.advisories)


def test_invalid_second_read_value_rejected():
    """An unrecognized value must not silently count as verified."""
    rec = _record()
    rec["second_read"] = "verified"
    with pytest.raises(T.TieOutError, match="second_read"):
        T.load_ledger({"records": [rec]})


# --------------------------------------------------------------------------
# A4 — expected_sources consumption (missing payer diff)
# --------------------------------------------------------------------------

def test_missing_expected_payer_surfaces():
    recs = _ledger(_record())  # payer "Acme"
    missing = T.missing_expected_payers(recs, {
        "_comment": "skipped",
        "w2_employers": ["Acme Corp"],
        "interest_payers": ["Chase Bank"],
        "brokerages": []})
    assert missing == ["Chase Bank"]  # Acme matched by normalized substring


def test_expected_payer_token_subset_matching_both_directions():
    recs = _ledger(_record(payer="Fidelity Brokerage Services LLC"))
    assert T.missing_expected_payers(
        recs, {"brokerages": ["Fidelity"]}) == []
    short = _ledger(_record(payer="Fidelity"))
    assert T.missing_expected_payers(
        short, {"brokerages": ["Fidelity Brokerage Services LLC"]}) == []


def test_expected_payer_no_mid_word_false_match():
    """A raw substring test would match 'Ally' inside 'Tally Corp' and hide
    the missing payer — token matching must flag it."""
    recs = _ledger(_record(payer="Tally Corp"))
    assert T.missing_expected_payers(
        recs, {"interest_payers": ["Ally"]}) == ["Ally"]


def test_expected_sources_accepts_rollover_object_shapes():
    """P1 caught in review: /tax-rollover writes OBJECT entries, not strings
    — {'name','ein'} employers, {'name','country_code'} payers,
    {'institution',...} foreign accounts. A strings-only validator would
    hard-error on every rolled-over profile."""
    recs = _ledger(_record())  # payer "Acme"
    missing = T.missing_expected_payers(recs, {
        "w2_employers": [{"name": "Acme Corp", "ein": "12-3456789"}],
        "interest_payers": [{"name": "Monzo Bank", "country_code": "GB"}],
        "foreign_accounts": [{"institution": "Deutsche Bank",
                              "country_code": "DE", "account_last4": "1234"}]})
    assert missing == ["Deutsche Bank", "Monzo Bank"]


def test_expected_sources_validation():
    recs = _ledger(_record())
    with pytest.raises(T.TieOutError, match="must be a list"):
        T.missing_expected_payers(recs, {"w2_employers": "Acme"})
    with pytest.raises(T.TieOutError, match="non-empty"):
        T.missing_expected_payers(recs, {"w2_employers": [""]})
    with pytest.raises(T.TieOutError, match="name.*institution"):
        T.missing_expected_payers(recs, {"w2_employers": [{"ein": "12-3456789"}]})
    with pytest.raises(T.TieOutError, match="strings or objects"):
        T.missing_expected_payers(recs, {"w2_employers": [42]})
    with pytest.raises(T.TieOutError, match="must be an object"):
        T.missing_expected_payers(recs, None)
    assert T.missing_expected_payers(recs, {}) == []


# --------------------------------------------------------------------------
# Check A — coverage
# --------------------------------------------------------------------------

def test_document_without_record_fails():
    """THE CP2000 case: a file in documents/ with no ledger record."""
    recs = _ledger(_record())
    report = T.evaluate(recs, _result(),
                        ["documents/W2_Acme.pdf", "documents/1099_Fidelity.pdf"])
    assert report.failed
    assert report.missing_records == ("documents/1099_Fidelity.pdf",)


def test_phantom_ledger_file_fails():
    recs = _ledger(_record(file="documents/GONE.pdf"))
    report = T.evaluate(recs, _result(), [])
    assert report.failed
    assert report.phantom_files == ("documents/GONE.pdf",)


def test_full_coverage_passes():
    recs = _ledger(_record())
    report = T.evaluate(recs, _result(), ["documents/W2_Acme.pdf"])
    assert not report.failed


def test_skipped_record_counts_as_coverage():
    recs = _ledger(_record(),
                   _record(doc_id="dup", file="documents/dup.pdf",
                           key_boxes={}, skipped="duplicate scan"))
    report = T.evaluate(recs, _result(),
                        ["documents/W2_Acme.pdf", "documents/dup.pdf"])
    assert not report.failed
    assert report.skipped == 1


def test_pending_skip_fails_the_gate():
    """Codex P2 round 9: a 'pending /tax-parse-crypto' skip satisfies coverage
    while the CSV's taxable disposals enter no sum — it must FAIL, not pass
    like a duplicate/non-tax skip."""
    recs = _ledger(_record(),
                   _record(doc_id="cb", file="documents/coinbase_2025.csv",
                           form_type="crypto-csv", payer="Coinbase",
                           key_boxes={}, skipped="pending /tax-parse-crypto"))
    report = T.evaluate(recs, _result(),
                        ["documents/W2_Acme.pdf", "documents/coinbase_2025.csv"])
    assert report.failed
    assert report.pending_skips == (
        "documents/coinbase_2025.csv — pending /tax-parse-crypto",)
    assert "PENDING another skill" in T.render_markdown(report)


def test_pending_skip_detection_is_case_insensitive():
    recs = _ledger(_record(key_boxes={}, skipped="  Pending /tax-parse-crypto"))
    report = T.evaluate(recs, _result(wages=0), ["documents/W2_Acme.pdf"])
    assert report.failed and len(report.pending_skips) == 1


def test_resolved_crypto_skip_passes():
    """/tax-parse-crypto's upsert replaces the pending placeholder with a
    documented skip pointing at its FIFO reconciliation — that passes."""
    recs = _ledger(_record(),
                   _record(doc_id="cb", file="documents/coinbase_2025.csv",
                           form_type="crypto-csv", payer="Coinbase",
                           key_boxes={},
                           skipped="crypto exchange CSV — ingested by "
                                   "/tax-parse-crypto; amounts flow to Form "
                                   "8949/Schedule D via crypto_summary"))
    report = T.evaluate(recs, _result(),
                        ["documents/W2_Acme.pdf", "documents/coinbase_2025.csv"])
    assert not report.failed
    assert report.pending_skips == ()


# --------------------------------------------------------------------------
# Check C — sums
# --------------------------------------------------------------------------

def test_sum_mismatch_fails_with_both_figures():
    """Ledger says $112K of wages landed; result carries $100K — the dropped
    $12K W-2 fails deterministically."""
    recs = _ledger(_record(),
                   _record(doc_id="w2-beta", file="documents/W2_Beta.pdf",
                           payer="Beta", key_boxes={"wages": 12000.0}))
    report = T.evaluate(recs, _result(wages=100000.0),
                        ["documents/W2_Acme.pdf", "documents/W2_Beta.pdf"])
    assert report.failed
    check = next(c for c in report.sum_checks if c.category == "wages")
    assert check.ledger_total == 112000.0
    assert check.result_total == 100000.0
    assert not check.ok


def test_sum_within_tolerance_passes():
    recs = _ledger(_record(key_boxes={"wages": 100000.6}))
    report = T.evaluate(recs, _result(wages=100000.0), ["documents/W2_Acme.pdf"])
    assert not report.failed


def test_multi_category_sums():
    recs = _ledger(
        _record(),
        _record(doc_id="int-bank", file="documents/1099INT_Bank.pdf",
                form_type="1099-INT", payer="Bank",
                key_boxes={"taxable_interest": 500.0, "federal_withholding_1099": 50.0}))
    result = _result(line_2b_taxable_interest=500.0,
                     line_25b_1099_withholding=50.0)
    report = T.evaluate(recs, result,
                        ["documents/W2_Acme.pdf", "documents/1099INT_Bank.pdf"])
    assert not report.failed
    assert {c.category for c in report.sum_checks} == {
        "wages", "federal_withholding_w2", "taxable_interest",
        "federal_withholding_1099"}


def test_retirement_gross_sums_4a_plus_5a():
    recs = _ledger(_record(doc_id="r", file="documents/1099R.pdf",
                           form_type="1099-R", payer="Broker",
                           key_boxes={"retirement_gross": 30000.0}))
    result = {"form_1040": {"line_4a_ira_distributions": 10000.0,
                            "line_5a_pensions": 20000.0}}
    report = T.evaluate(recs, result, ["documents/1099R.pdf"])
    assert not report.failed


def test_informational_categories_not_tied():
    recs = _ledger(_record(key_boxes={"wages": 100000.0,
                                      "federal_withholding_w2": 12000.0,
                                      "ss_wages": 100000.0,
                                      "medicare_wages": 100000.0}))
    report = T.evaluate(recs, _result(), ["documents/W2_Acme.pdf"])
    assert not report.failed
    assert not any(c.category in ("ss_wages", "medicare_wages")
                   for c in report.sum_checks)


def test_skipped_records_excluded_from_sums():
    recs = _ledger(_record(),
                   _record(doc_id="dup", file="documents/dup.pdf",
                           key_boxes={}, skipped="duplicate scan"))
    report = T.evaluate(recs, _result(),
                        ["documents/W2_Acme.pdf", "documents/dup.pdf"])
    check = next(c for c in report.sum_checks if c.category == "wages")
    assert check.ledger_total == 100000.0


def test_missing_mapped_field_in_present_section_fails():
    """Codex P1 round 7: form_1040 present but line_2b_taxable_interest
    absent must read as $0 and MISMATCH the ledger — not demote the dropped
    1099-INT to an unverifiable warning (exit 0)."""
    recs = _ledger(_record(doc_id="int", file="documents/1099INT.pdf",
                           form_type="1099-INT", payer="Bank",
                           key_boxes={"taxable_interest": 500.0}))
    report = T.evaluate(recs, _result(), ["documents/1099INT.pdf"])
    assert report.failed
    assert report.unverifiable == ()
    check = next(c for c in report.sum_checks if c.category == "taxable_interest")
    assert check.result_total == 0.0 and not check.ok


def test_missing_dotted_path_field_fails():
    """Same class, dotted path: schedule_d present but part_ii_long_term
    absent → cap-gain distributions compare against $0 and fail."""
    recs = _ledger(_record(doc_id="d", file="documents/1099DIV.pdf",
                           form_type="1099-DIV", payer="Fund",
                           key_boxes={"capital_gain_distributions": 3000.0}))
    report = T.evaluate(recs, {"schedule_d": {"line_16_combined": 0}},
                        ["documents/1099DIV.pdf"])
    assert report.failed
    check = next(c for c in report.sum_checks
                 if c.category == "capital_gain_distributions")
    assert check.result_total == 0.0 and not check.ok


def test_state_withholding_ties_to_ca_540():
    recs = _ledger(_record(key_boxes={"wages": 100000.0,
                                      "federal_withholding_w2": 12000.0,
                                      "state_withholding_ca": 6000.0}))
    result = _result()
    result["california_540"] = {"ca_withholding_box17": 6000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"])
    assert not report.failed


class TestNewYorkWithholding:
    """TWO categories, not one. New York City withholding is IT-201 line 73
    and the state's is line 72 — two lines and two refunds — so a city amount
    dropped into the state category would still tie while leaving the city
    withholding unclaimed on a return that owes city tax."""

    def _ny(self, state=0.0, city=0.0):
        return {"new_york_it201": {"refundable_credits_and_payments": {
            "line_72_state_withholding": state,
            "line_73_city_withholding": city}}}

    WAGES = 180000.0
    FED_WH = 31000.0

    def _base(self):
        return _result(wages=self.WAGES, w2_wh=self.FED_WH)

    def _recs(self, **boxes):
        return _ledger(_record(key_boxes={"wages": self.WAGES,
                                          "federal_withholding_w2": self.FED_WH,
                                          **boxes}))

    def test_both_lines_tie(self):
        report = T.evaluate(
            self._recs(state_withholding_ny=11200.0,
                       city_withholding_nyc=6400.0),
            {**self._base(), **self._ny(state=11200.0, city=6400.0)},
            ["documents/W2_Acme.pdf"])
        assert not report.failed

    def test_city_withholding_folded_into_the_state_line_FAILS(self):
        """The defect two categories exist to catch: one figure of 17,600 on
        line 72 ties against nothing, and line 73's 6,400 is lost."""
        report = T.evaluate(
            self._recs(state_withholding_ny=11200.0,
                       city_withholding_nyc=6400.0),
            {**self._base(), **self._ny(state=17600.0, city=0.0)},
            ["documents/W2_Acme.pdf"])
        assert report.failed
        failed = {c.category for c in report.sum_checks if not c.ok}
        assert failed == {"state_withholding_ny", "city_withholding_nyc"}

    def test_a_dropped_city_amount_fails_rather_than_warning(self):
        """The section is present, so the comparison is against a real $0 —
        never demoted to an unverifiable warning."""
        report = T.evaluate(
            self._recs(city_withholding_nyc=6400.0),
            {**self._base(), **self._ny()}, ["documents/W2_Acme.pdf"])
        assert report.failed
        check = next(c for c in report.sum_checks
                     if c.category == "city_withholding_nyc")
        assert check.result_total == 0.0 and not check.ok

    def test_the_categories_are_per_state_like_californias(self):
        """A category spanning states cannot fail when one state's amount
        lands in another state's section — the whole failure this watches
        for. `state_withholding_ca` is the precedent and the shape matches."""
        assert "state_withholding_ny" in T.CATEGORY_TARGETS
        assert "city_withholding_nyc" in T.CATEGORY_TARGETS
        assert "state_withholding" not in T.CATEGORY_TARGETS
        assert T.CATEGORY_TARGETS["state_withholding_ny"][0] == "new_york_it201"
        assert T.CATEGORY_TARGETS["city_withholding_nyc"][0] == "new_york_it201"

    def test_both_halves_shipped_or_the_gate_is_vacuous(self):
        """A registered-but-never-emitted category is dead protection: the
        ledger never carries it, every sum check passes on an empty set, and
        the gate reports clean. Both halves or neither."""
        parse_md = skill_text("tax-parse")
        for category in ("state_withholding_ny", "city_withholding_nyc"):
            assert category in parse_md, category
        assert "Yonkers" in parse_md, (
            "Yonkers withholding is IT-201 line 74 and has no category; the "
            "parse table must SAY so rather than leaving a reader to assume "
            "a zero")

    def test_every_target_path_is_a_real_template_field(self):
        """A path that resolves to nothing compares against $0 for ever — a
        check that can only fail, or only pass, for the wrong reason."""
        template = json.loads(
            (REPO / "agents/templates/result_template.json").read_text())
        for category in ("state_withholding_ny", "city_withholding_nyc"):
            section_name, fields = T.CATEGORY_TARGETS[category]
            node = template[section_name]
            for field in fields:
                for seg in field.split("."):
                    assert seg in node, f"{category}: {field}"
                    node = node[seg]


class TestUnroutedStateWithholding:
    """AA59: a 1099 state amount for CA or NY, whose returns this toolkit
    computes but whose 1099 withholding it does not route yet, blocks the
    gate. Dropped instead, it is a payment the return never claims, which
    over-states the balance due."""

    FILES = ["documents/W2_Acme.pdf", "documents/1099DA.pdf", "documents/1099R.pdf"]

    def _recs(self, amount, category="unrouted_state_withholding", other=None):
        records = [_record(), _record(
            doc_id="da-exchange", file="documents/1099DA.pdf", form_type="1099-DA",
            payer="Exchange", key_boxes={category: amount})]
        if other is not None:
            records.append(_record(doc_id="r-pension", file="documents/1099R.pdf", form_type="1099-R",
                                   payer="Pension", key_boxes={category: other}))
        if category == "other_state_withholding":
            for record in records[1:]:
                record["other_state_withholding_by_state"] = {"CA": record["key_boxes"][category]}
        return _ledger(*records)

    def _target(self, report, category="unrouted_state_withholding"):
        (target,) = [t for t in report.missing_result_targets if t.startswith(category)]
        return target

    def test_an_amount_fails_the_gate_names_its_form_and_says_what_to_do(self):
        report = T.evaluate(self._recs(125.0), _result(), self.FILES[:2])
        assert report.failed
        target = self._target(report)
        assert "da-exchange" in target and "Nothing waives this" in target
        # Preparing only the state return elsewhere clears nothing here.
        assert "still prevents completion, federal included" in target
        # Each way round the block that loses the payment is named, the
        # advisory category among them: nothing checks which state it holds.
        assert "re-read the source and use the supported" in target
        assert "Do not delete the record or hide it" in target
        assert "state income tax withheld on a 1099 or W-2G" in target
        assert "New York City or Yonkers tax withheld" in target

    def test_only_the_forms_carrying_an_amount_are_named(self):
        report = T.evaluate(self._recs(125.0, other=0.0), _result(), self.FILES)
        assert "r-pension" not in self._target(report)

    def test_it_is_never_tied_or_demoted_to_a_warning(self):
        """No result field can hold it, so a sum check could only compare it
        against a section that does not exist."""
        report = T.evaluate(self._recs(125.0), _result(), self.FILES[:2])
        assert all(c.category != "unrouted_state_withholding" for c in report.sum_checks)
        assert "unrouted_state_withholding" not in report.unverifiable

    def test_a_zero_amount_blocks_nothing(self):
        # The 1099-DA owes the option answer whatever its boxes hold (AA59).
        assert not T.evaluate(self._recs(0.0), _result(), self.FILES[:2],
                              option_exercise_answer=False).failed

    def test_first_sale_nft_proceeds_block_too(self):
        """Box 11c's proceeds are kept out of 1f, so `capital_proceeds` never
        sees them, and a form that never reaches broker_reporting would
        lose them."""
        report = T.evaluate(self._recs(900.0, "nft_first_sale_proceeds"), _result(), self.FILES[:2])
        target = self._target(report, "nft_first_sale_proceeds")
        assert report.failed and "da-exchange" in target and "Nothing waives this" in target

    def test_another_states_withholding_is_an_advisory_not_a_block(self):
        """A state whose return this run does not compute is claimed on a
        return filed elsewhere: blocking the federal return over it is a block
        with no way through, and dropping it silently loses the reminder."""
        report = T.evaluate(self._recs(300.0, "other_state_withholding"), _result(), self.FILES[:2],
                            option_exercise_answer=False)
        assert not report.failed
        (advisory,) = [a for a in report.advisories if a.startswith("other_state_withholding")]
        # The amount is on this return too, for an itemizer, once.
        assert "Schedule A line 5a counts it once, from its own form" in advisory
        # By category AND computed return: a Texas resident's CA 1099 belongs
        # here, as does a generic-estimate state's W-2 box 17.
        assert ("An amount for a state with its own withholding category whose return "
                "this run computes never belongs here") in advisory

    def test_a_1099_routes_only_to_a_state_return_this_run_computes(self):
        """A Texas resident's CA-withheld 1099 has no CA return here: blocking
        their federal return over it would name a return that does not exist."""
        parse_md = skill_text("tax-parse")
        for category in ("ca_1099_withholding", "state_withholding_ny", "state_withholding_nc"):
            (row,) = [line for line in parse_md.splitlines()
                      if "1099's state-tax-withheld box" in line and f"`{category}`" in line]
            assert "this run computes" in row, category

    def test_ptet_takes_line_5a_less_the_usable_credit(self):
        """Tax attributable to K-1 income can differ from the usable PTET
        credit, and a usable credit refunded next year is income then under
        the tax benefit rule, so its timing is not savings. Both the procedure
        and the headroom description must subtract the credit, refunded or not,
        and an unknown credit must degrade toward savings that read low."""
        ptet = " ".join((REPO / ".agents/skills/tax-ptet/SKILL.md").read_text(encoding="utf-8").split())
        assert "`state_local_income_taxes` recipe" in ptet
        assert "PTET credit this year's election makes usable**" in ptet
        assert "less the credit this year's election makes usable" in ptet
        assert "tax benefit rule" in ptet
        assert "Never subtract a credit from an earlier year's election" in ptet
        # The electing state's tax counts at the smaller of payments and the
        # year's tax, and the credit nets against that tax: a prior-year
        # balance or over-withholding otherwise let a refundable excess, PTET
        # already deducted and income in full next year, through as savings.
        assert "smaller of what was paid toward it this year and the owner's tax for the year" in ptet
        assert "no more than that state's tax for the year" in ptet
        assert "**Stop** when a refundable credit (NY, NJ) exceeds that state's tax" in ptet
        assert "`PTET (<STATE>): not priced — refundable credit exceeds the owner's <STATE> tax`" in ptet
        assert "A nonrefundable CA credit beyond the tax carries forward instead: no stop" in ptet
        # Payments below the credit can take the figure negative, which the
        # analyzer refuses; two entities in one state are one credit; and the
        # stopped state must leave the run, or its savings still print.
        assert "flooring the result at 0" in ptet
        # $44,000 less the $10,000 credit; the year's $20,000 tax, paid exactly,
        # counts in full and caps nothing here.
        assert "paid exactly, and a usable $10,000 credit leave `--other-salt 34000`" in ptet
        assert "all of that state's entities' credits together" in ptet
        assert "Leave that state's `--income` out of the run" in ptet
        assert "This holds on the standard deduction too" in ptet
        assert "when every state stops, give the stop lines and deadlines and skip the run" in ptet
        assert "at the same marginal rate both years" in ptet
        assert "as if the entity did not elect" in ptet
        assert "Unknown, take this year's whole PTET as the credit" in ptet
        assert "Do not reduce already-paid withholding" not in ptet
        assert "less the state income tax on the step-2 qualifying income" not in ptet
        assert "less the tax on the electing income" not in ptet
        assert "state income/wage" not in ptet

    def test_every_state_with_a_w2_category_has_a_1099_route(self):
        """CA retains its W-2-only field; NY/NC widen their form totals.
        Every registered state must have its corresponding route in both
        the parse table and the broker-box contract, never only one."""
        parse_md = skill_text("tax-parse")
        accounts = (REPO / "docs/contributing/crypto-accounts.md").read_text(encoding="utf-8")
        computed = {c.removeprefix("state_withholding_").upper() for c in T.CATEGORY_TARGETS
                    if c.startswith("state_withholding_")}
        for code in computed:
            category = "ca_1099_withholding" if code == "CA" else f"state_withholding_{code.lower()}"
            (row,) = [line for line in parse_md.splitlines()
                      if "1099's state-tax-withheld box" in line and f"`{category}`" in line]
            assert "this run computes" in row
            assert f"`{category}` for {code}" in accounts
        assert "other_state_withholding_by_state" in parse_md and "other_state_withholding_by_state" in accounts

    def test_a_w2g_routes_as_a_1099_does(self):
        """W-2G is not a 1099; its withheld payment must be explicitly routed."""
        parse_md = skill_text("tax-parse")
        (row,) = [line for line in parse_md.splitlines() if "1099's state-tax-withheld box" in line]
        assert "W-2G" in row.split("|")[1]
        for category in ("ca_1099_withholding", "state_withholding_ny", "state_withholding_nc"):
            assert f"`{category}`" in row

    def test_a_1099_local_tax_for_nyc_or_yonkers_has_its_own_route(self):
        """IT-201 72-74 / IT-203 62-64 include retirement/gambling withholding;
        a city payment is never folded into the state line."""
        parse_md = skill_text("tax-parse")
        for category in ("city_withholding_nyc", "city_withholding_yonkers"):
            (row,) = [line for line in parse_md.splitlines()
                      if line.lstrip().startswith("| W-2") and f"`{category}`" in line]
            assert "1099/W-2G local-tax box" in row and "this run computes NY" in row

    def test_every_1099_state_amount_reaches_the_schedule_a_recipe(self):
        """State income tax withheld is deductible on Schedule A line 5a
        whichever state's return claims it."""
        recipe = (REPO / ".agents/skills/tax-compute/references/income-and-agi.md").read_text(encoding="utf-8")
        line = recipe[recipe.index("- `state_local_income_taxes` ="):].split("\n- ")[0]
        assert "every 1099's state" in line and "all states" in line.split("1099")[1], line

    def test_the_schedule_a_recipe_takes_every_item_of_the_line_5a_list(self):
        """Each item of the TY2025 Schedule A instructions' line 5a list, the
        local half of each included: the recipe once took W-2 box 17, CA SDI
        and STATE payments alone, so a NYC resident's box 19 never reached an
        itemized return."""
        recipe = (REPO / ".agents/skills/tax-compute/references/income-and-agi.md").read_text(encoding="utf-8")
        line = " ".join(recipe[recipe.index("- `state_local_income_taxes` ="):].split("\n- ")[0].split())
        for item in ("W-2 box 17 (all states)", "counted once from its own form",
                     "box 19 (every locality's income tax", "local tax withheld (1099-R box 17)",
                     "a W-2G's (boxes 15 and 17)", "Forms 592-B and 593",
                     "state's own disability fund", "unemployment fund (AK, CA, NJ, PA)",
                     "paid family or medical leave program", "Rev. Rul. 2025-4",
                     "State and local estimated payments", "prior-year overpayment credited",
                     "prior-year state and local balances", "never their penalties or interest",
                     "supplemental workers' compensation fund",
                     "Never to a private or voluntary plan", "employer confirms the state fund",
                     "Local Services Tax is not an income tax",
                     "deducted on Schedule C, E or F", '"Joint state and local returns"',
                     "made in good faith", "unless that income is interest", "(14a from TY2026)"):
            assert item in line, item

    # Each form's own face (W-2 2025 and 2026, 1099-R, and the 1099-DIV,
    # 1099-B and 1099-DA AA59 read): the state and local tax-withheld boxes,
    # with the boxes that say whose tax it is, and the W-2's box 14, where the
    # recipe reads the state-fund contributions. A summary that never captured
    # one has nothing for the recipe above to sum.
    _WITHHELD_BOXES = {
        "W-2": {"14", "15", "17", "19", "20"},
        "1099-R": {"14", "15", "17", "18"},
        "1099-DIV": {"14", "15", "16"},
        "1099-B": {"14", "15", "16"},
        "1099-DA": {"14", "15", "16"},
    }

    @pytest.mark.parametrize("form", sorted(_WITHHELD_BOXES))
    def test_tax_parse_captures_each_forms_withheld_boxes(self, form):
        parse_md = skill_text("tax-parse")
        listed = parse_md[parse_md.index(f"### {form} Extraction"):].splitlines()[1]
        assert self._WITHHELD_BOXES[form] <= listed_boxes(listed.split(":", 1)[1]), listed
        if form == "W-2":  # box 14's labels name the funds the recipe reads
            assert "every label, SDI, PFL and UI among them" in listed, listed

    def test_compute_names_each_one_where_a_failed_gate_sends_the_reader(self):
        """Step 12.7's exit-2 guidance says "fix the ledger"; for these the fix
        is to keep the record, so each one must be named where that is read."""
        compute = (REPO / ".agents/skills/tax-compute/SKILL.md").read_text(encoding="utf-8")
        start = compute.index("**2 (FAILED): STOP")
        guidance = " ".join(compute[start:compute.index("**1 (error):**", start)].split())
        for category in T._BLOCKING_CATEGORIES:
            assert f"`{category}`" in guidance, category
        assert "never re-file it under another category" in guidance

    def test_none_of_them_is_also_tied(self):
        """Each names an amount no result line holds, so none may sit in a
        registry that compares it with one."""
        assert not set(T._BLOCKING_CATEGORIES) & (set(T.CATEGORY_TARGETS) | T._INFORMATIONAL)
        assert "other_state_withholding" not in T.CATEGORY_TARGETS


# --------------------------------------------------------------------------
# Check D — unverifiable categories warn loudly, never silently pass
# --------------------------------------------------------------------------

def test_unverifiable_category_warns_when_section_present():
    """Section PRESENT but comparable figures unavailable (only summarized
    gains, no proceeds detail) → loud warning, not a failure."""
    recs = _ledger(_record(doc_id="b", file="documents/1099B.pdf",
                           form_type="1099-B", payer="Broker",
                           key_boxes={"capital_proceeds": 50000.0}))
    result = {"schedule_d": {"line_16_combined": 8000.0}}
    report = T.evaluate(recs, result, ["documents/1099B.pdf"])
    assert not report.failed
    assert report.unverifiable == ("capital_proceeds",)
    assert "Unverifiable" in T.render_markdown(report)


def test_broker_doc_without_schedule_d_section_fails():
    """Codex P1 round 6 (same class): ledger proceeds with NO schedule_d
    section at all = a dropped brokerage, not a detail gap."""
    recs = _ledger(_record(doc_id="b", file="documents/1099B.pdf",
                           form_type="1099-B", payer="Broker",
                           key_boxes={"capital_proceeds": 50000.0}))
    report = T.evaluate(recs, {"form_1040": {}}, ["documents/1099B.pdf"])
    assert report.failed
    assert any("capital_proceeds" in t for t in report.missing_result_targets)


def test_capital_proceeds_verifiable_when_schedule_d_detail_present():
    recs = _ledger(_record(doc_id="b", file="documents/1099B.pdf",
                           form_type="1099-B", payer="Broker",
                           key_boxes={"capital_proceeds": 50000.0}))
    result = {"schedule_d": {"part_i_short_term": {
        "line_1a_1099b_box_a": {"proceeds": 50000.0, "cost": 42000.0, "gain": 8000.0}}}}
    report = T.evaluate(recs, result, ["documents/1099B.pdf"])
    assert not report.failed
    assert report.unverifiable == ()


def test_lt_only_return_with_zero_template_st_rows_warns_not_fails():
    """Codex P2 round 1: the template ships zero-valued Part I rows — a
    long-term-only broker must not be compared against \\$0 short-term
    proceeds and fail a correct return. Zero scaffolding = unverifiable."""
    recs = _ledger(_record(doc_id="b", file="documents/1099B.pdf",
                           form_type="1099-B", payer="Broker",
                           key_boxes={"capital_proceeds": 50000.0}))
    result = {"schedule_d": {
        "part_i_short_term": {
            "line_1a_1099b_box_a": {"proceeds": 0, "cost": 0, "gain": 0}},
        "part_ii_long_term": {"line_15_net_long_term": 8000.0}}}
    report = T.evaluate(recs, result, ["documents/1099B.pdf"])
    assert not report.failed
    assert report.unverifiable == ("capital_proceeds",)


def test_crypto_box_c_f_rows_excluded_from_proceeds_tie():
    """Codex P1 round 10: /tax-parse-crypto merges crypto disposals into Box
    C/F rows (no 1099-B backs them) while its CSVs are ledgered as documented
    skips — a broker+crypto return must not fail because the result side
    carries crypto proceeds the ledger never will."""
    recs = _ledger(_record(doc_id="b", file="documents/1099B.pdf",
                           form_type="1099-B", payer="Broker",
                           key_boxes={"capital_proceeds": 50000.0}))
    result = {"schedule_d": {
        "part_i_short_term": {
            "line_1a_1099b_box_a": {"proceeds": 50000.0, "cost": 42000.0, "gain": 8000.0},
            "line_1c_1099b_box_c": {"proceeds": 10000.0, "cost": 6000.0, "gain": 4000.0}},
        "part_ii_long_term": {
            "line_10_1099b_box_f": {"proceeds": 7000.0, "cost": 2000.0, "gain": 5000.0}}}}
    report = T.evaluate(recs, result, ["documents/1099B.pdf"])
    assert not report.failed
    check = next(c for c in report.sum_checks if c.category == "capital_proceeds")
    assert check.result_total == 50000.0 and check.ok


def test_retirement_taxable_mismatch_is_advisory_not_failure():
    """Codex P2 round 10: 1099-R box 2a is payer-reported; a backdoor-Roth
    return recomputes line 4b via Form 8606 — a mismatch must WARN, not exit
    2. The dropped-1099-R direction stays hard-gated by retirement_gross."""
    recs = _ledger(_record(doc_id="r", file="documents/1099R.pdf",
                           form_type="1099-R", payer="Broker",
                           key_boxes={"retirement_gross": 30000.0,
                                      "retirement_taxable": 30000.0}))
    result = {"form_1040": {"line_4a_ira_distributions": 30000.0,
                            "line_5a_pensions": 0,
                            "line_4b_taxable_ira": 12000.0,
                            "line_5b_taxable_pensions": 0}}
    report = T.evaluate(recs, result, ["documents/1099R.pdf"])
    assert not report.failed
    check = next(c for c in report.sum_checks
                 if c.category == "retirement_taxable")
    assert not check.ok and check.advisory
    assert "VERIFY" in T.render_markdown(report)
    gross = next(c for c in report.sum_checks
                 if c.category == "retirement_gross")
    assert gross.ok and not gross.advisory


def test_dropped_1099r_still_fails_via_gross():
    recs = _ledger(_record(doc_id="r", file="documents/1099R.pdf",
                           form_type="1099-R", payer="Broker",
                           key_boxes={"retirement_gross": 30000.0}))
    report = T.evaluate(
        recs, {"form_1040": {"line_4a_ira_distributions": 0,
                             "line_5a_pensions": 0}},
        ["documents/1099R.pdf"])
    assert report.failed


def test_negative_ss_benefits_allowed_and_advisory():
    """Codex P2 round 10: SSA-1099 box 5 goes negative when repayments exceed
    gross benefits (Pub 915) — must load and produce an advisory, not exit 1
    or a sum tie."""
    recs = _ledger(_record(doc_id="ssa", file="documents/SSA1099.pdf",
                           form_type="SSA-1099", payer="SSA",
                           key_boxes={"ss_benefits": -1200.0}))
    report = T.evaluate(recs, {"form_1040": {"line_6a_social_security": 0}},
                        ["documents/SSA1099.pdf"])
    assert not report.failed
    assert not any(c.category == "ss_benefits" for c in report.sum_checks)
    assert any("NEGATIVE" in a for a in report.advisories)
    assert "Advisory items" in T.render_markdown(report)


def test_state_refund_emits_advisory():
    """Codex P2 round 10: a 1099-G refund may be taxable (prior-year
    itemizer) — informational must not mean invisible."""
    recs = _ledger(_record(doc_id="g", file="documents/1099G.pdf",
                           form_type="1099-G", payer="FTB",
                           key_boxes={"state_refund_1099g": 800.0}))
    report = T.evaluate(recs, {"form_1040": {}}, ["documents/1099G.pdf"])
    assert not report.failed
    assert any("state_refund_1099g" in a and "itemized" in a
               for a in report.advisories)


def test_personal_1099k_informational_with_advisory():
    """Codex P2 round 10: a personal-item 1099-K is not Schedule C income —
    it must not be forced into self_employment_gross, and its presence emits
    a classification advisory."""
    recs = _ledger(_record(doc_id="k", file="documents/1099K.pdf",
                           form_type="1099-K", payer="Venmo",
                           key_boxes={"personal_1099k": 2500.0}))
    report = T.evaluate(recs, {"form_1040": {}}, ["documents/1099K.pdf"])
    assert not report.failed
    assert not any(c.category == "personal_1099k" for c in report.sum_checks)
    assert any("personal_1099k" in a and "8949" in a for a in report.advisories)


def test_capital_proceeds_found_in_part_ii_detail():
    recs = _ledger(_record(doc_id="b", file="documents/1099B.pdf",
                           form_type="1099-B", payer="Broker",
                           key_boxes={"capital_proceeds": 50000.0}))
    result = {"schedule_d": {"part_ii_long_term": {
        "line_8a_1099b_box_d": {"proceeds": 50000.0, "cost": 30000.0, "gain": 20000.0}}}}
    report = T.evaluate(recs, result, ["documents/1099B.pdf"])
    assert not report.failed
    assert report.unverifiable == ()


def test_capital_gain_distributions_tie_to_schedule_d_line_13():
    """Codex P2 round 1: 1099-DIV box 2a needs a category — matched passes."""
    recs = _ledger(_record(doc_id="d", file="documents/1099DIV.pdf",
                           form_type="1099-DIV", payer="Fund",
                           key_boxes={"capital_gain_distributions": 3000.0}))
    result = {"schedule_d": {"part_ii_long_term": {"line_13_cap_gain_dist": 3000.0}}}
    report = T.evaluate(recs, result, ["documents/1099DIV.pdf"])
    assert not report.failed


def test_dropped_capital_gain_distribution_fails():
    """The template ships line 13 as 0 — present — so a dropped mutual-fund
    distribution FAILS the sum check instead of hiding as unverifiable."""
    recs = _ledger(_record(doc_id="d", file="documents/1099DIV.pdf",
                           form_type="1099-DIV", payer="Fund",
                           key_boxes={"capital_gain_distributions": 3000.0}))
    result = {"schedule_d": {"part_ii_long_term": {"line_13_cap_gain_dist": 0}}}
    report = T.evaluate(recs, result, ["documents/1099DIV.pdf"])
    assert report.failed
    check = next(c for c in report.sum_checks
                 if c.category == "capital_gain_distributions")
    assert check.result_total == 0.0 and not check.ok


def test_employer_stock_adjustment_ties_when_applied():
    """J1: supplemental statement ledgers a +$50K basis correction; the
    Schedule D row carries adjustment −50000 (reduces gain) → the negated sum
    ties, gate passes."""
    recs = _ledger(_record(doc_id="s", file="documents/supp.pdf",
                           form_type="brokerage_statement", payer="Broker",
                           key_boxes={"employer_stock_basis_adjustment": 50000.0}))
    result = {"schedule_d": {"part_i_short_term": {
        "line_1a_1099b_box_a": {"proceeds": 60000.0, "cost": 0.0,
                                "adjustment": -50000.0, "adjustment_code": "B",
                                "employer_stock": True, "gain": 10000.0}}}}
    report = T.evaluate(recs, result, ["documents/supp.pdf"])
    assert not report.failed
    check = next(c for c in report.sum_checks
                 if c.category == "employer_stock_basis_adjustment")
    assert check.result_total == 50000.0 and check.ok


def test_employer_stock_adjustment_ignores_non_employer_code_b_rows():
    """Codex P2 (PR #47): code B is NOT unique to employer stock — a non-
    employer 1099-B basis correction (also code B) on another lot must not
    corrupt the reconciliation. The scan filters on the `employer_stock`
    provenance marker, not the code."""
    recs = _ledger(_record(doc_id="s", file="documents/supp.pdf",
                           form_type="brokerage_statement", payer="Broker",
                           key_boxes={"employer_stock_basis_adjustment": 50000.0}))
    result = {"schedule_d": {"part_i_short_term": {
        "line_1a_1099b_box_a": {"proceeds": 60000.0, "cost": 0.0,
                                "adjustment": -50000.0, "adjustment_code": "B",
                                "employer_stock": True, "gain": 10000.0},
        "line_2_1099b_box_b": {"proceeds": 9000.0, "cost": 12000.0,
                               "adjustment": -1000.0, "adjustment_code": "B",
                               "employer_stock": False, "gain": -4000.0}}}}
    report = T.evaluate(recs, result, ["documents/supp.pdf"])
    assert not report.failed
    check = next(c for c in report.sum_checks
                 if c.category == "employer_stock_basis_adjustment")
    assert check.result_total == 50000.0 and check.ok


def test_employer_stock_adjustment_dropped_fails_hard_gate():
    """The double-tax scenario this category exists to kill: the supplemental
    statement parsed into the ledger but the adjustment never landed in
    result.json (row shows the unadjusted $0-basis gain) → FAIL."""
    recs = _ledger(_record(doc_id="s", file="documents/supp.pdf",
                           form_type="brokerage_statement", payer="Broker",
                           key_boxes={"employer_stock_basis_adjustment": 50000.0}))
    result = {"schedule_d": {"part_i_short_term": {
        "line_1a_1099b_box_a": {"proceeds": 60000.0, "cost": 0.0,
                                "adjustment": 0, "gain": 60000.0}}}}
    report = T.evaluate(recs, result, ["documents/supp.pdf"])
    assert report.failed
    check = next(c for c in report.sum_checks
                 if c.category == "employer_stock_basis_adjustment")
    assert check.result_total == 0.0 and not check.ok


def test_espp_purchase_info_is_informational_not_gated():
    """A Form 3922 with no sale of those shares is an informational record —
    it never fails the sum gate (feeds future-year basis)."""
    recs = _ledger(_record(doc_id="f", file="documents/3922.pdf",
                           form_type="3922", payer="Employer",
                           key_boxes={"espp_purchase_info": 1200.0}))
    result = {"schedule_d": {"part_i_short_term": {}}}
    report = T.evaluate(recs, result, ["documents/3922.pdf"])
    assert not report.failed
    assert not any(c.category == "espp_purchase_info" for c in report.sum_checks)


def test_self_employment_gross_is_at_least_check():
    """Codex P2 round 3: Schedule C gross receipts may legitimately EXCEED the
    1099 totals (cash income) — only ledger > result fails."""
    recs = _ledger(_record(doc_id="nec", file="documents/1099NEC.pdf",
                           form_type="1099-NEC", payer="Client",
                           key_boxes={"self_employment_gross": 30000.0}))
    more_on_return = T.evaluate(
        recs, {"schedule_c": {"line_1_gross_receipts": 35000.0}},
        ["documents/1099NEC.pdf"])
    assert not more_on_return.failed
    under_reported = T.evaluate(
        recs, {"schedule_c": {"line_1_gross_receipts": 20000.0}},
        ["documents/1099NEC.pdf"])
    assert under_reported.failed


def test_1099_nec_without_schedule_c_section_fails():
    """Codex P1 round 6: a 1099-NEC with NO schedule_c section means the form
    (and its SE tax) was never computed — that must FAIL the gate, not warn."""
    recs = _ledger(_record(doc_id="nec", file="documents/1099NEC.pdf",
                           form_type="1099-NEC", payer="Client",
                           key_boxes={"self_employment_gross": 30000.0}))
    report = T.evaluate(recs, {"form_1040": {}}, ["documents/1099NEC.pdf"])
    assert report.failed
    assert any("self_employment_gross" in t and "schedule_c" in t
               for t in report.missing_result_targets)
    assert "NO result section" in T.render_markdown(report)


def test_hsa_distributions_tie_to_8889():
    recs = _ledger(_record(doc_id="sa", file="documents/1099SA.pdf",
                           form_type="1099-SA", payer="HSA Bank",
                           key_boxes={"hsa_distributions": 2000.0}))
    report = T.evaluate(
        recs, {"form_8889_hsa": {"line_14a_distributions": 2000.0}},
        ["documents/1099SA.pdf"])
    assert not report.failed


def test_state_refund_is_informational():
    """1099-G refunds are taxable only if the prior year itemized — sum-tying
    them against the aggregate Schedule 1 line 8 would false-fail; the
    category exists for coverage, not sums."""
    recs = _ledger(_record(doc_id="g", file="documents/1099G.pdf",
                           form_type="1099-G", payer="FTB",
                           key_boxes={"state_refund_1099g": 800.0}))
    report = T.evaluate(recs, {"form_1040": {}}, ["documents/1099G.pdf"])
    assert not report.failed
    assert not any(c.category == "state_refund_1099g" for c in report.sum_checks)
    assert "state_refund_1099g" not in report.unverifiable


def test_ss_benefits_tie_to_line_6a_social_security():
    """Codex P2 round 2: the mapping must use the schema's real field name
    (line_6a_social_security) — a guessed name demotes a dropped SSA-1099
    from hard failure to unverifiable warning."""
    recs = _ledger(_record(doc_id="ssa", file="documents/SSA1099.pdf",
                           form_type="SSA-1099", payer="SSA",
                           key_boxes={"ss_benefits": 24000.0}))
    ok = T.evaluate(recs, {"form_1040": {"line_6a_social_security": 24000.0}},
                    ["documents/SSA1099.pdf"])
    assert not ok.failed and ok.unverifiable == ()
    dropped = T.evaluate(recs, {"form_1040": {"line_6a_social_security": 0}},
                         ["documents/SSA1099.pdf"])
    assert dropped.failed


def test_category_targets_fields_exist_in_result_template():
    """Every mapped field must exist in result_template.json — drift between
    the registry and the schema silently turns Check C failures into Check D
    warnings (the exact bug Codex caught on ss_benefits)."""
    template = json.loads(
        (REPO / "agents/templates/result_template.json").read_text())
    for cat, (section_name, fields) in T.CATEGORY_TARGETS.items():
        if cat in ("capital_proceeds", "employer_stock_basis_adjustment"):
            continue  # detail-row scan (_proceeds_total / _adjustments_total),
            #            not fixed field names
        section = template.get(section_name)
        assert isinstance(section, dict), f"{cat}: no {section_name} in template"
        for field in fields:
            node = section
            for seg in field.split("."):
                assert isinstance(node, dict) and seg in node, (
                    f"{cat}: {section_name}.{field} not in result_template")
                node = node[seg]


# --------------------------------------------------------------------------
# renderer + CLI
# --------------------------------------------------------------------------

def test_render_failure_verdict():
    recs = _ledger(_record())
    report = T.evaluate(recs, _result(wages=50000.0), ["documents/W2_Acme.pdf"])
    md = T.render_markdown(report)
    assert "FAILED" in md and "MISMATCH" in md


def _build_workspace(tmp_path, wages_in_result):
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "W2_Acme.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "parse_ledger.json").write_text(json.dumps(
        {"records": [_record()]}))
    (tmp_path / "result.json").write_text(json.dumps(
        _result(wages=wages_in_result)))


def _run_cli(workspace, *extra):
    return subprocess.run(
        [sys.executable, str(REPO / "agents/lib/tie_out.py"),
         "--workspace", str(workspace), *extra],
        capture_output=True, text=True)


def test_cli_exit_zero_and_writes_report(tmp_path):
    _build_workspace(tmp_path, wages_in_result=100000.0)
    res = _run_cli(tmp_path)
    assert res.returncode == 0
    assert "Tied out" in res.stdout
    assert (tmp_path / "summaries" / "_tie_out.md").exists()


def test_cli_exit_two_on_mismatch(tmp_path):
    _build_workspace(tmp_path, wages_in_result=50000.0)
    res = _run_cli(tmp_path)
    assert res.returncode == 2
    assert "FAILED" in res.stdout


def test_cli_json_output(tmp_path):
    _build_workspace(tmp_path, wages_in_result=100000.0)
    res = _run_cli(tmp_path, "--json")
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["failed"] is False
    assert data["records"] == 1


def test_cli_missing_ledger_errors(tmp_path):
    (tmp_path / "result.json").write_text("{}")
    res = _run_cli(tmp_path)
    assert res.returncode == 1
    assert "parse_ledger.json not found" in res.stderr


def test_cli_malformed_ledger_errors(tmp_path):
    (tmp_path / "parse_ledger.json").write_text("{oops")
    (tmp_path / "result.json").write_text("{}")
    res = _run_cli(tmp_path)
    assert res.returncode == 1
    assert "malformed" in res.stderr


def test_bad_tolerance_rejected():
    with pytest.raises(T.TieOutError, match="tolerance"):
        T.evaluate([], {}, [], tolerance=float("nan"))


def test_iso_bargain_category_registered():
    """D9 review follow-up: Form 3921 bargain elements tie to the
    form_6251.iso_bargain_element input echo — a dropped 3921 must fail
    the gate, not stay invisible as a user assertion."""
    from agents.lib.tie_out import CATEGORY_TARGETS
    assert CATEGORY_TARGETS["iso_bargain"] == (
        "form_6251", ["iso_bargain_element"])


def test_every_category_documented_in_tax_parse_skill():
    """E5-review P1 enforcement: the tie-out only checks categories the
    ledger EMITS, and parse subagents learn categories from tax-parse.md's
    step-6.5 table — a category registered in CATEGORY_TARGETS but absent
    from the skill doc is dead protection (the gate passes vacuously).
    Mirrors the capabilities interview-asks-it grep test."""
    from agents.lib.tie_out import _ALL_CATEGORIES
    skill = skill_text("tax-parse")
    for cat in sorted(_ALL_CATEGORIES):
        assert f"`{cat}`" in skill, (
            f"tie-out category {cat!r} is registered but never documented "
            f"in .agents/skills/tax-parse/SKILL.md — parse subagents can't emit "
            f"it, so the tie never fires (register both halves or neither)")


# --------------------------------------------------------------------------
# PFIC §1291 completeness gate (machine-checked fail-closed)
# --------------------------------------------------------------------------

def test_pfic_incomplete_all_complete_false_fails():
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {"all_complete": False,
                                "funds": [{"complete": False}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"])
    assert report.failed
    assert report.pfic_incomplete


def test_pfic_incomplete_fund_level_false_fails():
    # all_complete missing/true but a per-fund complete=False still fails.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {"all_complete": True,
                                "funds": [{"complete": True}, {"complete": False}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"])
    assert report.failed
    assert report.pfic_incomplete


def test_pfic_all_complete_true_passes():
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {"all_complete": True,
                                "funds": [{"complete": True, "total_1291_addon": 0,
                                           "ordinary_income_current": 0, "nonexcess_ordinary": 0}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"])
    assert not report.pfic_incomplete
    assert not report.failed


def test_no_pfic_block_does_not_fail():
    # A return with no PFIC event is unaffected (backward compatible).
    recs = _ledger(_record())
    report = T.evaluate(recs, _result(), ["documents/W2_Acme.pdf"])
    assert not report.pfic_incomplete


def test_template_scaffold_empty_funds_does_not_fail():
    # Codex P1: result_template.json ships form_8621_pfic with all_complete:false
    # and funds:[] on EVERY return — the empty scaffold must NOT fail the gate.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {"all_complete": False, "funds": []}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"])
    assert not report.pfic_incomplete
    assert not report.failed


def test_acknowledged_pfic_event_with_no_fund_fails():
    # Codex P1: a PFIC taxable event acknowledged in the profile but with NO
    # computed fund (empty scaffold) must FAIL — the §1291 computation was skipped.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {"all_complete": False, "funds": []}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True)
    assert report.failed and report.pfic_incomplete


def test_acknowledged_pfic_event_absent_block_fails():
    # Even if the block was removed entirely, an acknowledged event with no
    # computation must fail closed.
    recs = _ledger(_record())
    report = T.evaluate(recs, _result(), ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True)
    assert report.failed and report.pfic_incomplete


def test_acknowledged_pfic_event_with_complete_fund_passes():
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {"all_complete": True, "funds": [
        {"complete": True, "total_1291_addon": 0, "ordinary_income_current": 0, "nonexcess_ordinary": 0}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, any_taxable_pfic_recorded=True)
    assert not report.pfic_incomplete and not report.failed


def test_acknowledged_pfic_event_fabricated_fund_no_holding_fails():
    # Codex round 11 P1: a complete funds[] entry with NO recorded taxable holding
    # must NOT pass — the attested holding facts are missing.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {"all_complete": True,
                                "funds": [{"complete": True, "fund_id": "A"}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, any_taxable_pfic_recorded=False)
    assert report.failed and report.pfic_incomplete


def test_pfic_per_fund_coverage_one_fund_missing_fails():
    # Codex round 9/10 P1: TWO taxable no-election funds (A, B) but only A computed —
    # identity matching by fund_id must catch the silently-omitted B.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {"all_complete": True,
                                "funds": [{"complete": True, "fund_id": "A"}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A", "B"},
                        any_taxable_pfic_recorded=True)
    assert report.failed and report.pfic_incomplete


def test_pfic_per_fund_coverage_all_present_passes():
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 0},
                  {"complete": True, "fund_id": "B", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 0}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A", "B"},
                        any_taxable_pfic_recorded=True)
    assert not report.pfic_incomplete and not report.failed


def test_pfic_duplicate_fund_id_does_not_cover_missing():
    # Codex round 10 P1: two COMPLETE entries for the SAME fund A must NOT satisfy
    # coverage of a second taxable fund B.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A"}, {"complete": True, "fund_id": "A"}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A", "B"},
                        any_taxable_pfic_recorded=True)
    assert report.failed and any("B" in m for m in report.pfic_incomplete)


def test_pfic_generic_entry_without_id_fails_coverage():
    # Codex round 10 P1: a generic {complete: true} entry with no fund_id cannot
    # cover an expected taxable fund.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {"all_complete": True, "funds": [{"complete": True}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"},
                        any_taxable_pfic_recorded=True)
    assert report.failed and report.pfic_incomplete


def test_pfic_elected_only_event_passes_without_computed_fund():
    # Codex round 10 P1: has_pfic_taxable_event set solely for an elected (QEF/MTM)
    # fund — hand-prepared, no §1291 result. Recorded but no expected no-election
    # id → must NOT fail (no fictitious result required).
    recs = _ledger(_record())
    report = T.evaluate(recs, _result(), ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids=set(),
                        any_taxable_pfic_recorded=True)
    assert not report.pfic_incomplete and not report.failed


def test_pfic_line24_addon_not_reconciled_fails():
    # Codex round 10 P1 / round 23: a nonzero total_1291_addon that never reached
    # TOTAL TAX (line 24) must fail — all_complete alone does not prove the charge
    # was filed. The add-on belongs on line 24 (after credits), not line 16.
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_16_tax"] = 20000.0
    result["form_1040"]["line_24_total_tax"] = 20000.0  # add-on never added
    result["form_8621_pfic"] = {
        "all_complete": True, "funds": [{"complete": True, "fund_id": "A",
            "total_1291_addon": 5000.0, "ordinary_income_current": 0,
            "nonexcess_ordinary": 0}],
        "total_1291_addon": 5000.0, "line_16_regular_tax": 20000.0,
        "line_24_regular_total_tax": 20000.0, "line_24_addon": 0,
        "line_24_with_addon": 20000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert report.failed and any("line 24" in m for m in report.pfic_incomplete)


def test_pfic_line24_addon_reconciled_passes():
    # Codex round 23: line 16 stays regular tax; the add-on lands in total tax.
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_16_tax"] = 20000.0  # regular tax only — NO add-on
    result["form_1040"]["line_24_total_tax"] = 22000.0  # reg total 17000 + add-on 5000
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": 5000.0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 0}],
        "total_1291_addon": 5000.0, "line_16_regular_tax": 20000.0,
        "line_24_regular_total_tax": 17000.0, "line_24_addon": 5000.0,
        "line_24_with_addon": 22000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert not report.pfic_incomplete and not report.failed


def test_pfic_addon_on_line16_fails_credits_would_erode():
    # Codex round 23 P1: folding the add-on into line 16 (so credits reduce it)
    # must FAIL — line 16 must equal line_16_regular_tax with NO add-on.
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_16_tax"] = 25000.0  # WRONG: regular 20000 + add-on 5000
    result["form_1040"]["line_24_total_tax"] = 25000.0
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": 5000.0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 0}],
        "total_1291_addon": 5000.0, "line_16_regular_tax": 20000.0,
        "line_24_regular_total_tax": 25000.0, "line_24_addon": 5000.0,
        "line_24_with_addon": 25000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert report.failed and any("line 24" in m for m in report.pfic_incomplete)


def test_pfic_line24_self_consistent_but_arithmetically_wrong_fails():
    # Codex round 11 P1: line_24_addon=5000 and line_24_with_addon=17000==line_24_tax
    # AGREE with each other, but the true total should be 22000 (reg 17000 + 5000).
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_16_tax"] = 20000.0
    result["form_1040"]["line_24_total_tax"] = 17000.0  # add-on never actually applied
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": 5000.0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 0}],
        "total_1291_addon": 5000.0, "line_16_regular_tax": 20000.0,
        "line_24_regular_total_tax": 17000.0, "line_24_addon": 5000.0,
        "line_24_with_addon": 17000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert report.failed and any("line 24" in m for m in report.pfic_incomplete)


def test_pfic_aggregate_addon_must_equal_fund_sum():
    # total_1291_addon must equal the SUM of the per-fund add-ons.
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_16_tax"] = 20000.0
    result["form_1040"]["line_24_total_tax"] = 22000.0
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": 3000.0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 0}],  # != 5000
        "total_1291_addon": 5000.0, "line_16_regular_tax": 20000.0,
        "line_24_regular_total_tax": 17000.0, "line_24_addon": 5000.0,
        "line_24_with_addon": 22000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert report.failed and any("line 24" in m for m in report.pfic_incomplete)


def test_pfic_flag_holding_contradiction_fails():
    # Codex round 12 P1: a taxable fund recorded but flag says no event — a stale
    # flag must not bypass Form 8621. Fail closed on the mismatch.
    recs = _ledger(_record())
    report = T.evaluate(recs, _result(), ["documents/W2_Acme.pdf"],
                        pfic_event_expected=False, any_taxable_pfic_recorded=True)
    assert report.failed and any("contradiction" in m for m in report.pfic_incomplete)


def test_pfic_negative_addon_fails():
    # Codex round 12 P1: a negative add-on skips the `> 0` reconciliation — must
    # be rejected outright (deferred tax / interest can never be negative).
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": -500.0}],
        "total_1291_addon": -500.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert report.failed and any("NEGATIVE" in m for m in report.pfic_incomplete)


def test_pfic_ordinary_income_not_in_line15_fails():
    # Codex round 12 P1: current-year ordinary income computed but never folded
    # into taxable income (line_15_pfic_ordinary_income absent) must fail.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "ordinary_income_current": 10000.0, "nonexcess_ordinary": 0}],
        "total_ordinary_income_current": 10000.0}  # not reconciled into line 15
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert report.failed and any("taxable income" in m for m in report.pfic_incomplete)


def test_pfic_ordinary_income_reconciled_passes():
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_15_taxable_income"] = 90000.0  # 80000 regular + 10000
    result["form_1040"]["line_9_total_income"] = 100000.0    # +10000 into gross income
    result["form_1040"]["line_11_agi"] = 100000.0            # +10000 into AGI
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "ordinary_income_current": 10000.0,
                   "total_1291_addon": 0, "nonexcess_ordinary": 0}],
        "total_ordinary_income_current": 10000.0, "total_nonexcess_ordinary": 0,
        "line_9_regular_total_income": 90000.0, "line_11_regular_agi": 90000.0,
        "line_15_regular_taxable_income": 80000.0,
        "line_15_pfic_ordinary_income": 10000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert not report.pfic_incomplete and not report.failed


def test_pfic_ordinary_income_line15_unchanged_fails():
    # Codex round 15 P1: the self-reported fields agree, but line 15 was left at
    # the pre-PFIC value (regular + ordinary != filed line 15) — must FAIL.
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_15_taxable_income"] = 80000.0  # NOT increased by 10000
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "ordinary_income_current": 10000.0,
                   "total_1291_addon": 0}],
        "total_ordinary_income_current": 10000.0,
        "line_15_regular_taxable_income": 80000.0,
        "line_15_pfic_ordinary_income": 10000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert report.failed and any("taxable income" in m for m in report.pfic_incomplete)


def test_expected_pfic_fund_ids():
    # Distribution OR disposition on a no-election fund yields its fund_id; a
    # no-id taxable fund yields an unmatchable placeholder; elected counts as
    # recorded but not expected; buy-and-hold/non-fund/junk excluded.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "received_distribution": True, "fund_id": "A"},
        {"kind": "fund", "pfic_election": "none", "disposed": True},  # no fund_id → placeholder
        {"kind": "fund", "pfic_election": "none"},                    # buy-and-hold
        {"kind": "fund", "pfic_election": "qef", "disposed": True, "fund_id": "Q"},  # elected
        {"kind": "bond", "disposed": True},                          # not a fund
        "junk",
    ]}}
    ids, any_taxable, invalid = T._expected_pfic_fund_ids(profile)
    assert "A" in ids and any(i.startswith("__unlabeled_") for i in ids) and len(ids) == 2
    assert any_taxable is True and invalid == []


def test_expected_pfic_fund_ids_unknown_election():
    # Codex round 15 P1: an unrecognized election value is neither computed nor
    # treated as valid QEF/MTM — it lands in invalid_elections.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "unknown", "disposed": True, "fund_id": "X"},
    ]}}
    ids, any_taxable, invalid = T._expected_pfic_fund_ids(profile)
    assert ids == set() and any_taxable is True and len(invalid) == 1 and "X" in invalid[0]


def test_pfic_invalid_election_fails_gate():
    recs = _ledger(_record())
    report = T.evaluate(recs, _result(), ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, any_taxable_pfic_recorded=True,
                        invalid_pfic_elections=["X: pfic_election='unknown'"])
    assert report.failed and any("unrecognized" in m for m in report.pfic_incomplete)


def test_expected_pfic_fund_ids_missing_detail():
    assert T._expected_pfic_fund_ids({}) == (set(), False, [])
    assert T._expected_pfic_fund_ids({"foreign_holdings_detail": {}}) == (set(), False, [])
    assert T._expected_pfic_fund_ids({"foreign_holdings_detail": {"holdings": "x"}}) == (set(), False, [])


def test_us_domiciled_fund_not_a_pfic_event():
    # Codex round 13 P1: a US-domiciled fund at a foreign broker is NOT a PFIC —
    # its distribution/sale must not be a §1291 event (would wrongly block a valid
    # return). An absent/foreign domicile stays a PFIC event (fail-closed).
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "fund_domicile": "US", "pfic_election": "none",
         "received_distribution": True, "fund_id": "USFUND"},
        {"kind": "fund", "fund_domicile": "IE", "pfic_election": "none",
         "received_distribution": True, "fund_id": "IEFUND"},
        {"kind": "fund", "received_distribution": True, "fund_id": "UNKNOWN"},  # no domicile
    ]}}
    ids, any_taxable, invalid = T._expected_pfic_fund_ids(profile)
    assert ids == {"IEFUND", "UNKNOWN"}  # US excluded; unknown treated as foreign
    assert any_taxable is True and invalid == []


def test_pfic_per_fund_negative_masked_by_positive_fails():
    # Codex round 13 P1: a -500 add-on in one fund masked by +1000 in another has a
    # positive aggregate but is still malformed — per-fund check must catch it.
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_16_tax"] = 20500.0
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": -500.0},
                  {"complete": True, "fund_id": "B", "total_1291_addon": 1000.0}],
        "total_1291_addon": 500.0, "line_16_regular_tax": 20000.0,
        "line_24_addon": 500.0, "line_24_with_addon": 20500.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A", "B"}, any_taxable_pfic_recorded=True)
    assert report.failed and any("NEGATIVE" in m for m in report.pfic_incomplete)


def test_pfic_nan_value_treated_as_absent():
    # Codex round 13 P1: json NaN must not slip through the reconciliation guards.
    import math as _math
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": _math.nan}],
        "total_1291_addon": _math.nan}
    report = T.evaluate(_ledger(_record()), result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    # The NaN add-on is caught by the per-fund non-finite check.
    assert report.failed and any("non-finite" in m for m in report.pfic_incomplete)


def test_get_num_rejects_nan():
    assert T._get_num({"x": float("nan")}, "x") is None
    assert T._get_num({"x": float("inf")}, "x") is None
    assert T._get_num({"x": 5.0}, "x") == 5.0


def test_pfic_positive_per_fund_addon_with_zero_aggregate_fails():
    # Codex round 14 P1: a positive per-fund add-on but the aggregate left at the
    # template default 0 must NOT skip reconciliation (line 16 unchanged).
    recs = _ledger(_record())
    result = _result()  # line_16_tax default (unchanged)
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": 5000.0}],
        "total_1291_addon": 0}  # aggregate never updated
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert report.failed and any("line 16" in m for m in report.pfic_incomplete)


def test_pfic_positive_per_fund_ordinary_with_zero_aggregate_fails():
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "ordinary_income_current": 10000.0, "nonexcess_ordinary": 0}],
        "total_ordinary_income_current": 0}  # aggregate never updated
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert report.failed and any("taxable income" in m for m in report.pfic_incomplete)


def test_pfic_nonfinite_aggregate_with_finite_funds_fails():
    # Codex round 14 P1: NaN aggregate (→ _get_num None) while per-fund is finite
    # must be caught as malformed, not silently skip reconciliation.
    import math as _math
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_16_tax"] = 25000.0
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": 5000.0}],
        "total_1291_addon": _math.nan, "line_16_regular_tax": 20000.0,
        "line_24_addon": 5000.0, "line_24_with_addon": 25000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert report.failed and any("non-finite" in m for m in report.pfic_incomplete)


def test_pfic_complete_fund_missing_amount_fields_fails():
    # Codex round 15 P1: a complete no-election fund with only a fund_id (no
    # amount fields) lets _fund_sum read the missing amounts as 0, skipping
    # reconciliation for a real distribution/gain. Require the fields present.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True, "funds": [{"complete": True, "fund_id": "A"}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert report.failed and any("missing a finite amount field" in m
                                 for m in report.pfic_incomplete)


def test_pfic_complete_fund_zero_amounts_ok():
    # $0 is a valid amount (a genuine zero-tax loss disposition) as long as the
    # fields are PRESENT.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 0}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert not report.pfic_incomplete and not report.failed


def test_expected_pfic_fund_ids_duplicate_ids_unmatchable():
    # Codex round 16 P1: two distinct taxable no-election funds sharing a fund_id
    # must NOT collapse — the duplicate gets an unmatchable placeholder so coverage
    # fails until distinct ids are assigned.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "disposed": True, "fund_id": "DUP"},
        {"kind": "fund", "pfic_election": "none", "received_distribution": True, "fund_id": "DUP"},
    ]}}
    ids, any_taxable, invalid = T._expected_pfic_fund_ids(profile)
    assert "DUP" in ids
    assert any(i.startswith("__duplicate_fund_id:DUP") for i in ids)
    assert len(ids) == 2  # one real + one unmatchable duplicate


def test_pfic_duplicate_holding_fund_id_fails_coverage():
    # A single computed result for the shared id must NOT satisfy both funds.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "DUP", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 0}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"DUP", "__duplicate_fund_id:DUP#1"},
                        any_taxable_pfic_recorded=True)
    assert report.failed and any("DUP" in m for m in report.pfic_incomplete)


def test_pfic_nonexcess_ordinary_omitted_from_line15_fails():
    # Codex round 17 P1: a below-threshold distribution has $0 excess (0 add-on,
    # 0 ordinary_income_current) but a positive nonexcess_ordinary that must reach
    # line 15. If line 15 was not increased by it, the gate must FAIL.
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_15_taxable_income"] = 80000.0  # NOT increased by 2000
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 2000.0}],
        "total_ordinary_income_current": 0, "total_nonexcess_ordinary": 2000.0,
        "line_15_regular_taxable_income": 80000.0,
        "line_15_pfic_ordinary_income": 2000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert report.failed and any("line 15" in m for m in report.pfic_incomplete)


def test_pfic_nonexcess_ordinary_reconciled_passes():
    # Below-threshold distribution correctly folded into line 15.
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_15_taxable_income"] = 82000.0  # 80000 + 2000 nonexcess
    result["form_1040"]["line_9_total_income"] = 92000.0
    result["form_1040"]["line_11_agi"] = 92000.0
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 2000.0}],
        "total_ordinary_income_current": 0, "total_nonexcess_ordinary": 2000.0,
        "line_9_regular_total_income": 90000.0, "line_11_regular_agi": 90000.0,
        "line_15_regular_taxable_income": 80000.0,
        "line_15_pfic_ordinary_income": 2000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True,
                        expected_pfic_fund_ids={"A"}, any_taxable_pfic_recorded=True)
    assert not report.pfic_incomplete and not report.failed


def test_pfic_positive_event_ids():
    # Codex round 20: a positive current-year distribution or a positive gain →
    # the fund must produce a nonzero result. A loss disposition / $0 distribution
    # does not.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "received_distribution": True,
         "fund_id": "DIST", "distributions_by_year": {"2024": 3000}},
        {"kind": "fund", "pfic_election": "none", "disposed": True,
         "fund_id": "GAIN", "disposition_gain_usd": 5000},
        {"kind": "fund", "pfic_election": "none", "disposed": True,
         "fund_id": "LOSS", "disposition_gain_usd": -2000},
        {"kind": "fund", "pfic_election": "none", "received_distribution": True,
         "fund_id": "ZERO", "distributions_by_year": {"2024": 0}},
    ]}}
    ids, missing, disp = T._pfic_positive_event_ids(profile, 2024)
    assert ids == {"DIST", "GAIN"} and missing == set()
    # Only the DISPOSITION gain is disposition-positive.
    assert disp == {"GAIN"}


def test_pfic_missing_event_amount_ids():
    # Codex round 21: a disposition with no gain amount, or a distribution with no
    # current-year amount, has unknown magnitude → flagged missing.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "disposed": True, "fund_id": "NOGAIN"},
        {"kind": "fund", "pfic_election": "none", "received_distribution": True,
         "fund_id": "NODIST"},  # no distributions_by_year
    ]}}
    ids, missing, disp = T._pfic_positive_event_ids(profile, 2024)
    assert ids == set() and missing == {"NOGAIN", "NODIST"}
    assert disp == set()


def test_pfic_missing_amount_fails_gate():
    recs = _ledger(_record())
    report = T.evaluate(recs, _result(), ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, any_taxable_pfic_recorded=True,
                        pfic_missing_amount_ids={"NOGAIN"})
    assert report.failed and any("finite amount" in m for m in report.pfic_incomplete)


def test_pfic_broker_reported_distribution_all_zero_fails():
    # Codex round 27: the unverifiable `distribution_in_parsed_docs` exemption is
    # RETIRED — a below-threshold broker-reported distribution must record its
    # nonexcess amount in nonexcess_ordinary (so it flows through the income
    # reconciliation + the parse-ledger tie-out). An all-zero fund for a positive
    # attested distribution now FAILS, even with the (obsolete) flag set.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "DIST", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 0,
                   "distribution_in_parsed_docs": True}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, expected_pfic_fund_ids={"DIST"},
                        any_taxable_pfic_recorded=True,
                        pfic_positive_event_ids={"DIST"})
    assert report.failed and any("ALL-ZERO" in m for m in report.pfic_incomplete)


def test_pfic_below_threshold_records_nonexcess_ordinary_ok():
    # The correct handling: a below-threshold broker-reported distribution records
    # its full nonexcess amount in nonexcess_ordinary (> 0), so the fund is not
    # all-zero and the income reconciles through lines 9/11/15.
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_15_taxable_income"] = 81000.0
    result["form_1040"]["line_9_total_income"] = 91000.0
    result["form_1040"]["line_11_agi"] = 91000.0
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "OK", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 1000}],
        "total_ordinary_income_current": 0, "total_nonexcess_ordinary": 1000,
        "line_15_pfic_ordinary_income": 1000,
        "line_9_regular_total_income": 90000.0, "line_11_regular_agi": 90000.0,
        "line_15_regular_taxable_income": 80000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, expected_pfic_fund_ids={"OK"},
                        any_taxable_pfic_recorded=True,
                        pfic_positive_event_ids={"OK"})
    assert not report.pfic_incomplete and not report.failed


def test_pfic_disposition_positive_detected():
    # Codex round 25/26: a fund with a positive disposition gain (with OR without a
    # same-year distribution) is a disposition-positive fund.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "BOTH",
         "preceding_holding_years": 3,
         "received_distribution": True,
         "distributions_by_year": {"2021": 2000, "2022": 2000, "2023": 2000,
                                   "2024": 1000},  # below threshold
         "disposed": True, "disposition_gain_usd": 5000},
        {"kind": "fund", "pfic_election": "none", "fund_id": "PURE",
         "disposed": True, "disposition_gain_usd": 3000},  # pure disposition
        {"kind": "fund", "pfic_election": "none", "fund_id": "LOSS",
         "disposed": True, "disposition_gain_usd": -1000},  # loss → not included
    ]}}
    ids, missing, disp = T._pfic_positive_event_ids(profile, 2024)
    assert disp == {"BOTH", "PURE"} and {"BOTH", "PURE"} <= ids


def test_pfic_pure_disposition_missing_disposition_ordinary_fails():
    # Codex round 26 P1: a fund SOLD AT A GAIN with no same-year distribution passes
    # the all-zero check on a stray nonexcess_ordinary=$10 even though the gain's
    # §1291 was never computed (ordinary_income_current/add-on = 0). Requiring a
    # positive disposition_ordinary_income for EVERY sale-at-gain fund catches it.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "PURE", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 10}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, expected_pfic_fund_ids={"PURE"},
                        any_taxable_pfic_recorded=True,
                        pfic_positive_event_ids={"PURE"},
                        pfic_disposition_positive_ids={"PURE"})
    assert report.failed and any("disposition_ordinary_income" in m
                                 for m in report.pfic_incomplete)


def test_pfic_dual_event_missing_disposition_ordinary_fails():
    # Codex round 25 P1: a dual-event fund whose aggregate result is nonzero (from a
    # small nonexcess distribution) but carries NO positive disposition_ordinary_
    # income must FAIL — the sale's §1291 may have been dropped behind the
    # distribution.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "BOTH", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 100}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, expected_pfic_fund_ids={"BOTH"},
                        any_taxable_pfic_recorded=True,
                        pfic_positive_event_ids={"BOTH"},
                        pfic_disposition_positive_ids={"BOTH"})
    assert report.failed and any("disposition_ordinary_income" in m
                                 for m in report.pfic_incomplete)


def test_pfic_dual_event_with_disposition_ordinary_passes():
    # A dual-event fund that records the sale leg's disposition_ordinary_income > 0
    # proves the disposition was run through §1291 → passes.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "BOTH", "total_1291_addon": 200,
                   "ordinary_income_current": 5100, "nonexcess_ordinary": 100,
                   "disposition_ordinary_income": 5000}]}
    result["form_1040"]["line_15_taxable_income"] = 85200.0
    result["form_1040"]["line_9_total_income"] = 95200.0
    result["form_1040"]["line_11_agi"] = 95200.0
    result["form_1040"]["line_16_tax"] = 20000.0
    result["form_1040"]["line_24_total_tax"] = 20200.0
    result["form_8621_pfic"].update({
        "total_ordinary_income_current": 5100, "total_nonexcess_ordinary": 100,
        "line_15_pfic_ordinary_income": 5200,
        "line_9_regular_total_income": 90000.0, "line_11_regular_agi": 90000.0,
        "line_15_regular_taxable_income": 80000.0,
        "total_1291_addon": 200, "line_16_regular_tax": 20000.0,
        "line_24_regular_total_tax": 20000.0, "line_24_addon": 200,
        "line_24_with_addon": 20200.0})
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, expected_pfic_fund_ids={"BOTH"},
                        any_taxable_pfic_recorded=True,
                        pfic_positive_event_ids={"BOTH"},
                        pfic_disposition_positive_ids={"BOTH"})
    assert not report.pfic_incomplete and not report.failed


def test_pfic_holding_year_checks_derives_from_dates():
    # Codex round 28: the asserted preceding_holding_years must not be shorter than
    # the acquisition date implies. Acquired 2020, event 2025 → derived 5 prior years;
    # asserting 1 is a mismatch. A DISPOSITION gain held ≥ 1 prior year and a
    # DISTRIBUTION held ≥ 1 prior year are flagged multi-year.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "UNDER",
         "acquisition_date": "2020-03-01", "preceding_holding_years": 1,
         "received_distribution": True, "distributions_by_year": {"2025": 10000}},
        {"kind": "fund", "pfic_election": "none", "fund_id": "SALE",
         "acquisition_date": "2021-01-01", "preceding_holding_years": 4,
         "disposed": True, "disposition_gain_usd": 50000},
        {"kind": "fund", "pfic_election": "none", "fund_id": "FIRSTYR",
         "acquisition_date": "2025-06-01", "preceding_holding_years": 0,
         "disposed": True, "disposition_gain_usd": 3000},  # same-year → not multi-year
    ]}}
    mismatch, multi_disp, multi_dist = T._pfic_holding_year_checks(profile, 2025)
    assert mismatch == {"UNDER"}          # asserted 1 < derived 5
    assert multi_disp == {"SALE"}         # FIRSTYR acquired same year → excluded
    assert multi_dist == {"UNDER"}


def test_pfic_holding_mismatch_fails_gate():
    recs = _ledger(_record())
    report = T.evaluate(recs, _result(), ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, any_taxable_pfic_recorded=True,
                        pfic_holding_mismatch_ids={"UNDER"})
    assert report.failed and any("SHORTER" in m for m in report.pfic_incomplete)


def test_pfic_multiyear_disposition_no_addon_fails():
    # Codex round 28: a multi-year sale-at-gain must produce a prior-year deferred-tax
    # add-on — a $1 disposition_ordinary_income with a $0 add-on is not a complete
    # multi-year §1291 computation.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "SALE", "total_1291_addon": 0,
                   "ordinary_income_current": 1, "nonexcess_ordinary": 0,
                   "disposition_ordinary_income": 1}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, expected_pfic_fund_ids={"SALE"},
                        any_taxable_pfic_recorded=True,
                        pfic_positive_event_ids={"SALE"},
                        pfic_disposition_positive_ids={"SALE"},
                        pfic_multiyear_disposition_ids={"SALE"})
    assert report.failed and any("PRIOR tax year" in m for m in report.pfic_incomplete)


def test_pfic_reclassified_dividend_ties_ledger():
    # Codex round 22: removing the PFIC excess from line 3b makes the (full box-1a)
    # ledger sum exceed the reduced filed line. reclassified_from_line_3b adds the
    # removed amount back to the result-side dividend total so the sum-check ties.
    result = _result()
    result["form_1040"]["line_3b_ordinary_dividends"] = 1000.0  # reduced by 400 excess
    result["form_8621_pfic"] = {"reclassified_from_line_3b": 400.0}
    # box 1a ledgered at the FULL 1400; result-side must reconcile to 1400.
    assert T._result_total(result, "ordinary_dividends") == 1400.0
    # without the reclassification field it would only see the reduced 1000.
    del result["form_8621_pfic"]["reclassified_from_line_3b"]
    assert T._result_total(result, "ordinary_dividends") == 1000.0


def test_pfic_reclassified_dividend_3b_uncapped():
    # Codex round 28: the line-3b add-back is UNCAPPED (a bookkeeping bridge), not
    # limited to total_ordinary_income_current — a multi-year excess allocates only
    # its current-year slice to ordinary_income_current, the rest to the deferred-tax
    # add-on, so a cap would reject a correct multi-year return. Here a $10,000 excess
    # with only $1,000 current allocation must still add back the full $10,000.
    result = _result()
    result["form_1040"]["line_3b_ordinary_dividends"] = 0.0
    result["form_8621_pfic"] = {"reclassified_from_line_3b": 10000.0,
                                "total_ordinary_income_current": 1000.0}
    assert T._result_total(result, "ordinary_dividends") == 10000.0


def test_pfic_recompute_per_fund_excess_addon_below_deferred_fails():
    # Codex round 29/30: a distribution with a recomputed EXCESS on a multi-year fund
    # must carry an add-on that at least covers the recomputed deferred tax (at the
    # real historical rates). Fund XS: 2020-acquired, $10,000 all-excess 2025
    # distribution, held 5 prior years — the prior-year allocations at 37% are
    # thousands of dollars, so a token $1 add-on FAILS. (Requires rates for the
    # deferred-tax magnitude check.)
    import agents.lib.tax_computations as TC
    rate_map = {yr: 0.37 for yr in range(2020, 2025)}
    exc = TC.compute_pfic_excess_distribution(10000, [0, 0, 0],
                                              preceding_holding_years=5)["excess_distribution"]
    rc = TC.compute_pfic_1291(exc, "2020-01-01", "2025-06-15", 2025, rate_map)
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "XS",
         "acquisition_date": "2020-01-01", "preceding_holding_years": 5,
         "received_distribution": True, "distribution_date": "2025-06-15",
         "distributions_by_year": {"2022": 0, "2023": 0, "2024": 0, "2025": 10000}},
    ]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "XS", "total_1291_addon": 1,
                   "ordinary_income_current": rc["ordinary_income_current"],
                   "nonexcess_ordinary": 0}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE,
                                        historical_top_rate=rate_map)
    assert any("deferred tax" in m and "XS" in m for m in failures)


def test_pfic_recompute_missing_acquisition_date_fails():
    # Codex round 29: a taxable fund with a positive event but no derivable acquisition
    # date must FAIL — the holding-period allocation can't be verified.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "NODATE",
         "disposed": True, "disposition_gain_usd": 50000},  # no acquisition_date
    ]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "NODATE",
                   "disposition_ordinary_income": 10000}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE)
    assert any("no derivable" in m and "NODATE" in m for m in failures)


def test_pfic_recompute_disposition_allocation_mismatch_fails():
    # Codex round 29: a large multi-year gain reported as a token current-year amount
    # must FAIL — disposition_ordinary_income is recomputed from the dates + gain.
    # Acquired 2020-01-01, sold 2025-12-31 → ~1/6 of the $120,000 gain is current-year
    # ordinary (~$20k); reporting $1 is a gross mismatch.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "SALE",
         "acquisition_date": "2020-01-01", "disposition_date": "2025-12-31",
         "disposed": True, "disposition_gain_usd": 120000},
    ]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "SALE", "total_1291_addon": 5000,
                   "disposition_ordinary_income": 1}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE)
    assert any("does not match" in m and "SALE" in m for m in failures)


def test_pfic_recompute_correct_disposition_passes():
    # A disposition whose ordinary allocation AND full add-on match the recompute
    # (both rate sets supplied) passes.
    import agents.lib.tax_computations as TC
    rc = TC.compute_pfic_1291(120000, "2020-01-01", "2025-12-31", 2025, _HIST_RATE_ALL,
                              underpayment_rates=_PFIC_6621, is_disposition=True)
    assert rc["complete"]
    expected = rc["ordinary_income_current"]
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "SALE",
         "acquisition_date": "2020-01-01", "disposition_date": "2025-12-31",
         "disposed": True, "disposition_gain_usd": 120000},
    ]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "SALE",
                   "total_1291_addon": rc["total_1291_addon"],
                   "ordinary_income_current": expected,  # both-legs total = disp leg
                   "disposition_ordinary_income": expected}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE,
                                        historical_top_rate=_HIST_RATE_ALL,
                                        underpayment_rates=_PFIC_6621)
    assert failures == []


def test_pfic_recompute_disposition_income_must_reach_ordinary_total():
    # Codex round 30 #3: a correct disposition_ordinary_income does NOT prove the
    # sale's allocation reached ordinary_income_current (the field routed into 1040
    # income). Here disposition_ordinary_income is right but ordinary_income_current
    # is 0 → the current-year inclusion is omitted → FAIL.
    import agents.lib.tax_computations as TC
    rate_map = {yr: 0.37 for yr in range(2020, 2025)}
    expected = TC.compute_pfic_1291(120000, "2020-01-01", "2025-12-31", 2025,
                                    rate_map, is_disposition=True)["ordinary_income_current"]
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "SALE",
         "acquisition_date": "2020-01-01", "disposition_date": "2025-12-31",
         "disposed": True, "disposition_gain_usd": 120000},
    ]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "SALE", "total_1291_addon": 5000,
                   "ordinary_income_current": 0,  # sale allocation NOT in the total
                   "disposition_ordinary_income": expected}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE)
    assert any("ordinary_income_current" in m and "SALE" in m for m in failures)


def test_pfic_recompute_holding_from_acquisition_not_pfic_start():
    # Codex round 30 #4: a fund already a PFIC in 2020 but PURCHASED in 2023 is held
    # from 2023 (2 prior years), not 2020. _acq_year must use acquisition_date, so a
    # correct preceding_holding_years=2 is NOT flagged as a mismatch.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "LATE",
         "acquisition_date": "2023-04-01", "pfic_start_date": "2020-01-01",
         "preceding_holding_years": 2, "received_distribution": True,
         "distributions_by_year": {"2025": 100}},
    ]}}
    mismatch, _d, _dist = T._pfic_holding_year_checks(profile, 2025)
    assert mismatch == set()  # derived from acquisition (2025-2023=2) == asserted 2


def test_pfic_recompute_addon_exact_match_passes():
    # Codex round 32 #3: the recorded add-on must EXACTLY match the recomputed deferred
    # tax + §6621 interest (both rate sets supplied) — a correct multi-year
    # distribution passes.
    import agents.lib.tax_computations as TC
    exc = TC.compute_pfic_excess_distribution(10000, [0, 0, 0],
                                              preceding_holding_years=5)["excess_distribution"]
    rc = TC.compute_pfic_1291(exc, "2020-01-01", "2025-06-15", 2025, _HIST_RATE_ALL,
                              underpayment_rates=_PFIC_6621)
    assert rc["complete"]
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "XS",
         "acquisition_date": "2020-01-01", "preceding_holding_years": 5,
         "received_distribution": True, "distribution_date": "2025-06-15",
         "distributions_by_year": {"2022": 0, "2023": 0, "2024": 0, "2025": 10000}},
    ]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "XS",
                   "total_1291_addon": rc["total_1291_addon"],  # deferred + interest, exact
                   "ordinary_income_current": rc["ordinary_income_current"],
                   "nonexcess_ordinary": 0}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE,
                                        historical_top_rate=_HIST_RATE_ALL,
                                        underpayment_rates=_PFIC_6621)
    assert failures == []


def test_pfic_recompute_addon_omitting_interest_fails():
    # Codex round 32 #3: an add-on that covers only the deferred tax (omitting §6621
    # interest) must FAIL the exact match.
    import agents.lib.tax_computations as TC
    exc = TC.compute_pfic_excess_distribution(10000, [0, 0, 0],
                                              preceding_holding_years=5)["excess_distribution"]
    rc = TC.compute_pfic_1291(exc, "2020-01-01", "2025-06-15", 2025, _HIST_RATE_ALL,
                              underpayment_rates=_PFIC_6621)
    assert rc["interest_total"] > T.DEFAULT_TOLERANCE
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "XS",
         "acquisition_date": "2020-01-01", "preceding_holding_years": 5,
         "received_distribution": True, "distribution_date": "2025-06-15",
         "distributions_by_year": {"2022": 0, "2023": 0, "2024": 0, "2025": 10000}},
    ]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "XS",
                   "total_1291_addon": rc["deferred_tax_total"],  # interest OMITTED
                   "ordinary_income_current": rc["ordinary_income_current"],
                   "nonexcess_ordinary": 0}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE,
                                        historical_top_rate=_HIST_RATE_ALL,
                                        underpayment_rates=_PFIC_6621)
    assert any("add-on" in m and "XS" in m for m in failures)


def test_pfic_recompute_no_rates_multiyear_fails_closed():
    # Codex round 32 #1: a multi-year event with NO cached rates must FAIL closed —
    # the untrusted result complete=True cannot substitute for verification.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "XS",
         "acquisition_date": "2020-01-01", "preceding_holding_years": 5,
         "received_distribution": True, "distribution_date": "2025-06-15",
         "distributions_by_year": {"2022": 0, "2023": 0, "2024": 0, "2025": 10000}},
    ]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "XS", "total_1291_addon": 2,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 0}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE)
    assert any("cannot be verified" in m and "XS" in m for m in failures)


def test_pfic_recompute_below_threshold_zero_ordinary_passes():
    # Codex round 31 #1: a fully NON-excess distribution belongs in nonexcess_ordinary
    # with ordinary_income_current == 0 — the recompute must NOT expect the nonexcess
    # in the §1291 current-year total. $100 vs a $2,000 three-year avg (threshold
    # $2,500) → 0 excess, whole $100 non-excess.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "LOW",
         "acquisition_date": "2021-01-01", "preceding_holding_years": 3,
         "received_distribution": True, "distribution_date": "2025-06-15",
         "distributions_by_year": {"2022": 2000, "2023": 2000, "2024": 2000,
                                   "2025": 100}},
    ]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "LOW", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 100}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE)
    assert failures == []


def test_pfic_recompute_partial_rates_incomplete_fails():
    # Codex round 31 #3: a rate cache missing one prior holding year cannot verify the
    # deferred tax — the fund must FAIL, not pass on a partial deferred. Fund held
    # 2020-2025; rate map omits 2021.
    partial = {2020: 0.37, 2022: 0.37, 2023: 0.37, 2024: 0.37}  # 2021 missing
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "GAP",
         "acquisition_date": "2020-01-01", "disposition_date": "2025-12-31",
         "disposed": True, "disposition_gain_usd": 120000}]}}
    import agents.lib.tax_computations as TC
    full = {yr: 0.37 for yr in range(2020, 2025)}
    exp = TC.compute_pfic_1291(120000, "2020-01-01", "2025-12-31", 2025, full,
                               is_disposition=True)["ordinary_income_current"]
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "GAP", "total_1291_addon": 50000,
                   "ordinary_income_current": exp, "disposition_ordinary_income": exp}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE,
                                        historical_top_rate=partial,
                                        underpayment_rates=_PFIC_6621)
    assert any("cannot be verified" in m and "GAP" in m for m in failures)


def test_pfic_recompute_negative_distribution_fails():
    # Codex round 32 #2: a NEGATIVE current-year distribution is malformed (distinct
    # from a negative disposition_gain_usd, a legit sale loss) and must FAIL.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "NEG",
         "acquisition_date": "2021-01-01", "received_distribution": True,
         "distributions_by_year": {"2025": -5000}}]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "NEG", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 0}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE)
    assert any("NEGATIVE current-year distribution" in m and "NEG" in m for m in failures)


def test_pfic_recompute_last_day_acquisition_needs_no_prior_rate():
    # Codex round 32 #4: a fund acquired on the LAST day of the prior tax year
    # (2024-12-31) allocates NOTHING to 2024 (holding starts 2025-01-01), so the
    # recompute must NOT demand a 2024 rate. A cache lacking 2024 still verifies (the
    # whole gain is current-year ordinary, $0 add-on).
    import agents.lib.tax_computations as TC
    rc = TC.compute_pfic_1291(50000, "2024-12-31", "2025-12-31", 2025,
                              {2025: 0.37}, underpayment_rates=_PFIC_6621,
                              is_disposition=True)
    assert rc["complete"] and rc["total_1291_addon"] == 0
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "EOY",
         "acquisition_date": "2024-12-31", "disposition_date": "2025-12-31",
         "disposed": True, "disposition_gain_usd": 50000}]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "EOY", "total_1291_addon": 0,
                   "ordinary_income_current": rc["ordinary_income_current"],
                   "disposition_ordinary_income": rc["ordinary_income_current"]}]}}
    # historical cache present but WITHOUT 2024 — must still pass.
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE,
                                        historical_top_rate={2025: 0.37},
                                        underpayment_rates=_PFIC_6621)
    assert failures == []


def test_pfic_recompute_band_is_whole_dollar_not_percentage():
    # Codex round 31 #4: the band is whole-dollar (the recompute reproduces the exact
    # figure), NOT 2% — a $1M current-year allocation reported at $980,000 must FAIL.
    import agents.lib.tax_computations as TC
    full = {yr: 0.37 for yr in range(2015, 2025)}
    exp = TC.compute_pfic_1291(6000000, "2015-01-01", "2025-12-31", 2025, full,
                               is_disposition=True)["ordinary_income_current"]
    assert exp > 500000  # ~ current-year allocation of a large multi-year gain
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "BIG",
         "acquisition_date": "2015-01-01", "disposition_date": "2025-12-31",
         "disposed": True, "disposition_gain_usd": 6000000}]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "BIG", "total_1291_addon": 900000,
                   "ordinary_income_current": exp - 20000,  # $20k short
                   "disposition_ordinary_income": exp - 20000}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE)
    assert any("does not match" in m and "BIG" in m for m in failures)


def test_pfic_recompute_nonzero_addon_when_recompute_zero_fails():
    # Codex round 33: a wholly-current-year event (acquired and sold the same year)
    # owes NO deferred tax — the recomputed add-on is $0. A nonzero total_1291_addon
    # adds §1291 tax the statute does not impose and must FAIL, even though the
    # expected add-on is zero.
    import agents.lib.tax_computations as TC
    rc = TC.compute_pfic_1291(30000, "2025-01-01", "2025-12-31", 2025, {2025: 0.37},
                              underpayment_rates=_PFIC_6621, is_disposition=True)
    assert rc["complete"] and rc["total_1291_addon"] == 0
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "SAMEYR",
         "acquisition_date": "2025-01-01", "disposition_date": "2025-12-31",
         "disposed": True, "disposition_gain_usd": 30000}]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "SAMEYR", "total_1291_addon": 100,
                   "ordinary_income_current": rc["ordinary_income_current"],
                   "disposition_ordinary_income": rc["ordinary_income_current"]}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE)
    assert any("does not match" in m and "SAMEYR" in m for m in failures)


def test_pfic_recompute_duplicate_fund_entries_fail():
    # Codex round 33: two entries for the same fund_id in form_8621_pfic.funds would
    # be summed by the aggregate/line-24 checks, doubling the fund's income + add-on,
    # while the per-fund recompute only sees the last. Detect + fail.
    profile = {"foreign_holdings_detail": {"holdings": [
        {"kind": "fund", "pfic_election": "none", "fund_id": "DUP",
         "acquisition_date": "2025-01-01", "disposed": True,
         "disposition_gain_usd": 30000}]}}
    result = {"meta": {"tax_year": 2025}, "form_8621_pfic": {
        "funds": [{"complete": True, "fund_id": "DUP", "total_1291_addon": 0,
                   "ordinary_income_current": 100},
                  {"complete": True, "fund_id": "DUP", "total_1291_addon": 0,
                   "ordinary_income_current": 100}]}}
    failures = T._pfic_recompute_checks(profile, result, 2025, T.DEFAULT_TOLERANCE)
    assert any("MORE THAN ONE entry" in m and "DUP" in m for m in failures)


def test_pfic_recompute_failures_emitted_by_evaluate():
    # evaluate appends passed-in recompute failures to pfic_incomplete and fails.
    recs = _ledger(_record())
    report = T.evaluate(recs, _result(), ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, any_taxable_pfic_recorded=True,
                        pfic_recompute_failures=["PFIC fund 'X': recompute mismatch"])
    assert report.failed and any("recompute mismatch" in m for m in report.pfic_incomplete)


def test_pfic_reclassified_qualified_dividend_ties_ledger():
    # Codex round 27: a PFIC dividend is NEVER qualified, so its nonexcess portion is
    # removed from line 3a but KEPT in line 3b as ordinary. The box-1b (qualified)
    # ledger reconciles via reclassified_from_line_3a — UNCAPPED, because the income
    # stays in line 3b (proven by the box-1a check) and recharacterizing qualified →
    # ordinary only RAISES tax. So no total_ordinary_income_current backing needed.
    result = _result()
    result["form_1040"]["line_3a_qualified_dividends"] = 600.0
    result["form_8621_pfic"] = {"reclassified_from_line_3a": 200.0}  # no Sch-1 backing
    assert T._result_total(result, "qualified_dividends") == 800.0


def test_pfic_agi_not_reconciled_fails():
    # Codex round 21: PFIC ordinary bolted onto line 15 but lines 9/11 unchanged.
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_15_taxable_income"] = 90000.0
    result["form_1040"]["line_9_total_income"] = 90000.0  # NOT increased
    result["form_1040"]["line_11_agi"] = 90000.0          # NOT increased
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "ordinary_income_current": 10000.0,
                   "total_1291_addon": 0, "nonexcess_ordinary": 0}],
        "total_ordinary_income_current": 10000.0, "total_nonexcess_ordinary": 0,
        "line_9_regular_total_income": 90000.0, "line_11_regular_agi": 90000.0,
        "line_15_regular_taxable_income": 80000.0,
        "line_15_pfic_ordinary_income": 10000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, expected_pfic_fund_ids={"A"},
                        any_taxable_pfic_recorded=True)
    assert report.failed and any("AGI" in m for m in report.pfic_incomplete)


def test_pfic_positive_event_with_zero_result_fails():
    # A recorded positive distribution but an all-zero fund result must FAIL.
    recs = _ledger(_record())
    result = _result()
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "DIST", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 0}]}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, expected_pfic_fund_ids={"DIST"},
                        any_taxable_pfic_recorded=True,
                        pfic_positive_event_ids={"DIST"})
    assert report.failed and any("ALL-ZERO" in m for m in report.pfic_incomplete)


def test_pfic_line15_may_grow_more_than_pfic_income():
    # Codex round 20: routing PFIC income through AGI can raise line 15 by MORE than
    # the PFIC amount (e.g. extra taxable SS). The >= reconciliation must accept it.
    recs = _ledger(_record())
    result = _result()
    # regular 80000 + 2000 nonexcess + 1500 downstream (extra taxable SS) = 83500
    result["form_1040"]["line_15_taxable_income"] = 83500.0
    # AGI also rises by the 2000 PFIC + 1500 extra taxable SS.
    result["form_1040"]["line_9_total_income"] = 93500.0
    result["form_1040"]["line_11_agi"] = 93500.0
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 2000.0}],
        "total_ordinary_income_current": 0, "total_nonexcess_ordinary": 2000.0,
        "line_9_regular_total_income": 90000.0, "line_11_regular_agi": 90000.0,
        "line_15_regular_taxable_income": 80000.0,
        "line_15_pfic_ordinary_income": 2000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, expected_pfic_fund_ids={"A"},
                        any_taxable_pfic_recorded=True)
    assert not report.pfic_incomplete and not report.failed


def test_pfic_line15_short_still_fails():
    # But line 15 SHORT of regular + PFIC income (omitted) still fails.
    recs = _ledger(_record())
    result = _result()
    result["form_1040"]["line_15_taxable_income"] = 80500.0  # only +500, need +2000
    result["form_8621_pfic"] = {
        "all_complete": True,
        "funds": [{"complete": True, "fund_id": "A", "total_1291_addon": 0,
                   "ordinary_income_current": 0, "nonexcess_ordinary": 2000.0}],
        "total_ordinary_income_current": 0, "total_nonexcess_ordinary": 2000.0,
        "line_15_regular_taxable_income": 80000.0,
        "line_15_pfic_ordinary_income": 2000.0}
    report = T.evaluate(recs, result, ["documents/W2_Acme.pdf"],
                        pfic_event_expected=True, expected_pfic_fund_ids={"A"},
                        any_taxable_pfic_recorded=True)
    assert report.failed and any("line 15" in m for m in report.pfic_incomplete)


# --------------------------------------------------------------------------
# write_report — the report lands in a restricted directory
# --------------------------------------------------------------------------

def test_write_report_restricts_the_summaries_directory(tmp_path):
    """`summaries/` holds one markdown file per source document, named after the
    document. 600 on `_tie_out.md` does nothing about a 755 directory listing
    `w2-acme.md` beside it. Pinned at umask 022 — under umask 077 mkdir's 0777
    request already yields 0700 and the assertion would pass unaided."""
    old = os.umask(0o022)
    try:
        T.write_report(tmp_path, "# tie-out\n")
    finally:
        os.umask(old)
    mode = (tmp_path / "summaries").stat().st_mode & 0o777
    assert mode == 0o700, f"summaries/ is {oct(mode)}, listing per-document filenames"


def test_write_report_fails_closed_when_directory_chmod_fails(tmp_path, monkeypatch):
    """A summaries/ that already exists with permissive permissions and can't be
    tightened must abort the write, not drop _tie_out.md into a listable
    directory — the tie-out HARD GATE has to fail loudly, not report success
    over an unsecured path.

    write_report() secures paths via secure_path() (agents/lib/secure_fs.py),
    which calls os.chmod() directly rather than the Path.chmod() method, so
    the failure is injected there — os is a singleton module, so patching it
    through any importer's `os` name (here tie_out.py's) reaches the same
    call secure_fs.py makes."""
    real_chmod = os.chmod

    def refuse(path, *args, **kwargs):
        if Path(path).name == "summaries":
            raise OSError("simulated chmod failure")
        return real_chmod(path, *args, **kwargs)

    monkeypatch.setattr("agents.lib.secure_fs.os.chmod", refuse)
    with pytest.raises(T.TieOutError, match="summaries"):
        T.write_report(tmp_path, "# tie-out\n")
    assert not (tmp_path / "summaries" / "_tie_out.md").exists()


def test_write_report_fails_closed_when_file_chmod_fails(tmp_path, monkeypatch):
    """Same guarantee on the report file itself: a chmod 600 that can't be
    applied must not leave a world-readable _tie_out.md behind silently — the
    file is created at mode 600 directly, but a chmod failure removes it
    rather than trusting whatever mode the creation call actually landed at."""
    real_chmod = os.chmod

    def refuse(path, *args, **kwargs):
        if "_tie_out.md" in Path(path).name:   # the temp beside it, too
            raise OSError("simulated chmod failure")
        return real_chmod(path, *args, **kwargs)

    monkeypatch.setattr("agents.lib.secure_fs.os.chmod", refuse)
    with pytest.raises(T.TieOutError, match="_tie_out.md"):
        T.write_report(tmp_path, "# tie-out\n")
    assert not (tmp_path / "summaries" / "_tie_out.md").exists(), \
        "chmod failure must not leave an unsecured report file behind"


def test_write_report_file_created_at_restricted_mode(tmp_path):
    """_tie_out.md is opened with mode 600 from creation, not written under
    the ambient umask and tightened afterward — closing the window where a
    freshly written report would sit at 644/664 until the following chmod."""
    old = os.umask(0o022)
    try:
        target = T.write_report(tmp_path, "# tie-out\n")
    finally:
        os.umask(old)
    assert target.stat().st_mode & 0o777 == 0o600


def test_write_report_refuses_symlinked_summaries_directory(tmp_path, tmp_path_factory):
    """summaries/ replaced with a symlink to an external directory must be
    refused before mkdir/chmod ever run — mkdir(exist_ok=True) accepts a
    symlink to an existing directory silently, and the chmod that follows
    would resolve through it before _validate_subpath's own check on the
    report path gets a chance to reject it as outside workspace."""
    external = tmp_path_factory.mktemp("external_summaries")
    (external / "private.txt").write_bytes(b"not the taxpayer's workspace")
    external_mode_before = external.stat().st_mode & 0o777

    (tmp_path / "summaries").symlink_to(external, target_is_directory=True)

    with pytest.raises(T.TieOutError, match="symlink"):
        T.write_report(tmp_path, "# tie-out\n")

    assert external.stat().st_mode & 0o777 == external_mode_before, \
        "chmod followed the symlink and mutated a directory outside the workspace"


def test_write_report_refuses_windows_junction_summaries_directory(tmp_path, monkeypatch):
    """summaries/ standing in for an NTFS junction must be refused the same
    way a symlinked summaries/ is (see test above) — Path.is_symlink()
    returns False for a junction (see secure_fs.py's module docstring), so
    this simulates one via is_reparse_point() directly on a real, plain
    directory, since a junction can't be created on POSIX. Patched at
    tie_out.py's own imported name (T.is_reparse_point), a `from X import Y`
    binding decoupled from secure_fs.py's own name once imported."""
    real_is_reparse_point = T.is_reparse_point

    def fake(path):
        if Path(path).name == "summaries":
            return True
        return real_is_reparse_point(path)

    monkeypatch.setattr("agents.lib.tie_out.is_reparse_point", fake)
    with pytest.raises(T.TieOutError, match="symlink or junction"):
        T.write_report(tmp_path, "# tie-out\n")


def _minimal_failing_workspace(tmp_path):
    """A workspace whose tie-out FAILS: the ledger claims a document that does
    not exist under documents/ (a phantom file), which the gate reports as a
    hard failure — exit 2, "return NOT complete"."""
    (tmp_path / "documents").mkdir()
    (tmp_path / "parse_ledger.json").write_text(json.dumps({"records": [
        {"doc_id": "w2-acme", "file": "documents/missing_W2.pdf",
         "form_type": "W-2", "payer": "Acme Corp", "person": "primary",
         "key_boxes": {"wages": 100000.0}, "skipped": None}]}))
    (tmp_path / "result.json").write_text(json.dumps({
        "meta": {"tax_year": 2026}, "form_1040": {"line_1a_wages": 100000.0}}))
    return tmp_path


def test_main_failing_gate_exits_2_even_when_the_report_cannot_be_written(
        tmp_path, monkeypatch, capsys):
    """A permissions fault must not mask a tax-correctness fault. write_report
    fails CLOSED on a chmod/write error, but if that raise happened before the
    verdict were printed, a FAILED gate (exit 2 = "do not mark the return
    complete") would be reported as exit 1 — which /tax-compute Step 12.7
    documents as "parse_ledger.json is missing or malformed", sending the agent
    to fix a ledger that is fine while the dropped-document failure is never
    printed at all."""
    ws = _minimal_failing_workspace(tmp_path)

    def exploding_write_report(workspace, markdown):
        raise T.TieOutError("Could not secure summaries (chmod 700): simulated")

    monkeypatch.setattr("agents.lib.tie_out.write_report", exploding_write_report)
    code = T._main(["--workspace", str(ws)])
    out = capsys.readouterr()
    assert code == 2, (
        "a FAILED tie-out must still exit 2 when only the report write failed")
    assert "FAILED" in out.out, "the verdict was suppressed by the write failure"
    assert "missing_W2.pdf" in out.out, "the failing document was never named"
    assert "Could not secure" in out.err, "the write failure went unreported"


def test_main_passing_gate_exits_1_when_the_report_cannot_be_written(
        tmp_path, monkeypatch, capsys):
    """The mirror case: an otherwise-clean tie-out whose report cannot be
    persisted is not a clean pass (exit 0) — write_report fails closed, so the
    run is an error (exit 1). The verdict is still printed."""
    ws = tmp_path
    (ws / "documents").mkdir()
    (ws / "parse_ledger.json").write_text(json.dumps({"records": []}))
    (ws / "result.json").write_text(json.dumps({"meta": {"tax_year": 2026}}))

    def exploding_write_report(workspace, markdown):
        raise T.TieOutError("Could not secure summaries (chmod 700): simulated")

    monkeypatch.setattr("agents.lib.tie_out.write_report", exploding_write_report)
    code = T._main(["--workspace", str(ws)])
    out = capsys.readouterr()
    assert code == 1, "a report that could not be written is not a clean pass"
    assert out.out.strip(), "the verdict was suppressed by the write failure"


def test_write_report_translates_an_uninspectable_summaries_path(tmp_path, monkeypatch):
    """is_reparse_point() fails CLOSED — every OSError but FileNotFoundError
    propagates — and this call sits outside any try, so without translation a
    PermissionError would escape write_report as a bare traceback past every
    caller's `except TieOutError`, including _main's."""
    def raising(path):
        raise PermissionError("simulated access denied")

    monkeypatch.setattr("agents.lib.tie_out.is_reparse_point", raising)
    with pytest.raises(T.TieOutError, match="could not be inspected"):
        T.write_report(tmp_path, "# tie-out\n")




# --------------------------------------------------------------------------
# The Earned Income Credit — the one dropped figure no check here can reach
# --------------------------------------------------------------------------
#
# Every other check in this module works by comparing the ledger to the
# return. A credit issues no source document, so a dropped EIC is invisible to
# all of them; these pin the advisory that carries the gap instead, and pin it
# as an ADVISORY — an under-statement degrades loudly under AGENTS.md §4, and
# a worksheet that legitimately comes out at $0 is a correct return this gate
# must never refuse.

def _eic_report(*, screening=True, nyc=False, **lines):
    result = _result(**{k: v for k, v in lines.items() if k.startswith("line_")})
    if "ny" in lines:
        result["new_york_it201"] = {
            "refundable_credits_and_payments": lines["ny"]}
        if nyc:
            result["new_york_it201"]["nyc"] = {"line_47_nyc_taxable_income": 0}
    return T.evaluate(_ledger(_record()), result, ["documents/W2_Acme.pdf"],
                      eic_screening=screening)


def _eic_advisories(report):
    return [a for a in report.advisories if "Earned Income Credit" in a]


def test_eic_advisory_fires_on_a_zero_federal_line():
    report = _eic_report(line_27_eic=0)
    assert not report.failed
    assert len(_eic_advisories(report)) == 1
    assert "Form 1040 line 27" in _eic_advisories(report)[0]


def test_eic_advisory_is_silent_when_the_taxpayer_screened_negative():
    """The flag is the taxpayer's own answer, and a `no` ends it. A gate that
    fired on every return regardless would be the cry-wolf shape: the reader
    learns to skip the advisory block, and the PFIC and second-read warnings
    that share it go with it."""
    assert _eic_advisories(_eic_report(screening=False, line_27_eic=0)) == []


def test_eic_advisory_fires_when_the_screening_question_is_unanswered():
    """Absence is not a `no` — nobody was asked. A profile predating the flag
    would otherwise get the silence this whole change exists to end, and there
    is no assertion there to defer to. `unanswered_pfic_event` in
    capabilities.py is the same shape at blocking level; this stays an advisory
    because the direction is an under-statement."""
    text = _eic_advisories(_eic_report(screening=None, line_27_eic=0))[0]
    assert "UNANSWERED" in text and "not a 'no'" in text


def test_eic_advisory_is_silent_once_a_figure_is_on_the_line():
    assert _eic_advisories(_eic_report(line_27_eic=3_400.0)) == []


def test_eic_advisory_never_fails_the_gate():
    """The whole point of the level. A worksheet that comes out at $0 for a
    filer whose investment income is over the cap is a CORRECT return, and a
    gate that refused it would teach the agent to route around the one warning
    that carries a thousands-of-dollars under-statement."""
    report = _eic_report(line_27_eic=0, nyc=True,
                         ny={"line_65_earned_income": 0,
                             "line_66_noncustodial_parent_eic": 0,
                             "line_70_nyc_earned_income": 0})
    assert not report.failed
    assert len(_eic_advisories(report)) == 1


def test_eic_advisory_names_all_four_lines_on_a_new_york_city_return():
    """FOUR, not three. An earlier draft named 65 and 70 and called the set
    complete, while `_NY_REFUNDABLE_CREDIT_LINES`' own comment names the pair
    that gets missed as the noncustodial parent EIC and the NYC EIC — this had
    picked up one of the two."""
    text = _eic_advisories(_eic_report(
        line_27_eic=0, nyc=True,
        ny={"line_65_earned_income": 0, "line_66_noncustodial_parent_eic": 0,
            "line_70_nyc_earned_income": 0}))[0]
    assert "Form 1040 line 27" in text
    assert "line 65" in text and "IT-215" in text
    assert "line 66" in text and "IT-209" in text
    assert "line 70" in text and "NYC" in text


def test_eic_advisory_omits_the_city_line_for_a_new_york_filer_outside_the_city():
    """`refundable_credits_and_payments` carries no `_include_if`; only the
    sibling `nyc` block is gated on flags.nyc_resident. Without the gate every
    New York filer was told to work a New York City credit — the cry-wolf
    shape, on the module whose advisory block also carries the PFIC and
    second-read warnings."""
    text = _eic_advisories(_eic_report(
        line_27_eic=0,
        ny={"line_65_earned_income": 0, "line_66_noncustodial_parent_eic": 0,
            "line_70_nyc_earned_income": 0}))[0]
    assert "line 65" in text and "line 66" in text
    assert "line 70" not in text and "NYC" not in text


def test_eic_advisory_omits_new_york_lines_on_a_return_that_has_none():
    """A return with no `new_york_it201` block is not a New York return, and
    naming IT-215 there sends the reader after a form they do not file.

    The REMEDY half is asserted too, and it is the half a mutation found
    unguarded: the authority clause says "each IT-201 line from the form named
    beside it", which contains neither IT-215 nor IT-209, so checking only the
    line list left the clause free to appear on a federal-only return."""
    text = _eic_advisories(_eic_report(line_27_eic=0))[0]
    assert "IT-215" not in text and "IT-209" not in text
    assert "IT-201" not in text
    # "not for New York" DOES belong here: it is the statement of what the
    # toolkit computes, true on every return, and not an instruction to go
    # anywhere. Only the remedy is scoped.


def test_eic_advisory_names_only_the_line_still_at_zero():
    text = _eic_advisories(_eic_report(
        line_27_eic=1_200.0,
        ny={"line_65_earned_income": 800.0,
            "line_66_noncustodial_parent_eic": 0}))[0]
    assert "Form 1040 line 27" not in text and "line 65" not in text
    assert "line 66" in text


def test_eic_each_line_is_sent_to_its_own_form_not_to_pub_596():
    """The over-statement this advisory could cause. A single federal remedy
    over a list that includes the New York lines invites the federal figure
    onto three New York lines — and the New York and City credits are not the
    federal credit. §4 calls an over-statement a hard error."""
    text = _eic_advisories(_eic_report(
        line_27_eic=0, nyc=True,
        ny={"line_65_earned_income": 0, "line_66_noncustodial_parent_eic": 0,
            "line_70_nyc_earned_income": 0}))[0]
    assert "OWN form" in text
    assert "Do NOT copy one line's figure onto another" in text
    assert "Form 1040 line 27 from the covered year's EIC worksheet" in text


def test_eic_a_null_on_the_line_counts_as_zero():
    """A null is not a figure anybody entered. Reading it as "something is on
    the line" is the direction that loses the credit."""
    assert len(_eic_advisories(_eic_report(line_27_eic=None))) == 1


def test_eic_a_non_finite_figure_counts_as_zero():
    """`json.loads` accepts NaN and Infinity, both pass isinstance, and every
    comparison with NaN is False (AGENTS.md §8) — so `value <= 0` alone would
    read a NaN line as a figure that is present."""
    for bad in (float("nan"), float("inf"), True):
        assert len(_eic_advisories(_eic_report(line_27_eic=bad))) == 1, bad


def test_eic_a_negative_figure_counts_as_zero():
    """An EIC is a credit and is never negative, so a minus sign is a sign
    error or a hand-edit, not a figure. `value == 0` let it silence the
    advisory. The federal line is refused upstream by
    return_builder._check_components; the three New York lines are not."""
    assert len(_eic_advisories(_eic_report(line_27_eic=-500.0))) == 1


def test_eic_an_absent_field_in_a_present_section_is_still_reported():
    """The opposite of what an earlier draft did, and `_result_total` one
    screen up settles the same question the same way: a present section always
    yields a number. Form 1040 always HAS a line 27 — only the JSON key is
    missing — and `return_builder.build` reads it as `working.get(field, 0)`,
    so it contributes a real $0 to line 33 either way."""
    report = T.evaluate(_ledger(_record()), {"form_1040": {}},
                        ["documents/W2_Acme.pdf"], eic_screening=True)
    assert len(_eic_advisories(report)) == 1


def test_eic_advisory_says_it_is_uncomputed_and_untieable():
    """The two facts a reader needs and cannot get anywhere else: nothing
    computes it, and nothing downstream will notice its absence."""
    text = _eic_advisories(_eic_report(line_27_eic=0))[0]
    assert "no source document" in text
    assert "may_qualify_for_eic" in text


def test_eic_lines_are_real_paths_in_the_result_template():
    """A path that resolves to nothing reports nothing for ever, and every
    fixture in this file is hand-built, so the constant and the fixtures could
    drift from the template together. Same check `_EIC_LINES`' neighbours get
    in `test_category_targets_fields_exist_in_result_template`."""
    template = json.loads(
        (REPO / "agents/templates/result_template.json").read_text())
    for label, path, gate in T._EIC_LINES:
        node = template
        for key in path[:-1]:
            assert isinstance(node, dict) and key in node, (label, key)
            node = node[key]
        assert path[-1] in node, (label, path[-1])
        if gate is not None:
            g = template
            for key in gate:
                assert isinstance(g, dict) and key in g, (label, key)
                g = g[key]


def test_every_eic_line_in_the_new_york_credit_block_is_covered():
    """DERIVED, not listed — a mutation over `_EIC_LINES` probes the members
    present and can never find the member absent, which is exactly how line 66
    was missed. Every refundable New York line whose NAME says it is an earned
    income credit must have an entry; New York adding one goes red here."""
    template = json.loads(
        (REPO / "agents/templates/result_template.json").read_text())
    block = template["new_york_it201"]["refundable_credits_and_payments"]
    named = {k for k in block
             if not k.startswith("_") and ("earned_income" in k or "eic" in k)}
    assert named, "the template's New York credit block named no EIC line"
    covered = {path[-1] for _, path, _ in T._EIC_LINES}
    assert named <= covered, (
        f"{sorted(named - covered)} are earned-income-credit lines in "
        "result_template.json's New York refundable block with no _EIC_LINES "
        "entry — an uncomputed input defaulting to 0 that this gate cannot "
        "see")


def test_the_tie_out_reads_the_flag_the_capability_rule_is_keyed_on():
    """Two modules spell one flag and nothing linked them: renaming it in the
    template and the rule left the suite green while this gate read a key that
    no longer existed — the disclosure half silently stopping, in the
    under-statement direction, with the capability half still passing."""
    from agents.lib import capabilities as C
    flag = C._RULES_BY_ID["earned_income_credit"].flag
    source = (REPO / "agents" / "lib" / "tie_out.py").read_text()
    assert f'flags.get("{flag}")' in source, (
        f"tie_out.py does not read flags.{flag}, which is what the "
        "`earned_income_credit` capability rule is keyed on")


def test_eic_cli_reads_the_screening_flag_from_the_profile(tmp_path):
    """The flag reaches the gate through the profile, like every other
    profile-derived input here — a parameter nothing in _main sets is a
    feature that works only in tests."""
    (tmp_path / "parse_ledger.json").write_text(json.dumps(
        {"records": [_record()]}))
    (tmp_path / "result.json").write_text(json.dumps(_result(line_27_eic=0)))
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "W2_Acme.pdf").write_bytes(b"%PDF-1.4\n")
    profile = tmp_path / "taxpayer_profile.json"

    def run():
        return subprocess.run(
            [sys.executable, str(REPO / "agents/lib/tie_out.py"),
             "--workspace", str(tmp_path)], capture_output=True, text=True)

    profile.write_text(json.dumps({"flags": {"may_qualify_for_eic": True}}))
    out = run()
    assert out.returncode == 0 and "screens POSITIVE" in out.stdout

    profile.write_text(json.dumps({"flags": {"may_qualify_for_eic": False}}))
    out = run()
    assert out.returncode == 0 and "Earned Income Credit" not in out.stdout

    # A profile that predates the question is UNANSWERED, not a no.
    profile.write_text(json.dumps({"flags": {}}))
    out = run()
    assert out.returncode == 0 and "UNANSWERED" in out.stdout


def test_eic_a_deleted_new_york_credit_block_is_still_reported():
    """An EMPTIED credit block already warned; a DELETED one went silent, which
    made deleting the block the quieter way to lose all three New York lines.
    Scoped by the return's top-level section instead."""
    report = T.evaluate(
        _ledger(_record()),
        {**_result(line_27_eic=500.0), "new_york_it201": {}},
        ["documents/W2_Acme.pdf"], eic_screening=True)
    text = _eic_advisories(report)[0]
    assert "line 65" in text and "line 66" in text


# --------------------------------------------------------------------------
# AA29: a flagged New York City or Yonkers line that nobody computed, and a
# line 58 that left one out. Line 58 read a missing 47a, 55 or 56 as $0, which
# under-states what the return owes; `compute_city_and_yonkers_total` refuses
# that only for a total computed through it, so the gate checks the result
# every route to line 58 lands in.
# --------------------------------------------------------------------------

_NY_FLAGS = {"nyc_resident": True, "yonkers_resident": True,
             "yonkers_nonresident_earnings": True}


def _ny_result(nyc=None, yonkers=None, mctmt=None, line_58=2074.0):
    ny = {"line_58_total_city_and_yonkers": line_58}
    for name, block in (("nyc", nyc), ("yonkers", yonkers), ("mctmt", mctmt)):
        if block is not None:
            ny[name] = block
    return {**_result(), "new_york_it201": ny}


def _ny_report(result, flags):
    return T.evaluate(_ledger(_record()), result, ["documents/W2_Acme.pdf"],
                      profile_flags=flags)


def _computed():
    # Line 54 is 47a after the City credits (none here); line 58 = 54 + 55 +
    # 56 = 1,292 + 782 + 0 = 2,074.
    # Each line carries the `method` its function returned (AA32).
    return {
        "nyc": {"line_47a_nyc_resident_tax": 1292.0,
                "line_47a_nyc_resident_tax_method": "table",
                "line_54_after_city_credits": 1292.0},
        "yonkers": {"line_55_yonkers_resident_surcharge": 782.0,
                    "line_55_yonkers_resident_surcharge_method": "worksheet",
                    "line_56_yonkers_nonresident_earnings_tax": 0,
                    "line_56_yonkers_nonresident_earnings_tax_method":
                        "form_y203"},
    }


def test_ny_household_lines_computed_pass_including_a_computed_zero():
    """A computed $0 is a figure: Y-203's line 6 can be zero."""
    report = _ny_report(_ny_result(**_computed()), _NY_FLAGS)
    assert not report.failed, report.ny_city_and_yonkers
    assert report.ny_city_and_yonkers == ()


@pytest.mark.parametrize("flag,_arg,block,field,line", T.NY_HOUSEHOLD_LINES)
@pytest.mark.parametrize("value", [None, "absent", float("nan"), True, -1.0,
                                   "1,292"])
def test_a_flagged_line_without_a_figure_fails_the_gate(flag, _arg, block,
                                                         field, line, value):
    blocks = _computed()
    if value == "absent":
        del blocks[block][field]
    else:
        blocks[block][field] = value
    report = _ny_report(_ny_result(**blocks), {flag: True})
    assert report.failed
    (finding,) = report.ny_city_and_yonkers
    assert f"line {line}" in finding and f"flags.{flag}" in finding
    assert "Never enter 0" in finding
    assert "not computed or not totalled" in T.render_markdown(report)


@pytest.mark.parametrize("flag,_arg,block,field,line", T.NY_HOUSEHOLD_LINES)
def test_a_flagged_figure_without_its_method_fails_the_gate(flag, _arg, block,
                                                            field, line):
    """AA32: a 0 typed over the template's null is a figure, so the figure
    check passed it as computed. The method its function returned is what a
    typed figure lacks — absent, null, made up, or another line's."""
    others = {m for f, ms in T.NY_HOUSEHOLD_METHODS.items() if f != field
              for m in ms}
    for method in ("absent", None, "typed", ["table"], {"m": 1},
                   *sorted(others)):
        blocks = _computed()
        blocks[block][field] = 0
        if method == "absent":
            del blocks[block][f"{field}_method"]
        else:
            blocks[block][f"{field}_method"] = method
        report = _ny_report(_ny_result(**blocks), {flag: True})
        assert report.failed, (field, method)
        (finding,) = report.ny_city_and_yonkers
        assert f"line {line}" in finding and f"{field}_method" in finding
    blocks = _computed()
    blocks[block][field] = 0
    assert not _ny_report(_ny_result(**blocks), {flag: True}).failed, (
        "a computed 0 with its method is a computed 0")


def test_a_flagged_line_whose_block_was_pruned_fails_the_gate():
    report = _ny_report(_ny_result(line_58=0), {"nyc_resident": True})
    assert report.failed
    assert "line 47a" in report.ny_city_and_yonkers[0]


@pytest.mark.parametrize("flag_value", ["true", 1, "yes"])
def test_a_household_flag_that_is_not_a_boolean_fails_the_gate(flag_value):
    """Skipping it read an unanswered flag as a no: the under-statement."""
    report = _ny_report(_ny_result(**_computed()),
                        {**_NY_FLAGS, "nyc_resident": flag_value})
    assert report.failed
    (finding,) = report.ny_city_and_yonkers
    assert "flags.nyc_resident" in finding and "not true or false" in finding


@pytest.mark.parametrize("line_58", [0, 1292.0, None, "2,074"])
def test_a_line_58_that_left_a_term_out_fails_the_gate(line_58):
    """Every line computed and line 58 short of 54 + 55 + 56: the total typed
    by hand, or assembled without one of the taxes."""
    report = _ny_report(_ny_result(**_computed(), line_58=line_58), _NY_FLAGS)
    assert report.failed
    (finding,) = report.ny_city_and_yonkers
    assert "line 58" in finding and "2,074.00" in finding


def test_the_mctmt_counts_toward_line_58_from_its_own_block():
    """54e is added after both City credit caps, like the Yonkers taxes."""
    blocks = {**_computed(), "mctmt": {"line_54e_total_mctmt": 500.0}}
    assert _ny_report(_ny_result(**blocks), _NY_FLAGS).failed
    assert not _ny_report(_ny_result(**blocks, line_58=2574.0),
                          _NY_FLAGS).failed


def test_a_line_58_that_says_more_passes():
    """Over the floor is the loud direction; the floor is not the form."""
    assert not _ny_report(_ny_result(**_computed(), line_58=2500.0),
                          _NY_FLAGS).failed


def test_an_unflagged_household_line_left_null_is_not_a_finding():
    """The template ships the lines null, and a household that does not owe
    one leaves it there."""
    nulls = {"nyc": {"line_47a_nyc_resident_tax": None},
             "yonkers": {"line_55_yonkers_resident_surcharge": None,
                         "line_56_yonkers_nonresident_earnings_tax": None}}
    report = _ny_report(_ny_result(**nulls, line_58=0),
                        {"nyc_resident": False, "yonkers_resident": False})
    assert not report.failed


def test_no_new_york_return_and_no_profile_ask_nothing():
    assert not _ny_report(_result(), _NY_FLAGS).failed
    assert not _ny_report(_ny_result(nyc={}, line_58=0), None).failed


def test_the_template_ships_the_household_lines_null():
    """A 0 default is the hazard: a line nobody computed read as a computed
    zero. null stays visibly uncomputed and is what the gate refuses."""
    template = json.loads((REPO / "agents/templates/result_template.json")
                          .read_text())["new_york_it201"]
    for _flag, _arg, block, field, _line in T.NY_HOUSEHOLD_LINES:
        assert template[block][field] is None, field
        assert template[block][f"{field}_method"] is None, field


def test_ny_household_cli_reads_the_flags_from_the_profile(tmp_path):
    """The flags reach the gate through the profile, and the verdict is the
    gate's exit 2 — a parameter only tests set is a check nothing runs."""
    (tmp_path / "parse_ledger.json").write_text(json.dumps(
        {"records": [_record()]}))
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "W2_Acme.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "result.json").write_text(json.dumps(
        _ny_result(nyc={"line_47a_nyc_resident_tax": None}, line_58=0)))
    profile = tmp_path / "taxpayer_profile.json"

    profile.write_text(json.dumps({"flags": {"nyc_resident": True}}))
    out = _run_cli(tmp_path)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "line 47a" in out.stdout

    profile.write_text(json.dumps({"flags": {"nyc_resident": False}}))
    out = _run_cli(tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr

    profile.write_text(json.dumps({"flags": {"nyc_resident": True}}))
    out = _run_cli(tmp_path, "--json")
    assert out.returncode == 2
    assert json.loads(out.stdout)["ny_city_and_yonkers"]


@pytest.mark.parametrize("location", ["flag", "figure", "method", "total"])
@pytest.mark.parametrize("shape", ["text", "list", "object"])
@pytest.mark.parametrize("output_args", [(), ("--json",)])
def test_ny_rejected_runtime_contents_stay_private_in_cli(
        tmp_path, location, shape, output_args):
    sentinel = "SYNTHETIC_PRIVATE_TEXT"
    invalid = {"text": sentinel, "list": [sentinel],
               "object": {"private": sentinel}}[shape]
    result = _ny_result(**_computed())
    flags = dict(_NY_FLAGS)
    if location == "flag":
        flags["nyc_resident"] = invalid
    elif location == "figure":
        result["new_york_it201"]["nyc"]["line_47a_nyc_resident_tax"] = invalid
    elif location == "method":
        result["new_york_it201"]["nyc"][
            "line_47a_nyc_resident_tax_method"] = invalid
    else:
        result["new_york_it201"]["line_58_total_city_and_yonkers"] = invalid
    (tmp_path / "parse_ledger.json").write_text(json.dumps(
        {"records": [_record()]}))
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "W2_Acme.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "result.json").write_text(json.dumps(result))
    (tmp_path / "taxpayer_profile.json").write_text(json.dumps({"flags": flags}))

    out = _run_cli(tmp_path, *output_args)
    assert out.returncode == 2
    assert "IT-201 line" in out.stdout
    assert sentinel not in out.stdout + out.stderr



def test_an_acknowledged_refused_household_is_not_held_to_the_engines_line():
    """AA30: a part-year City resident who acknowledged the gate's block
    prepares the City tax by hand on line 50 (IT-360.1), not 47a. Without the
    acknowledgement the line is still required."""
    flags = {"nyc_resident": True, "nyc_resident_full_year": False}
    profile = {"filing_status": "single", "flags": flags,
               "acknowledged_limitations":
                   ["ny_city_or_yonkers_household_unmodeled"]}
    result = _ny_result(nyc={"line_47a_nyc_resident_tax": None}, line_58=0)
    exempted = T._flags_for_city_and_yonkers(profile, flags)
    assert not _ny_report(result, exempted).failed
    profile["acknowledged_limitations"] = []
    assert _ny_report(result,
                      T._flags_for_city_and_yonkers(profile, flags)).failed
