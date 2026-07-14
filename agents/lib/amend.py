#!/usr/bin/env python3
"""Amended-return (Form 1040-X) three-column engine for /tax-amend (F3).

Pure compute: compares the AS-FILED snapshot (archive/{year}/as_filed/,
written by /tax-amend step 1 before any correction) against the CURRENT
recomputed result.json, and renders the 1040-X three-column summary
(original / net change / corrected) plus Part II explanation bullets
derived from the parse-ledger diff — A1's ledger is what makes "what
changed" computable instead of narrated.

Read-only: never writes any file. The skill owns the snapshot copy and the
re-parse/re-compute; this module only diffs and renders.

Line mapping targets the CURRENT Form 1040-X layout (lines 1-23). The
balance mechanics deliberately require `paid_with_original` as an input —
line 16 includes amounts paid with the original return, which result.json
cannot know.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_subpath,
)


class AmendError(Exception):
    """Raised when the 1040-X comparison cannot safely proceed."""


# 1040-X line → (label, form_1040 field). Lines whose official definition
# has no single result field are computed below, not mapped here.
_1040X_LINES = [
    ("1", "Adjusted gross income", "line_11_agi"),
    # 1040-X line 2 is 1040 line 12 (standard OR itemized deduction) ONLY —
    # NOT line_14_total_deductions (= line 12 + line 13 QBI), which would
    # double-count QBI against line 4a and break the 1−2−4a=5 arithmetic.
    ("2", "Itemized deductions or standard deduction", "line_12_standard_deduction"),
    ("4a", "Qualified business income deduction", "line_13a_qbi_deduction"),
    ("5", "Taxable income", "line_15_taxable_income"),
    ("6", "Tax", "line_16_tax"),
    ("7", "Nonrefundable credits", "line_21_total_credits"),
    ("10", "Other taxes", "line_23_other_taxes"),
    ("11", "Total tax", "line_24_total_tax"),
    ("12", "Withholding", "line_25d_total_withholding"),
    ("13", "Estimated tax payments", "line_26_estimated_payments"),
]


def _num(section: dict, field: str) -> float:
    v = (section or {}).get(field)
    if v is None:
        return 0.0
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise AmendError(f"form_1040.{field} must be numeric, got {v!r}")
    f = float(v)
    if not math.isfinite(f):
        raise AmendError(f"form_1040.{field} must be finite, got {v!r}")
    return f


def _computed_1040(result: dict, which: str) -> dict:
    if not isinstance(result, dict):
        raise AmendError(f"{which} result must be a JSON object")
    if not (result.get("meta") or {}).get("limitations"):
        raise AmendError(
            f"{which} result.json shows no compute evidence "
            f"(meta.limitations empty) — not a computed return")
    f1040 = result.get("form_1040")
    if not isinstance(f1040, dict):
        raise AmendError(f"{which} result.json has no form_1040 section")
    return f1040


def three_column(original: dict, corrected: dict,
                 paid_with_original: float = 0.0) -> dict:
    """Form 1040-X columns A (original) / B (net change) / C (corrected),
    plus the refund-or-owe mechanics (lines 16-22).

    `paid_with_original` = tax paid WITH the original return (balance due
    remitted, NOT withholding/estimates — those are in lines 12-13); it
    belongs on 1040-X line 16 and result.json cannot know it.
    """
    if isinstance(paid_with_original, bool) or not isinstance(
            paid_with_original, (int, float)):
        raise AmendError("paid_with_original must be numeric")
    if not math.isfinite(float(paid_with_original)) or paid_with_original < 0:
        raise AmendError(
            f"paid_with_original must be a finite non-negative number, "
            f"got {paid_with_original!r}")
    f_orig = _computed_1040(original, "as-filed")
    f_corr = _computed_1040(corrected, "corrected")

    rows = []
    for line, label, field in _1040X_LINES:
        a = _num(f_orig, field)
        c = _num(f_corr, field)
        rows.append({"line": line, "label": label,
                     "original": round(a, 2),
                     "change": round(c - a, 2),
                     "corrected": round(c, 2)})

    # Line 15: refundable credits + other payments — everything in line 33
    # that isn't withholding (12) or estimates (13).
    ref_orig = _num(f_orig, "line_33_total_payments") - _num(
        f_orig, "line_25d_total_withholding") - _num(
        f_orig, "line_26_estimated_payments")
    ref_corr = _num(f_corr, "line_33_total_payments") - _num(
        f_corr, "line_25d_total_withholding") - _num(
        f_corr, "line_26_estimated_payments")
    rows.append({"line": "15", "label": "Refundable credits and other payments",
                 "original": round(ref_orig, 2),
                 "change": round(ref_corr - ref_orig, 2),
                 "corrected": round(ref_corr, 2)})

    # Balance mechanics — official Form 1040-X line numbers:
    #  16 = tax paid with the original return + any tax paid after filing
    #  17 = total payments = (corrected lines 12-15, i.e. result line 33) + 16
    #  18 = overpayment shown on (or applied from) the original return
    #  19 = 17 − 18 (net payments)
    #  20 = amount owed = corrected total tax (line 11C) − 19, if positive
    #  21 = overpaid    = 19 − corrected total tax, if positive
    line16 = float(paid_with_original)
    line17 = _num(f_corr, "line_33_total_payments") + line16
    line18 = _num(f_orig, "line_34_overpayment")
    line19 = line17 - line18
    corrected_total_tax = _num(f_corr, "line_24_total_tax")
    owed = round(max(0.0, corrected_total_tax - line19), 2)
    refund = round(max(0.0, line19 - corrected_total_tax), 2)

    return {
        "rows": rows,
        "line_16_paid_with_original": round(line16, 2),
        "line_17_total_payments": round(line17, 2),
        "line_18_original_overpayment": round(line18, 2),
        "line_19_net_payments": round(line19, 2),
        "corrected_total_tax": round(corrected_total_tax, 2),
        "amount_owed": owed,
        "refund": refund,
        "notes": [
            "Line 16 (paid with original) is the paid_with_original input — "
            "verify it equals tax actually remitted with (or after) the "
            "original return, excluding penalties and interest.",
            "Line 18 assumes the ENTIRE original overpayment was received "
            "or applied — reduce it manually if the IRS adjusted the "
            "original refund.",
            "Interest accrues on any amount owed from the ORIGINAL due "
            "date; the IRS bills it — do not self-compute.",
        ],
    }


def ledger_changes(as_filed_ledger: dict, current_ledger: dict) -> list:
    """Part II explanation bullets from the parse-ledger diff.

    Per-file comparison of the A1 ledger: added files (new/corrected
    documents), removed files, and per-category amount changes on files
    present in both. This is the computable 'what changed' the roadmap
    says A1 enables — the skill turns these into the 1040-X Part II
    'Explanation of changes' narrative.
    """
    def _records(ledger):
        if not isinstance(ledger, dict) or not isinstance(
                ledger.get("records"), list):
            raise AmendError("ledger must carry a records list")
        out = {}
        for r in ledger["records"]:
            if not isinstance(r, dict) or not isinstance(r.get("file"), str):
                raise AmendError("every ledger record needs a file string")
            out[r["file"]] = r
        return out

    before = _records(as_filed_ledger)
    after = _records(current_ledger)
    bullets = []
    for f in sorted(set(after) - set(before)):
        payer = after[f].get("payer") or "unknown payer"
        bullets.append(f"ADDED document {f} ({payer}) — new or corrected "
                       f"information return received after filing.")
    for f in sorted(set(before) - set(after)):
        payer = before[f].get("payer") or "unknown payer"
        bullets.append(f"REMOVED document {f} ({payer}) — original return "
                       f"included amounts this document no longer supports.")
    for f in sorted(set(before) & set(after)):
        kb_b = before[f].get("key_boxes") or {}
        kb_a = after[f].get("key_boxes") or {}
        for cat in sorted(set(kb_b) | set(kb_a)):
            b, a = kb_b.get(cat), kb_a.get(cat)
            if b != a:
                payer = after[f].get("payer") or before[f].get("payer") or "?"
                bullets.append(
                    f"CHANGED {f} ({payer}) {cat}: "
                    f"{b if b is not None else 'absent'} → "
                    f"{a if a is not None else 'absent'}.")
    if not bullets:
        bullets.append(
            "No parse-ledger differences found — if the amendment stems "
            "from a non-document change (filing status, dependents, an "
            "election), write the Part II explanation from answers.md.")
    return bullets


def _money(v: float) -> str:
    body = f"${abs(v):,.2f}"
    return f"({body})" if v < 0 else body


def render_markdown(comparison: dict, bullets: list) -> str:
    lines = ["# Form 1040-X — three-column summary", "",
             "| Line | Item | A. Original | B. Net change | C. Corrected |",
             "|---|---|---:|---:|---:|"]
    for r in comparison["rows"]:
        lines.append(f"| {r['line']} | {r['label']} | {_money(r['original'])}"
                     f" | {_money(r['change'])} | {_money(r['corrected'])} |")
    lines += ["",
              f"- Line 16 (paid with original return): "
              f"{_money(comparison['line_16_paid_with_original'])}",
              f"- Line 17 (total payments): "
              f"{_money(comparison['line_17_total_payments'])}",
              f"- Line 18 (overpayment on original return): "
              f"{_money(comparison['line_18_original_overpayment'])}",
              f"- Line 19 (net payments): "
              f"{_money(comparison['line_19_net_payments'])}",
              f"- Corrected total tax (line 11C): "
              f"{_money(comparison['corrected_total_tax'])}"]
    if comparison["amount_owed"] > 0:
        lines.append(f"- **AMOUNT OWED with the 1040-X: "
                     f"{_money(comparison['amount_owed'])}**")
    else:
        lines.append(f"- **REFUND on the 1040-X: "
                     f"{_money(comparison['refund'])}**")
    lines += ["", "## Part II — explanation of changes (from the ledger diff)", ""]
    for b in bullets:
        lines.append(f"- {b}")
    lines += ["", "## Notes", ""]
    for n in comparison["notes"]:
        lines.append(f"- {n}")
    return "\n".join(lines) + "\n"


def _read_json(path: Path, what: str) -> dict:
    if not path.is_file():
        raise AmendError(f"{what} not found at {path}")
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        raise AmendError(f"could not read {what}: {e}") from e


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.amend")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--paid-with-original", type=float, default=0.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        year = int(args.year)
        if not (2000 <= year <= 2099):
            raise AmendError("--year must be a 4-digit year 2000-2099")
        workspace = Path(args.workspace).resolve()
        as_filed_dir = _validate_subpath(
            workspace / "archive" / str(year) / "as_filed", workspace)
        original = _read_json(as_filed_dir / "result.json",
                              "as-filed result.json")
        corrected = _read_json(
            _validate_subpath(workspace / "result.json", workspace),
            "current result.json")
        comparison = three_column(original, corrected,
                                  paid_with_original=args.paid_with_original)
        led_b = as_filed_dir / "parse_ledger.json"
        led_a = workspace / "parse_ledger.json"
        if led_b.is_file() and led_a.is_file():
            bullets = ledger_changes(_read_json(led_b, "as-filed ledger"),
                                     _read_json(led_a, "current ledger"))
        else:
            bullets = ["Parse ledger missing on one side — write Part II "
                       "from answers.md (the as-filed snapshot predates A1, "
                       "or /tax-parse hasn't rerun)."]
        if args.json:
            print(json.dumps({"comparison": comparison,
                              "part_ii_bullets": bullets}, indent=2))
        else:
            print(render_markdown(comparison, bullets))
    except (AmendError, RolloverError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
