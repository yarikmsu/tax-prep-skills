#!/usr/bin/env python3
"""Parse-to-return tie-out for /tax-compute (Epic A2) over the /tax-parse
ledger (A1).

THE problem this solves (the IRS reviewer's central P1): numbers flow
document → prose summaries → hand-aggregated result.json, and nothing ever
proves a parsed 1099 actually landed on a return line. A silently dropped
document is the textbook CP2000/AUR under-report. /tax-review cannot catch it
because it audits the already-aggregated result — this module audits the
intake-to-output MAPPING instead:

  Check A (coverage): every source file under documents/ has exactly one
    ledger record — parsed with key boxes, or skipped with a reason. A file
    with no record, or a record pointing at a missing file, FAILS. A skip
    whose reason starts with "pending" (another skill still owes the parse,
    e.g. "pending /tax-parse-crypto") also FAILS — it is a to-do, not a
    resolution, and coverage credit for it would let the return complete
    with that document's amounts never entering any sum.
  Check B (category validity): every key_boxes key is a known category from
    the canonical registry (a typo would silently drop a sum — hard error).
  Check C (sums): per-category ledger totals match the mapped result.json
    lines within tolerance ($1 default). A mismatch FAILS with both figures.
    Fixed-template fields absent from a PRESENT section read as $0 (the
    return reports nothing there), so a dropped 1099 still mismatches.
    Payer-reported figures the return legitimately recomputes (1099-R box
    2a vs line 4b/5b) WARN on mismatch instead of failing; potentially
    taxable informational categories (state refund, personal 1099-K) emit
    loud advisories rather than being silently skipped.
  Check D (verifiability): ledger income whose result SECTION is absent
    FAILS (the form was never computed); a present section whose comparable
    figures are genuinely unavailable (zero-scaffold Schedule D proceeds
    detail) is WARNED loudly, never silently passed.

Pure compute + markdown renderer + CLI, following projection.py/capabilities.py.
Read-only: writes only summaries/_tie_out.md (workspace-bounded), never touches
result.json. CLI exit codes mirror the capability gate: 0 = tied out
(warnings allowed, printed), 2 = FAILED (compute must not be marked complete),
1 = error.

Ledger contract (parse_ledger.json, written by /tax-parse step 6):
  {"records": [
      {"doc_id": "w2-acme-primary",
       "file": "documents/W2_Acme.pdf",
       "form_type": "W-2",
       "payer": "Acme Corp",
       "person": "primary",              # primary | spouse | joint
       "key_boxes": {"wages": 100000.0, "federal_withholding_w2": 12000.0},
       "skipped": null,                  # or a non-empty reason string
       "foreign": false,                 # optional; true = foreign institution
       "second_read": "match"},          # optional; "match"/"mismatch_resolved"
      ...]}
foreign feeds /tax-fbar's set-difference. Bool or absent ONLY — null is a
hard error; when domicile is unknown, OMIT the field and flag the doc for
follow-up instead. second_read is the A3 verification outcome for records
with any key box ≥ $10K (/tax-parse step 6.7); a high-value record without
it draws a tie-out advisory.
key_boxes are keyed by CANONICAL CATEGORY (registry below), mapped from the
raw form boxes at parse time — the skill documents the box→category table.
"""

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_subpath,
)


class TieOutError(Exception):
    """Raised when the tie-out cannot safely evaluate its inputs."""


DEFAULT_TOLERANCE = 1.0  # dollars; whole-dollar rounding across many docs

# Source-document extensions that MUST have a ledger record.
_DOCUMENT_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv"}

# Categories that may legitimately be negative: SSA-1099 box 5 goes negative
# when repayments exceed gross benefits (Pub 915) — taxable SS is then $0 and
# the repayment may be deductible, so a negative total becomes an advisory
# instead of a sum tie (line 6a conventions vary for the repayment case).
_SIGNED_CATEGORIES = {"ss_benefits"}

# Categories captured for cross-checks but not sum-tied to a single line
# (Box 3/5 wages never land on the return; SDI feeds the CA credit inputs;
# a 1099-G refund is taxable only if the prior year itemized and would
# false-mismatch against the aggregate Schedule 1 line 8; a personal-item
# 1099-K belongs on Form 8949, not Schedule C).
_INFORMATIONAL = {"ss_wages", "ss_tips", "medicare_wages", "sdi_withheld",
                  "state_refund_1099g", "personal_1099k"}

# Informational categories that are POTENTIALLY taxable — never silently
# skipped: their presence in the ledger emits a loud advisory (not a
# failure), so a taxable state refund or misclassified 1099-K can't vanish
# without the reviewer seeing a to-verify line.
_INFORMATIONAL_ADVISORIES = {
    "state_refund_1099g": (
        "taxable on Schedule 1 line 1 if the prior year itemized and got a "
        "tax benefit from the deduction — verify against the prior-year "
        "return / answers.md"),
    "personal_1099k": (
        "personal-item sales are not Schedule C income: gains belong on "
        "Form 8949, losses are non-deductible — verify the classification "
        "recorded in answers.md"),
}

# Categories where the ledger figure is PAYER-reported but the return
# legitimately recomputes it: 1099-R box 2a vs 1040 line 4b/5b (Form 8606
# basis, QCD, rollover, 'taxable amount not determined'). A mismatch here is
# a loud ⚠ VERIFY, never an exit-2 — the dropped-document direction is still
# hard-gated by retirement_gross (box 1 vs 4a+5a).
_ADVISORY_MISMATCH_CATEGORIES = {"retirement_taxable"}

# category → (result section, [field names summed on the result side]).
# The comparison is sum(ledger category) == sum(present mapped fields).
CATEGORY_TARGETS = {
    "wages": ("form_1040", ["line_1z_total_wages"]),
    "federal_withholding_w2": ("form_1040", ["line_25a_w2_withholding"]),
    "federal_withholding_1099": ("form_1040", ["line_25b_1099_withholding"]),
    "taxable_interest": ("form_1040", ["line_2b_taxable_interest"]),
    "tax_exempt_interest": ("form_1040", ["line_2a_tax_exempt_interest"]),
    "ordinary_dividends": ("form_1040", ["line_3b_ordinary_dividends"]),
    "qualified_dividends": ("form_1040", ["line_3a_qualified_dividends"]),
    "retirement_gross": ("form_1040", ["line_4a_ira_distributions", "line_5a_pensions"]),
    "retirement_taxable": ("form_1040", ["line_4b_taxable_ira", "line_5b_taxable_pensions"]),
    # Field name matches result_template.json exactly (line_6a_social_security,
    # NOT line_6a_ss_benefits) — a mismatch here demotes a dropped SSA-1099
    # from hard failure to a mere unverifiable warning.
    "ss_benefits": ("form_1040", ["line_6a_social_security"]),
    "state_withholding_ca": ("california_540", ["ca_withholding_box17"]),
    # Form 3921 (box 4 FMV - box 3 strike) x box 5 shares, HELD-through-
    # year-end exercises only (same-year disqualifying dispositions are W-2
    # ordinary income and are recorded as skips). Ties to the input echo
    # /tax-compute step 6.5 writes — a dropped 3921 otherwise leaves the
    # AMT bargain element user-asserted and invisible (D9 review).
    "iso_bargain": ("form_6251", ["iso_bargain_element"]),
    # Broker totals: only verifiable when /tax-compute filled proceeds detail
    # somewhere in schedule_d (either part — a long-term-only return must not
    # be compared against Part I's zero template rows); otherwise Check D
    # warns loudly (never a silent pass).
    "capital_proceeds": ("schedule_d", ["_proceeds_total"]),
    # 1099-DIV box 2a → Schedule D line 13. The template ships the line as 0,
    # which counts as present — a dropped mutual-fund distribution therefore
    # FAILS the sum check rather than hiding as unverifiable.
    "capital_gain_distributions": ("schedule_d", ["part_ii_long_term.line_13_cap_gain_dist"]),
    # 1099-NEC box 1 / 1099-MISC box 3-7 / 1099-K box 1a → Schedule C gross
    # receipts. AT-LEAST check (not equality): gross receipts may legitimately
    # EXCEED the 1099 totals (cash income without an information return), but
    # ledger > result is the under-report direction and fails. Section absent
    # (no schedule_c on the return despite 1099-NEC docs) → Check D warns.
    "self_employment_gross": ("schedule_c", ["line_1_gross_receipts"]),
    # 1099-SA box 1 / (5498-SA + W-2 box 12-W) → Form 8889.
    "hsa_distributions": ("form_8889_hsa", ["line_14a_distributions"]),
    "hsa_contributions": ("form_8889_hsa", ["line_2_contributions", "line_9_employer_contributions"]),
}

# Categories where the return may legitimately report MORE than the ledger
# (cash receipts without a 1099); only ledger > result fails (under-report).
_AT_LEAST_CATEGORIES = {"self_employment_gross"}

# A3: records with any key box at or above this get a second independent
# extraction (/tax-parse step 6.7). Absence is an ADVISORY, not a failure —
# the discipline is enforced at parse time; the gate makes the gap visible.
_SECOND_READ_THRESHOLD = 10000.0

_SECOND_READ_VALUES = {"match", "mismatch_resolved"}

_ALL_CATEGORIES = set(CATEGORY_TARGETS) | _INFORMATIONAL


@dataclass(frozen=True)
class LedgerRecord:
    doc_id: str
    file: str
    form_type: str
    payer: str
    person: str
    key_boxes: "tuple[tuple[str, float], ...]"
    skipped: "str | None"
    # Document is from a foreign institution (foreign brokerage/bank — the
    # Box B/E no-basis convention is a hint). /tax-fbar set-differences these
    # payers against the FBAR account table so an institution that earned
    # interest in the ledger can't silently miss FinCEN 114.
    foreign: bool = False
    # A3 second-read verification outcome for high-value records:
    # "match" (independent re-extraction agreed) or "mismatch_resolved"
    # (disagreed; user confirmed the correct figure, recorded in answers.md).
    second_read: "str | None" = None


@dataclass(frozen=True)
class SumCheck:
    category: str
    ledger_total: float
    result_total: float
    delta: float
    ok: bool
    advisory: bool = False  # mismatch warns (recomputed-on-return) vs fails


@dataclass(frozen=True)
class TieOutReport:
    records: int
    skipped: int
    missing_records: "tuple[str, ...]"  # documents/ files with no ledger record
    phantom_files: "tuple[str, ...]"  # ledger records whose file doesn't exist
    pending_skips: "tuple[str, ...]"  # skips deferring to a skill that never ran — fails
    sum_checks: "tuple[SumCheck, ...]"
    missing_result_targets: "tuple[str, ...]"  # ledger income with NO result section — fails
    unverifiable: "tuple[str, ...]"  # Check D warnings (section present, figures unavailable)
    advisories: "tuple[str, ...]"  # loud manual-verify notes (never a failure)
    failed: bool


def _num(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TieOutError(f"{where} must be a number, got {value!r}")
    v = float(value)
    if not math.isfinite(v):
        raise TieOutError(f"{where} must be finite, got {value!r}")
    return v


def _require_str(value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TieOutError(f"{where} must be a non-empty string, got {value!r}")
    return value.strip()


def load_ledger(raw: dict) -> "list[LedgerRecord]":
    """Validate the parse_ledger.json payload strictly — a malformed ledger
    must never look like a passing tie-out."""
    if not isinstance(raw, dict) or not isinstance(raw.get("records"), list):
        raise TieOutError("parse_ledger.json must be an object with a 'records' list")
    records = []
    seen_ids, seen_files = set(), set()
    for i, rec in enumerate(raw["records"]):
        where = f"records[{i}]"
        if not isinstance(rec, dict):
            raise TieOutError(f"{where} must be an object")
        doc_id = _require_str(rec.get("doc_id"), f"{where}.doc_id")
        if doc_id in seen_ids:
            raise TieOutError(f"duplicate doc_id {doc_id!r} — records must be unique")
        seen_ids.add(doc_id)
        file = _require_str(rec.get("file"), f"{where}.file")
        if file in seen_files:
            raise TieOutError(
                f"duplicate file {file!r} — one physical document must have "
                "exactly ONE record; two records would double-count its "
                "amounts while still passing coverage."
            )
        seen_files.add(file)
        skipped = rec.get("skipped")
        if skipped is not None and (not isinstance(skipped, str) or not skipped.strip()):
            raise TieOutError(f"{where}.skipped must be null or a non-empty reason")
        boxes = rec.get("key_boxes")
        if boxes is None:
            boxes = {}
        if not isinstance(boxes, dict):
            raise TieOutError(f"{where}.key_boxes must be an object")
        if skipped is None and not boxes:
            raise TieOutError(
                f"{where} ({doc_id}): a non-skipped record must carry key_boxes — "
                "an empty parse is either a skip (give the reason) or a bug."
            )
        if skipped is not None and boxes:
            raise TieOutError(
                f"{where} ({doc_id}): a SKIPPED record must not carry key_boxes "
                "— its amounts would earn coverage credit while vanishing from "
                "the sum checks (the exact intake-drop the gate exists to "
                "catch). Either it's a skip (no amounts) or a parse (no skip)."
            )
        validated = []
        for cat, amount in boxes.items():
            if cat not in _ALL_CATEGORIES:
                raise TieOutError(
                    f"{where}.key_boxes: unknown category {cat!r}. Valid: "
                    f"{sorted(_ALL_CATEGORIES)}. A typo here would silently "
                    "drop the amount from the tie-out sums."
                )
            v = _num(amount, f"{where}.key_boxes[{cat}]")
            if v < 0 and cat not in _SIGNED_CATEGORIES:
                raise TieOutError(
                    f"{where}.key_boxes[{cat}] cannot be negative ({v})")
            validated.append((cat, v))
        person = rec.get("person") or "joint"
        if person not in ("primary", "spouse", "joint"):
            raise TieOutError(f"{where}.person must be primary/spouse/joint")
        foreign = rec.get("foreign", False)
        if not isinstance(foreign, bool):
            raise TieOutError(
                f"{where}.foreign must be a boolean, got {foreign!r} — a "
                "truthy string here would silently corrupt the FBAR "
                "set-difference")
        second_read = rec.get("second_read")
        if second_read is not None and second_read not in _SECOND_READ_VALUES:
            raise TieOutError(
                f"{where}.second_read must be one of "
                f"{sorted(_SECOND_READ_VALUES)} or absent, got {second_read!r}"
                " — an unrecognized value would silently count as verified")
        records.append(LedgerRecord(
            doc_id=doc_id, file=file,
            form_type=_require_str(rec.get("form_type"), f"{where}.form_type"),
            payer=_require_str(rec.get("payer"), f"{where}.payer"),
            person=person, key_boxes=tuple(validated), skipped=skipped,
            foreign=foreign, second_read=second_read))
    return records


def _expected_payer_name(entry, where: str) -> str:
    """Extract the institution name from an expected_sources entry — a plain
    string, or the object shapes /tax-rollover writes ('name' for
    employers/payers, 'institution' for foreign accounts)."""
    if isinstance(entry, str):
        if entry.strip():
            return entry.strip()
        raise TieOutError(f"{where} string entries must be non-empty")
    if isinstance(entry, dict):
        for key in ("name", "institution"):
            v = entry.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        raise TieOutError(
            f"{where} object entries must carry a non-empty 'name' or "
            f"'institution', got {entry!r} — an unnamed entry can never "
            "match a ledger payer and would false-alarm forever")
    raise TieOutError(
        f"{where} entries must be strings or objects, got {entry!r}")


def _payer_tokens(name: str) -> frozenset:
    return frozenset(
        t for t in "".join(
            ch if ch.isalnum() else " " for ch in name.casefold()).split()
        if t)


def missing_expected_payers(ledger: "list[LedgerRecord]",
                            expected_sources: dict) -> "list[str]":
    """A4: expected payers (rolled over from the prior year by /tax-rollover)
    with no matching ledger payer — each is a document that existed last year
    and is absent this year (the CP2000 intake gap /tax-parse step 2.5 must
    resolve: closed account, or a missing document). Matching is a
    case/punctuation-insensitive TOKEN-SUBSET test in either direction, so
    "Fidelity" matches "Fidelity Brokerage Services LLC" but "Ally" does NOT
    match "Tally Corp" — a raw substring test would hide exactly the missing
    payer this function exists to surface. A renamed institution false-alarms
    (safe direction: one extra question) rather than false-matching."""
    if not isinstance(expected_sources, dict):
        raise TieOutError("expected_sources must be an object")
    expected = []
    for key, values in expected_sources.items():
        if key.startswith("_"):
            continue
        if not isinstance(values, list):
            raise TieOutError(f"expected_sources.{key} must be a list")
        for v in values:
            # /tax-rollover writes OBJECT entries — {"name", "ein"} for
            # employers, {"name", "country_code"} for payers,
            # {"institution", "country_code", "account_last4"} for foreign
            # accounts. Plain strings are accepted for hand-maintained lists.
            expected.append(_expected_payer_name(v, f"expected_sources.{key}"))
    ledger_tokens = [t for t in (_payer_tokens(r.payer) for r in ledger) if t]
    missing = []
    for name in expected:
        toks = _payer_tokens(name)
        if not toks:
            continue
        if not any(toks <= lt or lt <= toks for lt in ledger_tokens):
            missing.append(name)
    return sorted(set(missing))


def foreign_payers(ledger: "list[LedgerRecord]") -> "list[str]":
    """Sorted unique payers of foreign-institution records — the FBAR
    set-difference input (/tax-fbar step 1.6). Skipped records count too: a
    duplicate scan of a foreign bank's statement still evidences the account."""
    return sorted({r.payer for r in ledger if r.foreign})


def _result_total(result: dict, category: str) -> "float | None":
    """Sum the mapped result-side fields. None ONLY when the section itself
    is absent, or for capital_proceeds without non-zero detail rows — the
    genuinely unverifiable cases. For fixed-template categories a present
    section always yields a number: absent/non-numeric mapped fields count
    as 0, so a return that never filled the line (form_1040 present, no
    line_2b_taxable_interest) produces a $0-vs-ledger MISMATCH instead of
    demoting the dropped 1099 to an unverifiable warning."""
    section_name, fields = CATEGORY_TARGETS[category]
    section = result.get(section_name)
    if not isinstance(section, dict):
        return None
    if category == "capital_proceeds":
        # Proceeds live in detail rows of EITHER Schedule D part. Verifiable
        # only when a NON-ZERO proceeds figure exists somewhere: the template
        # ships zero-valued Part I rows, and treating those as "found" would
        # compare a long-term-only broker's ledger total against $0 and fail a
        # correct return ("section present" = non-zero, per repo convention).
        # Box C/F rows are EXCLUDED: by definition no 1099-B backs them —
        # that's where /tax-parse-crypto merges crypto disposals (its CSVs
        # are ledgered as documented skips, so their proceeds are on the
        # result side only) and where personal no-1099-B sales go. Including
        # them would fail a correct broker+crypto return. A broker row
        # misfiled into Box C/F under-counts the result side → ledger >
        # result → still FAILS (the safe direction).
        total, found = 0.0, False
        for part_key in ("part_i_short_term", "part_ii_long_term"):
            part = section.get(part_key)
            if not isinstance(part, dict):
                continue
            for row_key, row in part.items():
                if "box_c" in row_key or "box_f" in row_key:
                    continue
                if isinstance(row, dict):
                    p = row.get("proceeds")
                    if isinstance(p, (int, float)) and not isinstance(p, bool):
                        total += float(p)
                        if p != 0:
                            found = True
        return total if found else None
    total = 0.0
    for field in fields:
        node = section
        for seg in field.split("."):
            if not isinstance(node, dict) or seg not in node:
                node = None
                break
            node = node[seg]
        if isinstance(node, (int, float)) and not isinstance(node, bool):
            total += float(node)
    return total


def evaluate(ledger: "list[LedgerRecord]", result: dict,
             document_files: "list[str]",
             tolerance: float = DEFAULT_TOLERANCE) -> TieOutReport:
    """Run checks A-D. document_files are workspace-relative paths of the
    source files that exist under documents/."""
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) \
            or not math.isfinite(tolerance) or tolerance < 0:
        raise TieOutError(f"tolerance must be a finite non-negative number, got {tolerance!r}")
    if not isinstance(result, dict):
        raise TieOutError("result.json must be a JSON object")

    ledger_files = {r.file for r in ledger}
    doc_set = set(document_files)
    missing_records = tuple(sorted(doc_set - ledger_files))
    phantom_files = tuple(sorted(ledger_files - doc_set))

    # A "pending <skill>" skip is a promissory note: /tax-parse ledgers
    # unprocessed crypto CSVs as "pending /tax-parse-crypto", and coverage
    # credit for those would let the return complete with the CSV's taxable
    # disposals never imported. The named skill's own record (an upsert by
    # file) replaces the pending skip when it runs.
    pending_skips = tuple(sorted(
        f"{r.file} — {r.skipped}" for r in ledger
        if r.skipped is not None and r.skipped.strip().lower().startswith("pending")))

    sums: "dict[str, float]" = {}
    for rec in ledger:
        if rec.skipped is not None:
            continue
        for cat, amount in rec.key_boxes:
            sums[cat] = sums.get(cat, 0.0) + amount

    sum_checks, unverifiable, missing_targets, advisories = [], [], [], []
    for cat in sorted(sums):
        if cat in _INFORMATIONAL:
            note = _INFORMATIONAL_ADVISORIES.get(cat)
            if note is not None:
                advisories.append(f"{cat} ({sums[cat]:,.2f}): {note}")
            continue
        if cat == "ss_benefits" and sums[cat] < 0:
            # Repayments exceeded gross benefits — taxable SS is $0 and the
            # net-negative case has no stable line-6a convention to tie to.
            advisories.append(
                f"ss_benefits net NEGATIVE ({sums[cat]:,.2f}) — repayments "
                "exceeded gross benefits: taxable SS is $0 and the repayment "
                "may be deductible (Pub 915) — verify; line 6a not sum-tied "
                "for this case")
            continue
        result_total = _result_total(result, cat)
        if result_total is None:
            # A dropped SECTION is a dropped form — ledger income with no
            # result-side section to land on FAILS (a 1099-NEC with no
            # schedule_c means no SE tax was computed; warning was too soft).
            # Only a PRESENT section whose comparable figures are unavailable
            # (zero-scaffold Schedule D proceeds detail) degrades to a warning.
            section_name = CATEGORY_TARGETS[cat][0]
            if isinstance(result.get(section_name), dict):
                unverifiable.append(cat)
            else:
                missing_targets.append(
                    f"{cat} (ledger total {round(sums[cat], 2):,.2f}) — no "
                    f"'{section_name}' section in result.json")
            continue
        delta = round(sums[cat] - result_total, 2)
        # at-least categories: only ledger > result (the under-report
        # direction) fails; a return reporting MORE than the 1099s is legal.
        ok = (delta <= tolerance if cat in _AT_LEAST_CATEGORIES
              else abs(delta) <= tolerance)
        sum_checks.append(SumCheck(
            category=cat, ledger_total=round(sums[cat], 2),
            result_total=round(result_total, 2), delta=delta,
            ok=ok, advisory=cat in _ADVISORY_MISMATCH_CATEGORIES))

    # A3: high-value records (any key box ≥ $10K) need a second independent
    # extraction — a transposed digit there is material. Absence is an
    # advisory (the discipline lives in /tax-parse step 6.7), never a
    # failure.
    for rec in ledger:
        if rec.skipped is not None or rec.second_read is not None:
            continue
        big = max((abs(amt) for _, amt in rec.key_boxes), default=0.0)
        if big >= _SECOND_READ_THRESHOLD:
            advisories.append(
                f"{rec.doc_id}: key box of {big:,.2f} (≥ "
                f"{_SECOND_READ_THRESHOLD:,.0f}) has NO second-read "
                "verification — re-extract the key boxes independently "
                "(/tax-parse step 6.7) before trusting the sums")

    failed = bool(missing_records or phantom_files or pending_skips
                  or missing_targets
                  or any(not c.ok and not c.advisory for c in sum_checks))
    return TieOutReport(
        records=len(ledger), skipped=sum(1 for r in ledger if r.skipped is not None),
        missing_records=missing_records, phantom_files=phantom_files,
        pending_skips=pending_skips,
        sum_checks=tuple(sum_checks),
        missing_result_targets=tuple(missing_targets),
        unverifiable=tuple(unverifiable),
        advisories=tuple(advisories),
        failed=failed)


def render_markdown(report: TieOutReport) -> str:
    lines = ["# Parse-to-return tie-out", ""]
    lines.append(f"- Ledger records: {report.records} ({report.skipped} skipped with reason)")
    lines.append("")
    if report.missing_records:
        lines += ["## ⛔ Documents with NO ledger record (parsed nothing?)", ""]
        lines += [f"- {f}" for f in report.missing_records]
        lines += ["", "Every source file must be parsed or explicitly skipped "
                  "with a reason — a silent drop here is the CP2000 scenario.", ""]
    if report.phantom_files:
        lines += ["## ⛔ Ledger records pointing at missing files", ""]
        lines += [f"- {f}" for f in report.phantom_files]
        lines.append("")
    if report.pending_skips:
        lines += ["## ⛔ Documents skipped PENDING another skill", ""]
        lines += [f"- {s}" for s in report.pending_skips]
        lines += ["", "A pending skip is a to-do, not a resolution — its "
                  "amounts are in no sum. Run the named skill (its record "
                  "replaces the pending skip), then re-run /tax-compute.", ""]
    if report.missing_result_targets:
        lines += ["## ⛔ Ledger income with NO result section to land on", ""]
        lines += [f"- {t}" for t in report.missing_result_targets]
        lines += ["", "The form these amounts belong on was never computed — "
                  "a dropped Schedule C/8889/Schedule D, not a rounding issue.", ""]
    lines += ["## Category sums (ledger vs result.json)", "",
              "| Category | Ledger | Result | Δ | |",
              "|---|---:|---:|---:|---|"]
    for c in report.sum_checks:
        if c.ok:
            mark = "✅"
        elif c.advisory:
            mark = ("⚠ VERIFY — payer-reported figure; the return may "
                    "legitimately recompute it (Form 8606 basis, QCD, "
                    "rollover)")
        else:
            mark = "⛔ MISMATCH"
        lines.append(f"| {c.category} | {c.ledger_total:,.2f} | "
                     f"{c.result_total:,.2f} | {c.delta:,.2f} | {mark} |")
    if not report.sum_checks:
        lines.append("| (no tieable categories in ledger) | | | | |")
    lines.append("")
    if report.unverifiable:
        lines += ["## ⚠ Unverifiable categories (no result-side figures to tie to)", ""]
        lines += [f"- {c}" for c in report.unverifiable]
        lines += ["", "These sums exist in the ledger but the mapped result "
                  "section is absent/empty — verify them manually before "
                  "trusting the return.", ""]
    if report.advisories:
        lines += ["## ⚠ Advisory items (verify manually — not sum-tied)", ""]
        lines += [f"- {a}" for a in report.advisories]
        lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append("**FAILED — do not mark the return complete.**" if report.failed
                 else "**Tied out.**")
    lines.append("")
    return "\n".join(lines)


def list_document_files(workspace: Path) -> "list[str]":
    docs = workspace / "documents"
    if not docs.is_dir():
        return []
    out = []
    for p in sorted(docs.rglob("*")):
        if p.is_file() and not p.name.startswith(".") \
                and p.suffix.lower() in _DOCUMENT_EXTENSIONS:
            out.append(str(p.relative_to(workspace)))
    return out


def write_report(workspace: Path, markdown: str) -> Path:
    summaries = workspace / "summaries"
    summaries.mkdir(exist_ok=True)
    target = _validate_subpath((summaries / "_tie_out.md").resolve(), workspace)
    target.write_text(markdown)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def _read_json(path: Path, what: str) -> dict:
    if not path.exists():
        raise TieOutError(f"{what} not found at {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise TieOutError(f"{what} is malformed: {e}")


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.tie_out")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args(argv)

    try:
        workspace = Path(args.workspace).resolve()
        ledger_path = _validate_subpath((workspace / "parse_ledger.json").resolve(), workspace)
        result_path = _validate_subpath((workspace / "result.json").resolve(), workspace)
        ledger = load_ledger(_read_json(ledger_path, "parse_ledger.json"))
        result = _read_json(result_path, "result.json")
        report = evaluate(ledger, result, list_document_files(workspace),
                          tolerance=args.tolerance)
        markdown = render_markdown(report)
        write_report(workspace, markdown)
    except (TieOutError, RolloverError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.json:
        out = {
            "failed": report.failed,
            "records": report.records,
            "skipped": report.skipped,
            "missing_records": list(report.missing_records),
            "phantom_files": list(report.phantom_files),
            "pending_skips": list(report.pending_skips),
            "sum_checks": [vars(c) for c in report.sum_checks],
            "missing_result_targets": list(report.missing_result_targets),
            "unverifiable": list(report.unverifiable),
            "advisories": list(report.advisories),
        }
        print(json.dumps(out, indent=2))
    else:
        print(markdown)
    return 2 if report.failed else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
