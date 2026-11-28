"""Record material filing outcomes and the workspace inputs they depend on."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from pathlib import Path

from agents.lib import receipt
from agents.lib.rollover import RolloverError, _validate_subpath
from agents.lib.secure_fs import atomic_write, redact
from agents.lib.workspace import (
    WorkspaceError,
    add_workspace_argument,
    hold_workspace,
    resolve_workspace,
)

FILENAME = receipt.MATERIAL_EVIDENCE_NAME
OUTCOMES = ("open", "computed", "reviewed", "not_applicable")
_FORM_PREFIX = "form."
W2_PREFIX = "w2."
SOURCE_PREFIX = "source."
_INPUT_PREFIX = "input."
DEDUCTION_ITEM = "input.deduction_election"
PERSONAL_1099K_ITEM = "input.1099k_personal_routing"
BUSINESS_OVERLAP_ITEM = "input.business_receipt_overlap"
EXCESS_SS_EMPLOYERS_ITEM = "input.excess_ss_employers"
SCHEDULE_D_WORKSHEET_ITEM = "input.schedule_d_tax_worksheet"
DEDUCTION_METHODS = ("standard", "itemized", "standard_worksheet")
_FILING_INPUT_LABELS = {
    DEDUCTION_ITEM: "Form 1040 line 12 deduction election",
    PERSONAL_1099K_ITEM: "Personal Form 1099-K receipts on a business return",
    BUSINESS_OVERLAP_ITEM: "Overlapping business receipt evidence",
    EXCESS_SS_EMPLOYERS_ITEM: "Schedule 3 line 11 employer identity",
    SCHEDULE_D_WORKSHEET_ITEM: "Schedule D Tax Worksheet for Form 1040 line 16",
    "input.nonrefundable_credits": "Form 1040 lines 19–20 nonrefundable credit worksheets",
    "input.amt": "Form 6251 alternative minimum tax worksheet",
    "input.other_taxes": "Schedule 2 Part II other-tax worksheets",
    "input.payments": "Form 1040 and Form 2210 payment worksheets",
}
_EXCLUDED_INPUTS = {FILENAME, receipt.RECEIPT_NAME, "answers.md"}
# Compared case-folded: on a case-insensitive volume (macOS, Windows)
# `ANSWERS.md` IS answers.md, and an exact match let it through.
_EXCLUDED_FOLDED = {name.casefold() for name in _EXCLUDED_INPUTS}
_ID = re.compile(r"[a-z][a-z0-9_.-]{0,95}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
# Ids the tie-out derives from a digest. A hex digest holds a run of nine
# decimal digits about one time in four, which `redact` reads as an
# identifier, so these ids skip that check. The writer refuses manual
# entries under the prefix.
_LEDGER_ID = re.compile(r"(?:source\.[0-9a-f]{64}|w2\.[0-9a-f]{64}\.[12])")


class MaterialEvidenceError(Exception):
    """The material outcome record cannot support final export."""


def _distinct_keys(pairs: list[tuple[str, object]]) -> dict:
    data = dict(pairs)
    if len(data) != len(pairs):
        raise MaterialEvidenceError(f"{FILENAME} contains duplicate keys")
    return data


def _label(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 160
            and value == value.strip() and value.isprintable() and redact(value) == value)


def _source_path(name: str) -> Path:
    if not isinstance(name, str) or redact(name) != name or not name.isprintable():
        raise MaterialEvidenceError("each evidence reference must be a safe workspace-relative path")
    path = Path(name)
    if (path.is_absolute() or path.anchor or "\\" in name or ":" in name or ".." in path.parts
            or name != path.as_posix() or not path.parts or name.casefold() in _EXCLUDED_FOLDED):
        raise MaterialEvidenceError("each evidence reference must name a canonical workspace input")
    return path


def _dependency(workspace: Path, name: str) -> Path:
    path = _source_path(name)
    try:
        resolved = _validate_subpath(workspace / path, workspace)
    except RolloverError as exc:
        raise MaterialEvidenceError("an evidence reference points outside the workspace") from exc
    if resolved.relative_to(workspace.resolve()).as_posix().casefold() in _EXCLUDED_FOLDED:
        raise MaterialEvidenceError("each evidence reference must name a canonical workspace input")
    return resolved


def read(workspace: Path) -> tuple[bytes | None, dict]:
    """Load the exact evidence bytes and validate the versioned record."""
    path = _validate_subpath(workspace / FILENAME, workspace)
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise MaterialEvidenceError(f"{FILENAME} must be a regular file")
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, {}
    try:
        data = json.loads(raw, object_pairs_hook=_distinct_keys)
    except ValueError as exc:
        raise MaterialEvidenceError(f"{FILENAME} is not readable JSON") from exc
    if (not isinstance(data, dict) or type(data.get("version")) is not int
            or data["version"] != 1 or not isinstance(data.get("items"), dict)):
        raise MaterialEvidenceError(f"{FILENAME} must be a version 1 material evidence record")
    _check_items(data["items"])
    return raw, data["items"]


def _check_items(items: dict) -> None:
    for item_id, item in items.items():
        if (not _ID.fullmatch(item_id)
                or (not _LEDGER_ID.fullmatch(item_id) and redact(item_id) != item_id)
                or not isinstance(item, dict)
                or not _label(item.get("label")) or item.get("outcome") not in OUTCOMES):
            raise MaterialEvidenceError(f"{FILENAME} contains an invalid material item")
        inputs = item.get("inputs")
        if (not isinstance(inputs, dict)
                or (item["outcome"] == "open") != (not inputs)):
            raise MaterialEvidenceError(f"{FILENAME} requires source files for each recorded outcome")
        for name, digest in inputs.items():
            _source_path(name)
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise MaterialEvidenceError(f"{FILENAME} contains an invalid source digest")


def _write(workspace: Path, items: dict) -> None:
    # The reader's checks, before anything reaches disk: a record its own
    # reader refuses would wedge every later gate on this workspace.
    _check_items(items)
    with atomic_write(workspace / FILENAME) as handle:
        json.dump({"version": 1, "items": items}, handle, indent=2, sort_keys=True)
        handle.write("\n")


def sync_required_forms(workspace: Path, required: dict[str, str]) -> None:
    """Register open forms from the capability gate without changing other items."""
    sync_group(workspace, _FORM_PREFIX,
               {key: {"label": label, "outcome": "open", "inputs": {}}
                for key, label in required.items()})


def sync_group(workspace: Path, prefix: str, replacements: dict) -> None:
    """Replace the records derived from one gate, preserving the other items."""
    _, items = read(workspace)
    updated = {key: item for key, item in items.items() if not key.startswith(prefix)}
    updated.update(replacements)
    if updated != items:
        _write(workspace, updated)


def open_items(workspace: Path, items: dict, required: dict[str, str]) -> list[str]:
    """Report open or stale items, including required forms absent from the file."""
    reasons = [f"Open material item: {label}." for label in required.values()]
    for item_id, item in sorted(items.items()):
        if item_id in required:
            continue
        label = item["label"]
        if item["outcome"] == "open":
            reasons.append(f"Open material item: {label}.")
            continue
        for name, previous in item["inputs"].items():
            try:
                path = _dependency(workspace, name)
                unchanged = path.is_file() and receipt._file_digest(path) == previous
            except (OSError, RolloverError, MaterialEvidenceError, receipt.ReceiptError):
                unchanged = False
            if not unchanged:
                reasons.append(f"Open material item: {label}. A source changed or is missing.")
                break
    return reasons


def _positive(value) -> bool:
    from agents.lib.tax_law_fetcher import _is_number
    return _is_number(value) and value > 0


def _status_key(result: dict, profile: dict) -> str:
    from agents.lib.projection import _canonical_status
    status = (result.get("meta") or {}).get("filing_status") or profile.get("filing_status")
    return _canonical_status(status, "material filing inputs").lower()


def _above(value, threshold) -> bool:
    """Over a cached threshold; a missing threshold cannot rule the tax out."""
    from agents.lib.tax_law_fetcher import _is_number
    return not _is_number(threshold) or (_is_number(value) and value > threshold)


def _required_filing_inputs(result: dict, profile: dict, law: dict | None = None, *,
                            ledger: list | None = None) -> dict[str, bool]:
    """Applicable inputs, with True when a positive amount is already claimed
    or the return's own inputs make the item apply."""
    from agents.lib.tax_law_fetcher import _get_path
    from agents.lib.tie_out import filing_medicare_wages, has_rrta_tier1_withholding, has_special_rate_gains
    from agents.lib.yoy_diff import _section_present

    flags = profile.get("flags") or {}
    form = result.get("form_1040") or {}
    required = {DEDUCTION_ITEM: True}
    if has_special_rate_gains(ledger or [], profile, result):
        required[SCHEDULE_D_WORKSHEET_ITEM] = True

    def flags_set(*names):
        return any(flags.get(name) is True for name in names)

    def positive_lines(*names):
        return any(_positive(form.get(name)) for name in names)

    # The key /tax-rollover and the template write. `form_8801_carryforward`
    # was read here and in AA114, and nothing writes it, so a prior-year AMT
    # credit (Form 8801 through Schedule 3 line 6b) never asked for review.
    amt_credit = _positive((profile.get("prior_year") or {}).get("amt_credit_carryforward"))
    credits = positive_lines("line_19_ctc", "line_20_schedule3_line8")
    if credits or amt_credit or profile.get("dependents") or result.get("dependents") or flags_set(
            "has_dependent_care", "has_education_expenses", "has_foreign_tax_paid", "has_energy_credits", "has_ev_purchase"):
        required["input.nonrefundable_credits"] = credits
    amt = positive_lines("line_17_schedule2_part1")
    if amt or amt_credit or flags_set("has_iso_exercise") or (result.get("schedule_a") or {}).get("use_itemized") is True or _section_present(result, "form_6251"):
        required["input.amt"] = amt
    se_total = (result.get("schedule_se") or {}).get("schedule_2_line_4_se_tax")
    other_tax = form.get("line_23_other_taxes", 0)
    remainder = _positive(other_tax) and round(other_tax, 2) != (se_total or 0)
    # NIIT and Additional Medicare tax apply from the return's own inputs. A
    # line 23 at 0, or equal to the SE tax alone, left a $390,000 joint return
    # with $30,000 of investment income validated with neither tax.
    status = _status_key(result, profile)
    key = {"mfj": "mfj", "qss": "mfj", "mfs": "mfs"}.get(status, "single")
    law = law or {}
    investment = positive_lines("line_2b_taxable_interest", "line_3b_ordinary_dividends",
                                "line_7_capital_gain") or _section_present(result, "schedule_e_rentals")
    niit_applies = investment and _positive(form.get("line_11_agi")) and _above(
        form.get("line_11_agi"), _get_path(law, f"federal.niit.threshold.{key}"))
    business = (result.get("schedule_c") or {}).get("line_31_net")
    # Form 8959 line 8 never nets one spouse's loss against the other's
    # earnings, so the Schedule SE earnings per owner screen as well.
    se_owners = ((result.get("schedule_se") or {}).get("by_owner") or {}).values()
    se_earnings = sum(owner["net_earnings"] for owner in se_owners
                      if isinstance(owner, dict) and _positive(owner.get("net_earnings")))
    if se_earnings and not (isinstance(business, (int, float)) and business >= se_earnings):
        business = se_earnings
    wages = filing_medicare_wages(ledger or [], form.get("line_1a_wages"))
    earnings = 0.0
    for value in (wages, business):
        if isinstance(value, (int, float)) and _positive(value):
            earnings += value
    medicare_key = "single" if status == "qss" else key
    medicare_applies = _positive(earnings) and _above(
        earnings, _get_path(law, f"federal.additional_medicare_tax.threshold.{medicare_key}"))
    if remainder or niit_applies or medicare_applies or flags_set(
            "has_unreported_or_allocated_tips", "has_household_employees", "has_dependent_unearned_income",
            "has_hsa", "has_1099r") or _section_present(result, "form_8959_additional_medicare"):
        required["input.other_taxes"] = remainder or niit_applies or medicare_applies

    # Excess Social Security withholding is part of line 31. The tie-out
    # checks it against W-2 boxes 3, 4 and 7, but a railroad employee's
    # combined Tier 1 worksheet only the payment review can check.
    other_payment = form.get("line_31_schedule3_part2", 0)
    has_other_payment = _positive(other_payment)
    dated_payments = (result.get("form_2210") or {}).get("payment_table") is not None
    needs_manual_penalty = not dated_payments and positive_lines("line_38_estimated_tax_penalty")
    if (has_other_payment or needs_manual_penalty or flags_set("filed_extension")
            or has_rrta_tier1_withholding(ledger or [])):
        required["input.payments"] = has_other_payment or needs_manual_penalty
    return required


def _deduction_facts(profile: dict, key: str) -> tuple[bool, bool]:
    """(65 or older or blind, can be claimed as a dependent): the facts that move
    line 12 off the basic amount. The spouse counts only on a joint return."""
    flags = profile.get("flags") or {}
    exemptions = profile.get("ca_exemptions") or {}
    people = ("primary", "spouse") if key == "mfj" else ("primary",)
    older = any(flags.get(f"age_65_plus_{who}") is True or exemptions.get(f"{who}_blind") is True
                for who in people)
    dependent = any(exemptions.get(f"{who}_can_be_claimed") is True for who in people)
    return older, dependent


def _deduction_problem(result: dict, profile: dict, law: dict, method: str | None) -> str:
    from agents.lib.projection import ProjectionError, _full_status_key
    from agents.lib.tax_computations._shared import _amount
    from agents.lib.tax_law_fetcher import _get_path

    form = result.get("form_1040") or {}
    try:
        amount = _amount(form.get("line_12_standard_deduction"), "Form 1040 line 12")
        sched_a = result.get("schedule_a") or {}
        if method != "itemized" and (sched_a.get("inputs") or {}).get("mfs_spouse_itemizes") is True:
            return "Use the reviewed Schedule A election when the spouse itemizes."
        if method == "itemized":
            itemized = _amount(sched_a.get("itemized_total"), "Schedule A itemized total")
            if sched_a.get("use_itemized") is not True or round(amount, 2) != round(itemized, 2):
                return "Line 12 must equal the elected Schedule A deduction."
        elif method in ("standard", "standard_worksheet"):
            status = (result.get("meta") or {}).get("filing_status") or profile.get("filing_status")
            key = _full_status_key(status, "line 12 deduction election")
            older, dependent = _deduction_facts(profile, key)
            base = _amount(_get_path(law, f"federal.filing_statuses.{key}.standard_deduction"), "cached standard deduction")
            if sched_a.get("use_itemized") is True:
                return "The reviewed standard deduction conflicts with the Schedule A election."
            if method == "standard":
                if older or dependent:
                    return ("Use the reviewed standard deduction worksheet for age, blindness or a filer "
                            "who can be claimed as a dependent.")
                if amount != base or base <= 0:
                    return "Line 12 must equal the covered-year basic standard deduction."
            # The worksheet is for a fact the profile records. Without one it
            # accepted any line 12 with any file attached, the AA113 zero included.
            elif not (older or dependent):
                return "Use the basic standard deduction: no age, blindness or dependent fact needs the worksheet."
            elif amount <= 0 or (dependent and not older and amount > base) or (older and not dependent and amount <= base):
                return "Line 12 is outside what the standard deduction worksheet allows for these facts."
        else:
            return "Record the deduction method."
    except (ValueError, TypeError, AttributeError, OverflowError, ProjectionError):
        return "Review the elected deduction and the inputs that support it."
    return ""


def filing_input_issues(captured: dict[str, bytes | None], *, ledger: list | None = None) -> list[str]:
    """Check required outcomes against the snapshots being used at export."""
    from agents.lib.projection import ProjectionError
    from agents.lib.tax_law_fetcher import _get_path, _is_number

    try:
        result = json.loads(captured.get("result.json") or b"{}")
        profile = json.loads(captured.get("taxpayer_profile.json") or b"{}")
        evidence = json.loads(captured.get(FILENAME) or b"{}")
        if not all(isinstance(doc, dict) for doc in (result, profile, evidence)) or not isinstance(result.get("form_1040"), dict):
            return []
        items = evidence.get("items") or {}
        law_name = _law_name(result, profile)
        law = json.loads(captured.get(law_name) or b"{}")
        mandatory = {"result.json", "taxpayer_profile.json", law_name}
        reasons = []
        for item_id, positive in _required_filing_inputs(result, profile, law, ledger=ledger).items():
            item = items.get(item_id) or {}
            outcome = item.get("outcome")
            inputs = item.get("inputs") or {}
            prefix = f"Open material item: {_FILING_INPUT_LABELS[item_id]}."
            if outcome not in OUTCOMES or outcome == "open":
                reasons.append(prefix)
                continue
            if outcome == "not_applicable" and positive:
                reasons.append(f"{prefix} The return still reports an applicable amount or election.")
                continue
            # The worksheet reads box 2b and 2d amounts the return does not
            # restate, so a re-read source changes only the ledger.
            bound = mandatory | ({"parse_ledger.json"} if item_id == SCHEDULE_D_WORKSHEET_ITEM else set())
            if not bound <= inputs.keys() or any(not captured.get(name) or
                    inputs[name] != receipt._sha256(captured[name] or b"") for name in bound):
                ledger_note = " or parse ledger" if "parse_ledger.json" in bound else ""
                reasons.append(f"{prefix} The result, profile or covered-year law{ledger_note} needs fresh review.")
                continue
            method = item.get("deduction_method")
            if not _worksheet_attached(item_id, method, inputs, bound):
                reasons.append(f"{prefix} Attach the reviewed worksheet as a source.")
                continue
            if item_id == SCHEDULE_D_WORKSHEET_ITEM:
                if outcome != "reviewed":
                    reasons.append(f"{prefix} Record the worksheet outcome as reviewed.")
                    continue
                tax = _get_path(result, "schedule_d.tax_worksheet_tax")
                if not _is_number(tax) or tax < 0 or tax != result["form_1040"].get("line_16_tax"):
                    reasons.append(f"{prefix} Record the reviewed tax and apply it to Form 1040 line 16.")
            if item_id == DEDUCTION_ITEM:
                detail = _deduction_problem(result, profile, law, method)
                if detail:
                    reasons.append(f"{prefix} {detail}")
        return reasons
    except (ValueError, TypeError, AttributeError, OverflowError, ProjectionError):
        return ["Could not validate the material filing inputs."]


def _law_name(result, profile) -> str:
    year = (result.get("meta") or {}).get("tax_year") or profile.get("tax_year")
    return f"tax_law_{year}.json" if type(year) is int and 2000 <= year <= 2099 else ""


def _worksheet_attached(item_id, method, inputs, mandatory) -> bool:
    return bool(item_id == DEDUCTION_ITEM and method == "standard"
                or inputs.keys() - mandatory)


def reviewed_ledger_sources(item: dict, ledger_sha256: str | None) -> dict[str, str]:
    """Return the sources of a reviewed item tied to the current ledger."""
    sources = item.get("inputs") or {}
    if (item.get("outcome") != "reviewed" or ledger_sha256 is None
            or sources.get("parse_ledger.json") != ledger_sha256):
        return {}
    return sources


def record(workspace: Path, item_id: str, label: str, outcome: str,
           inputs: list[str], *, deduction_method: str | None = None) -> None:
    """Record an outcome after working its evidence. The caller holds the workspace lock."""
    if not _ID.fullmatch(item_id) or redact(item_id) != item_id or not _label(label):
        raise MaterialEvidenceError("use a stable item id and a short label without taxpayer details")
    if item_id.startswith(_FORM_PREFIX):
        raise MaterialEvidenceError("required forms must be completed through their filing workflow")
    if item_id.startswith(W2_PREFIX):
        raise MaterialEvidenceError("W-2 box outcomes come from the parse ledger")
    if item_id.startswith(SOURCE_PREFIX):
        raise MaterialEvidenceError("source-document outcomes come from the parse ledger")
    if outcome not in OUTCOMES or (outcome == "open") != (not inputs):
        raise MaterialEvidenceError("record a closed outcome with its source files, or mark it open")
    if item_id.startswith(_INPUT_PREFIX):
        if item_id not in _FILING_INPUT_LABELS:
            raise MaterialEvidenceError("unknown material filing input")
        label = _FILING_INPUT_LABELS[item_id]
        if item_id == DEDUCTION_ITEM and outcome != "open" and deduction_method not in DEDUCTION_METHODS:
            raise MaterialEvidenceError("record the reviewed deduction method")
    if deduction_method is not None and (item_id != DEDUCTION_ITEM or outcome == "open"):
        raise MaterialEvidenceError("a deduction method belongs on a completed deduction election")
    _, items = read(workspace)
    digests = {}
    for name in inputs:
        path = _dependency(workspace, name)
        if not path.is_file():
            raise MaterialEvidenceError("each evidence reference must name an existing regular file")
        try:
            digests[name] = receipt._file_digest(path)
        except receipt.ReceiptError as exc:
            raise MaterialEvidenceError("an evidence source could not be read") from exc
    item = {"label": label, "outcome": outcome, "inputs": digests}
    if deduction_method is not None:
        item["deduction_method"] = deduction_method
    if items.get(item_id) != item:
        items[item_id] = item
        _write(workspace, items)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agents.lib.material_evidence")
    add_workspace_argument(parser)
    parser.add_argument("--id", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--outcome", choices=OUTCOMES, required=True)
    parser.add_argument("--deduction-method", choices=DEDUCTION_METHODS)
    parser.add_argument("--input", action="append", default=[],
                        help="workspace-relative evidence or input file (repeatable)")
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace).resolve()
        hold_workspace(workspace, "material_evidence")
        record(workspace, args.id, args.label, args.outcome, args.input,
               deduction_method=args.deduction_method)
    except (MaterialEvidenceError, WorkspaceError, RolloverError, receipt.ReceiptError, OSError) as exc:
        print(f"ERROR: {redact(exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
