"""Tests for agents/lib/amend.py — 1040-X three-column engine (F3)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import amend as AM  # noqa: E402


def _result(agi=100000, deductions=30000, qbi=0, taxable=70000, tax=12000,
            credits=2000, other=0, total_tax=10000, wh=9000, est=0,
            payments=None, overpayment=0, sched_1a=0):
    payments = wh + est if payments is None else payments
    return {
        "meta": {"limitations": ["State: CA computed in depth."]},
        "form_1040": {
            "line_11_agi": agi,
            "line_12_standard_deduction": deductions,
            "line_13a_qbi_deduction": qbi,
            "line_13b_schedule_1a_deductions": sched_1a,
            "line_14_total_deductions": deductions + qbi + sched_1a,
            "line_15_taxable_income": taxable,
            "line_16_tax": tax,
            "line_21_total_credits": credits,
            "line_23_other_taxes": other,
            "line_24_total_tax": total_tax,
            "line_25d_total_withholding": wh,
            "line_26_estimated_payments": est,
            "line_33_total_payments": payments,
            "line_34_overpayment": overpayment,
        },
    }


def test_three_column_rows_hand_computed():
    # Corrected 1099 adds $2,000 dividends: AGI 100k -> 102k, taxable
    # 70k -> 72k, tax 12k -> 12.44k, total tax 10k -> 10.44k.
    orig = _result()
    corr = _result(agi=102000, taxable=72000, tax=12440, total_tax=10440)
    cmp = AM.three_column(orig, corr)
    by_line = {r["line"]: r for r in cmp["rows"]}
    assert by_line["1"] == {"line": "1", "label": "Adjusted gross income",
                            "original": 100000.0, "change": 2000.0,
                            "corrected": 102000.0}
    assert by_line["11"]["change"] == 440.0


def test_charitable_non_itemizer_maps_to_1040x_line_4c():
    # Codex (PR #46): a §170(p) line-13c change must surface as 1040-X line 4c,
    # so the 1 − 2 − 4a − 4b − 4c = 5 arithmetic reconciles for the amendment.
    orig = _result()
    orig["form_1040"]["line_13c_charitable_non_itemizer"] = 0
    corr = _result(taxable=68000)
    corr["form_1040"]["line_13c_charitable_non_itemizer"] = 2000
    by_line = {r["line"]: r for r in AM.three_column(orig, corr)["rows"]}
    assert "4c" in by_line
    assert by_line["4c"]["change"] == 2000.0
    assert by_line["4c"]["corrected"] == 2000.0


def test_balance_mechanics_owed_after_original_refund():
    """Original: tax 10k, payments 11k -> 1k refund received.
    Corrected: tax 10.44k, payments unchanged 11k, paid nothing with
    original. Line 16 = 11k, line 17 = 1k, line 18 = 10k ->
    owed = 10,440 - 10,000 = 440."""
    orig = _result(payments=11000, overpayment=1000)
    corr = _result(agi=102000, taxable=72000, tax=12440, total_tax=10440,
                   payments=11000)
    cmp = AM.three_column(orig, corr)
    assert cmp["line_16_paid_with_original"] == 0.0
    assert cmp["line_17_total_payments"] == 11000.0
    assert cmp["line_18_original_overpayment"] == 1000.0
    assert cmp["line_19_net_payments"] == 10000.0
    assert cmp["amount_owed"] == 440.0
    assert cmp["refund"] == 0.0


def test_balance_mechanics_refund_with_paid_with_original():
    """Original balance-due return: tax 10k, payments 9k, taxpayer PAID
    the $1k with the return. Correction lowers tax to 9.5k. Line 16 =
    9k + 1k = 10k, line 17 = 0 -> refund = 10k - 9.5k = 500."""
    orig = _result(payments=9000)
    corr = _result(total_tax=9500, payments=9000)
    cmp = AM.three_column(orig, corr, paid_with_original=1000)
    assert cmp["line_16_paid_with_original"] == 1000.0
    assert cmp["line_17_total_payments"] == 10000.0
    assert cmp["refund"] == 500.0
    assert cmp["amount_owed"] == 0.0


def test_line_15_refundable_credits_derived():
    # payments 12k = wh 9k + est 0 + 3k refundable -> line 15 = 3k.
    orig = _result(payments=12000)
    corr = _result(payments=12500)
    by_line = {r["line"]: r for r in AM.three_column(orig, corr)["rows"]}
    assert by_line["15"]["original"] == 3000.0
    assert by_line["15"]["corrected"] == 3500.0


def test_uncomputed_side_rejected():
    good = _result()
    bad = _result()
    bad["meta"]["limitations"] = []
    with pytest.raises(AM.AmendError):
        AM.three_column(bad, good)
    with pytest.raises(AM.AmendError):
        AM.three_column(good, bad)


def test_paid_with_original_validation():
    for bad in (float("nan"), -1, True, "1000"):
        with pytest.raises(AM.AmendError):
            AM.three_column(_result(), _result(), paid_with_original=bad)


def test_ledger_changes_added_removed_changed():
    before = {"records": [
        {"file": "w2.pdf", "payer": "Acme",
         "key_boxes": {"wages": 100000, "federal_withholding_w2": 9000}},
        {"file": "old.pdf", "payer": "Gone Bank",
         "key_boxes": {"taxable_interest": 50}},
    ]}
    after = {"records": [
        {"file": "w2.pdf", "payer": "Acme",
         "key_boxes": {"wages": 100000, "federal_withholding_w2": 9500}},
        {"file": "div_corrected.pdf", "payer": "Broker X",
         "key_boxes": {"ordinary_dividends": 2000}},
    ]}
    bullets = AM.ledger_changes(before, after)
    joined = "\n".join(bullets)
    assert "ADDED document div_corrected.pdf (Broker X)" in joined
    assert "REMOVED document old.pdf (Gone Bank)" in joined
    assert "CHANGED w2.pdf (Acme) federal_withholding_w2: 9000 → 9500" in joined
    assert "wages" not in joined.replace("wages: 100000", "")  # unchanged


def test_ledger_no_changes_points_to_answers():
    l = {"records": [{"file": "a.pdf", "key_boxes": {"wages": 1}}]}
    bullets = AM.ledger_changes(l, l)
    assert any("answers.md" in b for b in bullets)


def test_render_markdown_shape():
    cmp = AM.three_column(_result(payments=11000, overpayment=1000),
                          _result(total_tax=10440, payments=11000))
    md = AM.render_markdown(cmp, ["ADDED document x.pdf"])
    assert "| 1 | Adjusted gross income |" in md
    assert "AMOUNT OWED" in md
    assert "Part II" in md
    assert "do not self-compute" in md


REPO = Path(__file__).resolve().parent.parent


def _write_workspace(tmp_path, orig, corr):
    as_filed = tmp_path / "archive" / "2025" / "as_filed"
    as_filed.mkdir(parents=True)
    (as_filed / "result.json").write_text(json.dumps(orig))
    (tmp_path / "result.json").write_text(json.dumps(corr))


def test_cli_end_to_end(tmp_path):
    _write_workspace(tmp_path, _result(payments=11000, overpayment=1000),
                     _result(total_tax=10440, payments=11000))
    out = subprocess.run(
        [sys.executable, "-m", "agents.lib.amend", "--workspace",
         str(tmp_path), "--year", "2025", "--json"],
        capture_output=True, text=True, cwd=REPO)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["comparison"]["amount_owed"] == 440.0
    assert any("Parse ledger missing" in b for b in data["part_ii_bullets"])


def test_cli_missing_snapshot_errors(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps(_result()))
    out = subprocess.run(
        [sys.executable, "-m", "agents.lib.amend", "--workspace",
         str(tmp_path), "--year", "2025"],
        capture_output=True, text=True, cwd=REPO)
    assert out.returncode == 1
    assert "as-filed" in out.stderr


def test_line_2_is_deduction_only_no_qbi_double_count():
    """1040-X line 2 = 1040 line 12 (std/itemized) ONLY; QBI is line 4a.
    Mapping line 2 to line_14_total_deductions would double-count QBI and
    break 1−2−4a=5."""
    orig = _result(deductions=27700, qbi=5000, agi=100000, taxable=67300)
    corr = _result(deductions=27700, qbi=6000, agi=100000, taxable=66300)
    by_line = {r["line"]: r for r in AM.three_column(orig, corr)["rows"]}
    # Line 2 shows the deduction WITHOUT qbi; line 4a shows qbi.
    assert by_line["2"]["original"] == 27700.0
    assert by_line["4a"]["original"] == 5000.0
    assert by_line["4a"]["change"] == 1000.0
    # AGI(1) − deduction(2) − qbi(4a) reconciles to taxable(5).
    assert (by_line["1"]["corrected"] - by_line["2"]["corrected"]
            - by_line["4a"]["corrected"]) == by_line["5"]["corrected"]


def test_line_4b_schedule_1a_row():
    """Form 1040-X line 4b (added Dec 2025) carries the OBBBA Schedule 1-A
    deduction; the taxable-income arithmetic is 1 − 2 − 4a − 4b = 5."""
    orig = _result(deductions=27700, qbi=5000, sched_1a=0, agi=100000, taxable=67300)
    corr = _result(deductions=27700, qbi=5000, sched_1a=8000, agi=100000, taxable=59300)
    by_line = {r["line"]: r for r in AM.three_column(orig, corr)["rows"]}
    assert by_line["4b"]["original"] == 0.0
    assert by_line["4b"]["corrected"] == 8000.0
    assert by_line["4b"]["change"] == 8000.0
    assert (by_line["1"]["corrected"] - by_line["2"]["corrected"]
            - by_line["4a"]["corrected"] - by_line["4b"]["corrected"]
            == by_line["5"]["corrected"])
