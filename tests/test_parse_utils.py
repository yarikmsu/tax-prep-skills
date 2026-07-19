"""Tests for agents/lib/parse_utils.py — document classification."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib.parse_utils import classify_document  # noqa: E402


def test_classify_form_3921():
    """D9 review follow-up: Form 3921 (ISO exercise) must classify so the
    bargain element enters the parse ledger instead of staying a user
    assertion invisible to the tie-out."""
    assert classify_document(
        "Form 3921 — Exercise of an Incentive Stock Option Under "
        "Section 422(b)") == "3921"
    assert classify_document(
        "EXERCISE OF AN INCENTIVE STOCK OPTION\nCopy B for Employee") == "3921"


def test_classify_3921_beats_tax_return_fallthrough():
    # Some 3921 layouts mention Form 1040 in the filing instructions — the
    # 3921 pattern must win over the tax_return classification.
    text = ("FORM 3921 Exercise of an Incentive Stock Option — report any "
            "adjustment on Form 1040 (Form 6251)")
    assert classify_document(text) == "3921"


def test_classify_form_3922():
    """J2: Form 3922 (ESPP §423 purchase info) must classify so the
    purchase-date boxes enter the ledger for the disposition split and future
    basis math."""
    assert classify_document(
        "Form 3922 — Transfer of Stock Acquired Through an Employee Stock "
        "Purchase Plan Under Section 423(c)") == "3922"
    assert classify_document(
        "EMPLOYEE STOCK PURCHASE PLAN UNDER SECTION 423\nCopy B") == "3922"


def test_classify_3922_beats_1099b_cost_basis_shadow():
    # A broker-issued 3922 packet mentions "COST BASIS" (the 1099-b keyword) in
    # its cover text — the 3922 pattern must win, exactly as 3921 does.
    text = ("FORM 3922 Transfer of Stock Acquired Through an Employee Stock "
            "Purchase Plan — see your COST BASIS records")
    assert classify_document(text) == "3922"


def test_classify_existing_types_unchanged():
    assert classify_document("FORM W-2 Wage and Tax Statement") == "w2"
    assert classify_document("Form 1099-INT Interest Income") == "1099-int"
    assert classify_document("") == "unknown"
    assert classify_document(None) == "unknown"


def test_classify_3921_beats_cost_basis_shadow():
    """Review round: broker-issued 3921 packets mention 'cost basis' in
    cover text — the generic 1099-b keyword must not shadow the specific
    3921 classification."""
    text = ("EXERCISE OF AN INCENTIVE STOCK OPTION Under Section 422(b). "
            "Your cost basis information is included for your records.")
    assert classify_document(text) == "3921"
