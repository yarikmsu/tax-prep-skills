#!/usr/bin/env python3
"""Filing-package assembly order + attachment checklist for /tax-generate
--filing-package (F1).

Pure compute: reads result.json, returns the paper-assembly order (Form 1040
first, then every present form by its official IRS Attachment Sequence
Number) and the attachment checklist. Read-only — the PDF itself is built by
the workspace's generate_tax_pdf.py per the skill instructions; this module
supplies the ORDER and the CHECKLIST so they're computed and testable, not
hand-enumerated prose.

NOT an e-file artifact: no MeF XML, no Form 8879 — the skill says so on
page 1 of the package.

Sequence numbers are STRUCTURAL facts printed in the top-right corner of
each official form ("Attachment Sequence No."). They are stable year to
year but not statutorily guaranteed — the skill instructs a spot-check
against the filing year's forms.
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
from agents.lib import receipt  # noqa: E402
from agents.lib.yoy_diff import _section_present  # noqa: E402
from agents.lib.secure_fs import redact  # noqa: E402


class FilingPackageError(Exception):
    """Raised when the filing package cannot safely be assembled."""


# result.json section key → (display title, IRS Attachment Sequence No.).
# Ordered output sorts by sequence; Form 1040 itself is always first and
# carries no sequence number. State forms follow ALL federal attachments.
# Sequence numbers per the official 2024-cycle forms (top-right corner).
# Keys are ACTUAL result_template.json section keys. Schedules 1/2/3/SE/8812
# and Schedule B have no single same-named section — they are DERIVED from
# the computed fields that encode them (see _derived_schedules): omitting
# them made a Schedule C return unfilable as assembled (review P1).
_FEDERAL_SEQUENCE = {
    "form_2210": ("Form 2210 (Underpayment of Estimated Tax)", "06"),
    "schedule_a": ("Schedule A (Itemized Deductions)", "07"),
    "schedule_c": ("Schedule C (Profit or Loss From Business)", "09"),
    "schedule_d": ("Schedule D (Capital Gains and Losses)", "12"),
    "form_8949": ("Form 8949 (Sales and Other Dispositions)", "12A"),
    "form_1116_ftc": ("Form 1116 (Foreign Tax Credit)", "19"),
    "form_6251": ("Form 6251 (Alternative Minimum Tax)", "32"),
    "form_8606": ("Form 8606 (Nondeductible IRAs)", "48"),
    "form_8863": ("Form 8863 (Education Credits)", "50"),
    "form_8889_hsa": ("Form 8889 (Health Savings Accounts)", "52"),
    "form_8995_qbi": ("Form 8995 (QBI Deduction)", "55"),
    "form_8959_additional_medicare": ("Form 8959 (Additional Medicare Tax)", "71"),
}

_STATE_SECTIONS = {
    "california_540": "California Form 540",
    "schedule_ca_itemized": "California Schedule CA (540)",
}


def _seq_sort_key(seq: str):
    """'12A' sorts after '12' and before '13'."""
    digits = "".join(ch for ch in seq if ch.isdigit())
    suffix = "".join(ch for ch in seq if not ch.isdigit())
    if not digits:
        raise FilingPackageError(
            f"attachment sequence {seq!r} has no digits — fix the sequence "
            f"table (state/None entries must bypass the sort)")
    return (int(digits), suffix)


def _field_num(section: dict, field: str, what: str) -> float:
    """Numeric field read, repo-strict: absent/None → 0; bool/non-finite/
    non-numeric present values hard-error (a string '1200' silently
    coercing to 0 would drop checklist lines that money depends on)."""
    v = (section or {}).get(field)
    if v is None:
        return 0.0
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise FilingPackageError(f"{what} must be numeric, got {v!r}")
    f = float(v)
    if not math.isfinite(f):
        raise FilingPackageError(f"{what} must be finite, got {v!r}")
    return f


def _derived_schedules(result: dict) -> list:
    """Schedules derived from the computed fields that encode them, value-gated
    (not _section_present): a $0 line_19_ctc must not conjure a Schedule 8812.

    Each entry is (title, sequence, section_key). section_key is None for the
    reconstructed schedules that have NO same-named result section (Schedule
    1/2/3/B/SE/8812); Schedule 1-A is the exception — it has a real
    `schedule_1a` section, so its key is set and the filing package renders
    the per-provision worksheet detail rather than a bare assembly line."""
    f1040 = result.get("form_1040") or {}
    sch_c = result.get("schedule_c") or {}
    out = []
    if (_field_num(f1040, "line_8_schedule1_other", "line_8") != 0
            or _field_num(f1040, "line_10_adjustments", "line_10") != 0
            or _section_present(result, "schedule_c")):
        out.append(("Schedule 1 (Additional Income and Adjustments)", "01",
                    None))
    # Schedule 1-A (OBBBA tips/overtime/car-loan/senior below-AGI deductions) —
    # gated on the computed line-13b total. The official form prints Attachment
    # Sequence No. "1A"; _seq_sort_key parses it to (1, "A"), which sorts after
    # Schedule 1 ("01" → (1, "")) and before Schedule 2, so it still attaches
    # right after Schedule 1.
    if _field_num(f1040, "line_13b_schedule_1a_deductions", "line_13b") != 0:
        # line 13b is non-zero, so the `schedule_1a` section MUST carry the
        # per-provision tips/overtime/car-loan/senior worksheet detail —
        # packaging a bare Schedule 1-A row (or a total-only section like
        # {"total": 9000}, which _section_present would wave through) would
        # attach an incomplete form. Require at least one provision object with
        # an `allowed` figure, so an inconsistent result hard-stops instead of
        # emitting a keyed-but-hollow attachment.
        sch1a = result.get("schedule_1a")
        line_13b = _field_num(
            f1040, "line_13b_schedule_1a_deductions", "line_13b")
        allowed_sum = 0.0
        if isinstance(sch1a, dict):
            for p in ("tips", "overtime", "car_loan", "senior"):
                obj = sch1a.get(p)
                if isinstance(obj, dict) and "allowed" in obj:
                    allowed_sum += _field_num(
                        obj, "allowed", f"schedule_1a.{p}.allowed")
        # The per-provision `allowed` amounts must RECONCILE to line 13b — a
        # key check alone would pass a scaffold (every allowed:0) or a stale
        # section whose detail doesn't support the packaged total, attaching a
        # hollow/mismatched Schedule 1-A worksheet.
        if round(allowed_sum, 2) != round(line_13b, 2):
            raise FilingPackageError(
                f"schedule_1a per-provision allowed amounts (sum "
                f"{allowed_sum:.2f}) do not reconcile to Form 1040 line 13b "
                f"({line_13b:.2f}) — the attached Schedule 1-A worksheet must "
                f"support the packaged total; re-run /tax-compute before "
                f"packaging")
        # Car-loan Part IV: a nonzero car-loan deduction needs at least one
        # complete vehicle row (VIN + interest_paid) to complete Schedule 1-A
        # Part IV — a claim with no vehicle detail can't be assembled. (The
        # allowed amount is not reconciled to the row interest: the cap and
        # MAGI phase-out make allowed ≤ personal interest, so equality would
        # false-fail a legitimately capped/phased claim.)
        car_obj = sch1a.get("car_loan") if isinstance(sch1a, dict) else None
        car_allowed = (
            _field_num(car_obj, "allowed", "schedule_1a.car_loan.allowed")
            if isinstance(car_obj, dict) and "allowed" in car_obj else 0.0)
        if car_allowed > 0:
            detail = sch1a.get("car_loan_detail")
            has_row = isinstance(detail, list) and any(
                isinstance(row, dict) and row.get("vin")
                and _field_num(row, "interest_paid",
                               "car_loan_detail.interest_paid") > 0
                for row in detail)
            if not has_row:
                raise FilingPackageError(
                    "schedule_1a.car_loan.allowed is nonzero but "
                    "schedule_1a.car_loan_detail has no complete vehicle row "
                    "(vin + interest_paid) — Schedule 1-A Part IV requires the "
                    "VIN detail to attach; add it before packaging")
        out.append(("Schedule 1-A (Additional Deductions — tips/overtime/"
                    "car-loan/senior)", "1A", "schedule_1a"))
    if (_field_num(f1040, "line_23_other_taxes", "line_23") != 0
            or _field_num(result.get("form_6251") or {}, "amt", "amt") > 0):
        out.append(("Schedule 2 (Additional Taxes)", "02", None))
    if (_field_num(f1040, "line_20_schedule3_line8", "line_20") != 0
            or _field_num(f1040, "line_31_schedule3_part2", "line_31") != 0
            or _field_num(f1040, "schedule_3_line_11_excess_ss",
                          "sch3_line11") != 0):
        out.append(("Schedule 3 (Additional Credits and Payments)", "03",
                    None))
    # Schedule B: >$1,500 interest or dividends, or Part III (foreign
    # accounts — mandatory for exactly this toolkit's FBAR audience).
    interest = result.get("interest_detail") or {}
    if (_field_num(f1040, "line_2b_taxable_interest", "line_2b") > 1500
            or _field_num(f1040, "line_3b_ordinary_dividends",
                          "line_3b") > 1500
            or interest.get("foreign_accounts_yes") is True
            or interest.get("fbar_required") is True
            or _section_present(result, "fbar_fincen_114")
            or _section_present(result, "form_8938_fatca")):
        out.append(("Schedule B (Interest and Ordinary Dividends — incl. "
                    "Part III foreign accounts)", "08", None))
    if _field_num(sch_c, "se_tax", "schedule_c.se_tax") > 0:
        out.append(("Schedule SE (Self-Employment Tax)", "17", None))
    if _field_num(f1040, "line_19_ctc", "line_19_ctc") > 0:
        out.append(("Schedule 8812 (Credits for Qualifying Children)", "47",
                    None))
    return out


def assembly_order(result: dict) -> list:
    """Paper-assembly order for the sections PRESENT in result.json.

    Returns a list of dicts {key, title, sequence} — Form 1040 first, then
    federal attachments by Attachment Sequence No., then state forms.
    Presence uses the same content rule as the YoY diff (`_section_present`)
    so template scaffolding never generates a phantom attachment line.
    """
    if not isinstance(result, dict):
        raise FilingPackageError("result must be a JSON object")
    if not _section_present(result, "form_1040"):
        raise FilingPackageError(
            "result.json has no computed form_1040 — run /tax-compute first")
    # Compute-evidence marker: parse-stage data alone fills income lines,
    # and packaging an uncomputed return would assemble a wrong filing.
    # meta.limitations is never empty after /tax-compute Step 12 (the same
    # evidence /tax-generate itself hard-gates on).
    if not ((result.get("meta") or {}).get("limitations")):
        raise FilingPackageError(
            "result.json shows no compute evidence (meta.limitations is "
            "empty) — run /tax-compute through Step 12 before packaging")
    order = [{"key": "form_1040", "title": "Form 1040", "sequence": None}]
    federal = []
    for key, (title, seq) in _FEDERAL_SEQUENCE.items():
        if _section_present(result, key):
            federal.append({"key": key, "title": title, "sequence": seq})
    for title, seq, dkey in _derived_schedules(result):
        federal.append({"key": dkey, "title": title, "sequence": seq})
    federal.sort(key=lambda e: _seq_sort_key(e["sequence"]))
    order.extend(federal)
    # Form 8938 files WITH the return but the template models it as an
    # international-compliance block; keep it after the sequenced forms.
    if _section_present(result, "form_8938_fatca"):
        order.append({"key": "form_8938_fatca",
                      "title": "Form 8938 (Statement of Specified Foreign "
                               "Financial Assets)", "sequence": "175"})
    for key, title in _STATE_SECTIONS.items():
        if _section_present(result, key):
            order.append({"key": key, "title": title, "sequence": "STATE"})
    return order


def attachment_checklist(result: dict) -> list:
    """Human checklist lines for what physically attaches to a paper filing."""
    lines = [
        "Sign and date Form 1040 (both spouses on a joint return) — an "
        "unsigned return is not filed.",
        "Attach each W-2, Copy B, to the front of Form 1040.",
        "Attach a 1099 ONLY when it shows federal income tax withheld "
        "(e.g. 1099-R with withholding).",
    ]
    f1040 = result.get("form_1040") or {}

    if _section_present(result, "form_8949"):
        lines.append(
            "Form 8949: two separate exceptions — Box A/D covered lots "
            "with NO adjustments may be reported directly on Schedule D "
            "lines 1a/8a with NO 8949 and NO attachment at all; for ANY "
            "box, a broker statement carrying the same columns may attach "
            "in place of additional 8949 pages (8949 instructions, "
            "Exception 2).")
    balance_due = _field_num(f1040, "line_37_amount_owed", "line_37")
    if balance_due > 0:
        lines.append(
            "Federal balance due: include Form 1040-V — payee 'United "
            "States Treasury', memo with SSN, tax year, and 'Form 1040'; "
            "do NOT staple the payment to the return (or pay via IRS "
            "Direct Pay and skip the voucher).")
        lines.append(
            "Owing this year usually means NEXT year needs estimated "
            "payments — run /tax-project for the 1040-ES (and CA 540-ES) "
            "voucher schedule; vouchers are not part of this package.")
    refund = _field_num(f1040, "line_35a_refund", "line_35a")
    if refund > 0:
        lines.append(
            "Refund: complete Form 1040 lines 35b-35d (routing number, "
            "account number, account type) for direct deposit — a blank "
            "35b-d means a paper check.")
    if _section_present(result, "form_8606"):
        lines.append(
            "Form 8606 is PER INDIVIDUAL — on a joint return, each spouse "
            "with nondeductible basis or a conversion files their OWN "
            "8606; this package carries one section, so duplicate it per "
            "spouse as needed.")
    if _section_present(result, "california_540"):
        lines.append(
            "California Form 540 mails in its OWN envelope to the FTB — "
            "the payment and refund addresses are DIFFERENT PO boxes; "
            "confirm the current addresses on ftb.ca.gov.")
        lines.append(
            "CA 540 attachments: W-2s (Copy 2), Schedule CA (540) when "
            "present, and a COMPLETE copy of the federal return whenever "
            "any federal schedule other than A or B was filed — for this "
            "toolkit's returns that is almost always.")
        if balance_due >= 0 and _section_present(result, "california_540"):
            ca = result.get("california_540") or {}
            ca_tax = _field_num(ca, "ca_total_tax", "ca_total_tax")
            ca_paid = (_field_num(ca, "ca_withholding_box17", "ca_wh")
                       + _field_num(ca, "ca_excess_sdi_credit", "ca_sdi"))
            if ca_tax > ca_paid:
                lines.append(
                    "CA balance due: check payable to 'Franchise Tax "
                    "Board' with SSN and '{tax year} Form 540' in the "
                    "memo, or pay via FTB Web Pay (mandatory e-pay may "
                    "apply — R&TC §19011.5).")
    # FBAR: never a categorical all-clear — absence of the section only
    # means /tax-fbar hasn't run (review P1: gating on the wrong signal
    # printed 'nothing files outside this package' for a foreign-account
    # return; FBAR nonfiling penalties start ~$10K/violation).
    interest = result.get("interest_detail") or {}
    foreign_signals = (interest.get("foreign_accounts_yes") is True
                       or interest.get("fbar_required") is True
                       or _section_present(result, "form_8938_fatca"))
    if _section_present(result, "fbar_fincen_114"):
        lines.append(
            "FBAR (FinCEN 114) is NOT part of this package — it files "
            "separately on the BSA e-filing system (see /tax-fbar).")
    elif foreign_signals:
        lines.append(
            "FOREIGN-ACCOUNT SIGNALS present but no FBAR section — the "
            "FBAR determination is PENDING: run /tax-fbar before filing "
            "(it files separately on the BSA system; nonfiling penalties "
            "start around $10K per violation).")
    else:
        lines.append(
            "No foreign-account signals in this return. If you DO have "
            "foreign accounts, run /tax-fbar — the FBAR files separately "
            "(BSA e-filing), never with this package.")
    return lines


def render_markdown(result: dict) -> str:
    order = assembly_order(result)
    lines = ["# Filing package — assembly order", "",
             "**NOT an e-file artifact.** No MeF submission, no Form 8879. "
             "For paper filing, transcribe onto the OFFICIAL forms in this "
             "order (each form's Attachment Sequence No. is printed in its "
             "top-right corner — spot-check against the filing year's "
             "forms); for software filing, use the transfer mapping in the "
             "package PDF.", "",
             "| # | Form | Attachment Seq. |", "|---|---|---|"]
    for i, e in enumerate(order, 1):
        lines.append(f"| {i} | {e['title']} | {e['sequence'] or '—'} |")
    lines += ["", "## Attachment checklist", ""]
    for item in attachment_checklist(result):
        lines.append(f"- [ ] {item}")
    return "\n".join(lines) + "\n"


def _draft_banner(checked) -> str:
    """The NOT VALIDATED header a draft carries, or "" for a validated package.

    On PAGE ONE, beside the limitations manifest the generated PDF already
    prints there: a reader who sees only the assembled forms cannot tell a draft
    from a return, and the one place they are certain to look is the top.
    """
    if checked.valid:
        return ""
    lines = ["# NOT VALIDATED — DRAFT ONLY", "",
             "This package was assembled with `--draft`. It does NOT describe "
             "the workspace as it stands:", ""]
    lines += [f"- {reason}" for reason in checked.reasons]
    lines += ["", "Re-run /tax-compute — its Step 0.7 and Step 12.7 gates "
                  "rewrite the validation receipt — and assemble again before "
                  "anything here is filed.", "", "---", ""]
    return "\n".join(lines)


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.filing_package")
    parser.add_argument("--result", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--draft", action="store_true",
                        help="assemble anyway when the validation receipt is "
                             "stale, marked NOT VALIDATED on page 1")
    args = parser.parse_args(argv)
    try:
        workspace = Path(args.workspace).resolve()
        result_path = _validate_subpath(Path(args.result).resolve(), workspace)
        # Read ONCE, and check THESE bytes below: re-reading to verify checks a
        # file that may no longer be the one being packaged.
        raw = result_path.read_bytes()
        result = json.loads(raw)
        # V8: the package is assembled from gates that PASSED on the inputs that
        # are here NOW. Without this, a document arriving after Step 12.7 — the
        # second 1099 on Thursday — produced a filing package on a tie-out that
        # never saw it, and nothing in the package said so.
        #
        # `--draft` is the escape hatch, and it is banner-marked rather than
        # silent: a refusal with no way through is one an agent routes around by
        # not running this command at all, and a draft nobody can tell from a
        # final package is worse than either.
        checked = receipt.verify(workspace)
        # ...and the file being PACKAGED has to be the file the tie-out gate
        # validated. `--result` takes any path inside the workspace, so a
        # receipt covering `result.json` would otherwise certify a package
        # assembled from `result_draft.json` — a valid receipt on a return no
        # gate ever saw, which is the same defect one level up.
        packaged = receipt.covers_result(workspace, result_path, contents=raw)
        if packaged:
            checked = receipt.VerifyReport(False, checked.reasons + [packaged])
        if not checked.valid and not args.draft:
            print("REFUSED: the filing package would not describe this "
                  "workspace.", file=sys.stderr)
            for reason in checked.reasons:
                print(f"  - {reason}", file=sys.stderr)
            print("\nRe-run /tax-compute (Step 0.7 and Step 12.7 rewrite the "
                  "receipt), or pass --draft for a NOT VALIDATED copy.",
                  file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({
                "assembly_order": assembly_order(result),
                "attachment_checklist": attachment_checklist(result),
                "validated": checked.valid,
                "receipt": checked.to_dict(),
            }, indent=2))
        else:
            print(_draft_banner(checked) + render_markdown(result))
    except (FilingPackageError, RolloverError, receipt.ReceiptError, OSError,
            json.JSONDecodeError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
