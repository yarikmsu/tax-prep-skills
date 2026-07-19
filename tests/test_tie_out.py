"""Tests for the parse-to-return tie-out (agents/lib/tie_out.py, Epic A).

The invariant under test: a parsed document that never landed on a return
line is DETERMINISTICALLY caught, not hopefully noticed.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import tie_out as T  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


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
    f = {"line_1z_total_wages": wages, "line_25a_w2_withholding": w2_wh}
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
        if cat == "capital_proceeds":
            continue  # detail-row scan, not fixed field names
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
    from pathlib import Path
    from agents.lib.tie_out import CATEGORY_TARGETS, _INFORMATIONAL
    skill = (Path(__file__).resolve().parent.parent
             / "agents/skills/tax-parse.md").read_text()
    for cat in sorted(set(CATEGORY_TARGETS) | set(_INFORMATIONAL)):
        assert f"`{cat}`" in skill, (
            f"tie-out category {cat!r} is registered but never documented "
            f"in agents/skills/tax-parse.md — parse subagents can't emit "
            f"it, so the tie never fires (register both halves or neither)")
