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
from agents.lib import receipt  # noqa: E402
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_subpath,
)
from agents.lib.secure_fs import (  # noqa: E402
    atomic_write,
    is_reparse_point,
    redact,
    secure_path,
)
from agents.lib.tax_computations import (  # noqa: E402
    compute_pfic_1291,
    compute_pfic_excess_distribution,
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
# qualified_tips / qualified_overtime_premium feed the OBBBA Schedule 1-A
# BELOW-AGI deduction (line 13b), not a 1040 income line — informational like
# ss_tips. The deduction is capped and MAGI-phased, so the ledger figure won't
# equal any result line; the record just makes the raw amount traceable.
_INFORMATIONAL = {"ss_wages", "ss_tips", "medicare_wages", "sdi_withheld",
                  "state_refund_1099g", "personal_1099k",
                  "qualified_tips", "qualified_overtime_premium",
                  # Form 3922 ESPP boxes — feed the §423 disposition split and
                  # FUTURE-year basis; no single result line to tie to.
                  "espp_purchase_info"}

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
    "qualified_tips": (
        "feeds the OBBBA Schedule 1-A tip deduction (line 13b) — verify the "
        "computed schedule_1a.tips.allowed / line_13b is nonzero, OR that the "
        "occupation/SSN/MFS rules disqualify it (a parsed tip figure with "
        "line 13b at $0 is a dropped deduction)"),
    "qualified_overtime_premium": (
        "feeds the OBBBA Schedule 1-A overtime deduction (line 13b) — verify "
        "the computed schedule_1a.overtime.allowed / line_13b is nonzero, OR "
        "that eligibility disqualifies it (a parsed premium with line 13b at "
        "$0 is a dropped deduction)"),
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
    # W-2 box 1 ties to line 1a (the pure "total from Forms W-2, box 1"), NOT
    # line 1z. Line 1z = the sum of lines 1a–1h, which can legitimately EXCEED
    # the W-2 total (line 1h carries non-W-2 earned income — e.g. a J2 ESPP
    # disqualifying-disposition ordinary component the former employer omitted).
    # Tying to line 1z would false-fail a correct return with any 1b–1h income.
    "wages": ("form_1040", ["line_1a_wages"]),
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
    # J1: employer-stock (RSU/ESPP/NQSO) Form 8949 code-B basis corrections.
    # The ledger records the POSITIVE sum of (adjusted basis − 1099-B basis) for
    # lots sold this year; the result side is the NEGATED sum of the signed
    # schedule_d `adjustment` fields (negative = reduces gain), so the two tie
    # (see _adjustments_total via the special branch in _result_total). A
    # supplemental statement that parsed into the ledger but whose adjustment
    # never landed in result.json therefore FAILS the hard gate — the
    # double-taxation scenario this category exists to kill.
    "employer_stock_basis_adjustment": ("schedule_d", ["_adjustments_total"]),
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
    pfic_incomplete: "tuple[str, ...]"  # form_8621_pfic present but not all_complete — fails
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
            raise TieOutError(
                f"duplicate doc_id {redact(repr(doc_id))} — records "
                f"must be unique")
        seen_ids.add(doc_id)
        file = _require_str(rec.get("file"), f"{where}.file")
        if file in seen_files:
            raise TieOutError(
                f"duplicate file {redact(repr(file))} — one physical document must have "
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
                f"{where} ({redact(doc_id)}): a non-skipped record must carry "
                "key_boxes — "
                "an empty parse is either a skip (give the reason) or a bug."
            )
        if skipped is not None and boxes:
            raise TieOutError(
                f"{where} ({redact(doc_id)}): a SKIPPED record must not carry key_boxes "
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
            f"'institution', got {redact(repr(entry))} — an unnamed entry "
            "can never match a ledger payer and would false-alarm forever")
    raise TieOutError(
        f"{where} entries must be strings or objects, got "
        f"{redact(repr(entry))}")


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
    if category == "employer_stock_basis_adjustment":
        # Sum the SIGNED `adjustment` across the Schedule D box detail rows
        # flagged `employer_stock`, then NEGATE: employer-stock corrections are
        # negative (basis up → gain down), while the ledger records the positive
        # basis correction, so −Σadjustment ties. Scope on the `employer_stock`
        # boolean provenance marker, NOT the adjustment_code — Form 8949 code
        # 'B' is not unique to employer stock (a non-employer 1099-B basis
        # correction also uses code B), so a code-only filter could pull an
        # unrelated correction into (or offset) the employer-stock total.
        # schedule_d present with no employer-stock rows returns 0.0 (a real "no
        # adjustment landed" figure a nonzero ledger fails against) — None only
        # when the section is absent (already handled above).
        total = 0.0
        for part_key in ("part_i_short_term", "part_ii_long_term"):
            part = section.get(part_key)
            if not isinstance(part, dict):
                continue
            for row in part.values():
                if not isinstance(row, dict):
                    continue
                if row.get("employer_stock") is not True:
                    continue
                adj = row.get("adjustment")
                if isinstance(adj, (int, float)) and not isinstance(adj, bool):
                    total += float(adj)
        return -total
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
    # PFIC dividend reclassification: a broker-reported PFIC distribution is parsed
    # into 1099-DIV box 1a/1b and ledgered there, but the EXCESS portion is REMOVED
    # from line 3b/3a and routed through §1291 (Schedule 1 line 8 as
    # ordinary_income_current). Without accounting for that, the ledger (full box
    # 1a) > the reduced line 3b and the sum-check fails a CORRECTLY reclassified
    # return (Codex round 22). Add the recorded reclassified amount BACK to the
    # result-side dividend total so it ties to the broker box, while the filed line
    # stays correctly reduced.
    #
    # The reclassification add-back is a BOOKKEEPING bridge from the broker's full
    # box-1a/1b to the reduced filed line, NOT an income check — added back in FULL
    # (uncapped). It is NOT capped at total_ordinary_income_current: for a MULTI-YEAR
    # excess distribution only the current-year day allocation is
    # ordinary_income_current, while the prior-year allocations are reflected in the
    # deferred-tax add-on, so a cap at current-year income would reject an otherwise
    # correct multi-year PFIC return (Codex round 28 corrected the round-25 cap). That
    # the removed excess was actually run through §1291 (not just erased from line 3b)
    # is proved by OTHER gates: the all-zero gate fails a positive-event fund with a
    # zero §1291 result, and the multi-year add-on checks fail a multi-year
    # excess/disposition whose prior-year deferred tax was omitted. A PFIC dividend is
    # never qualified, so its nonexcess portion is removed from line 3a but kept in
    # line 3b (recharacterizing qualified → ordinary only raises tax; the box-1a check
    # proves the income stays in line 3b).
    if category == "ordinary_dividends":
        v = _get_num(result, "form_8621_pfic.reclassified_from_line_3b")
        if v is not None and v > 0:
            total += v
    elif category == "qualified_dividends":
        v = _get_num(result, "form_8621_pfic.reclassified_from_line_3a")
        if v is not None and v > 0:
            total += v
    return total


def _get_num(doc: dict, path: str) -> "float | None":
    """Return the FINITE number at a dotted path, or None if absent/non-numeric/
    non-finite. json.loads accepts NaN/Infinity, which pass isinstance(x, float)
    but defeat every `> 0` / `< -tol` comparison (all NaN comparisons are False) —
    so a NaN must read as ABSENT here, never slip through a reconciliation guard."""
    node = doc
    for seg in path.split("."):
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    if isinstance(node, (int, float)) and not isinstance(node, bool) and math.isfinite(node):
        return float(node)
    return None


# US-domicile markers (the PFIC test is the FUND's country of registration; a
# US-domiciled fund is never a PFIC regardless of what it holds or where it is
# custodied).
_US_DOMICILE = {"US", "USA", "U.S.", "UNITED STATES"}
# The ONLY recognized election values. None / "none" → no election (§1291 default,
# computed). "qef"/"mtm" → a timely election (hand-prepared, not §1291-computed).
_NO_ELECTION = {None, "none"}
_ELECTED = {"qef", "mtm"}


def _expected_pfic_fund_ids(profile: dict) -> "tuple[set, bool, list]":
    """Inspect `foreign_holdings_detail.holdings` and return
    ``(expected_ids, any_taxable_recorded, invalid_elections)``:

    - ``expected_ids`` = the set of ``fund_id`` values for NO-ELECTION foreign
      funds that had a §1291 taxable event this year (a distribution OR a
      disposition). Each MUST have one COMPLETE computed form_8621_pfic result
      with a matching ``fund_id`` (a loss disposition still emits its zero-tax
      entry). A no-election taxable fund WITHOUT a fund_id contributes an
      unmatchable ``"__unlabeled_{i}"`` placeholder so the gate fails until the
      preparer assigns a stable id (identity matching — a bare count let two
      entries for one fund hide a second omitted fund; Codex round 10).
    - ``any_taxable_recorded`` = True if ANY taxable fund (elected OR not) is
      recorded, so an elected-only event (hand-prepared Form 8621, no §1291
      result) is NOT failed by the acknowledged-but-nothing-recorded check.
    - ``invalid_elections`` = taxable funds whose ``pfic_election`` is neither a
      recognized election (qef/mtm) nor no-election (none/None) — a typo like
      "unknown" would otherwise be silently treated as elected and skip §1291
      entirely (Codex round 15). The caller FAILS the gate on any of these.
    Only ``qef``/``mtm`` count as elected (hand-prepared). A fund whose
    ``fund_domicile`` is the US is NOT a PFIC (the PFIC test is the FUND's
    domicile) — a US mutual fund held at a foreign broker can legitimately appear
    here, so its distribution/sale is NOT a §1291 event and is skipped entirely
    (Codex round 13). An ABSENT/unknown domicile is treated as potentially foreign
    (fail-closed — the safe direction against understatement).
    """
    detail = profile.get("foreign_holdings_detail")
    holdings = detail.get("holdings") if isinstance(detail, dict) else None
    if not isinstance(holdings, list):
        return set(), False, []
    expected: set = set()
    any_taxable = False
    invalid_elections: list = []
    for i, h in enumerate(holdings):
        if not isinstance(h, dict) or h.get("kind") != "fund":
            continue
        dom = h.get("fund_domicile")
        if isinstance(dom, str) and dom.strip().upper() in _US_DOMICILE:
            continue  # a US-domiciled fund is not a PFIC — not a §1291 event
        taxable = h.get("received_distribution") is True or h.get("disposed") is True
        if not taxable:
            continue
        any_taxable = True
        election = h.get("pfic_election")
        fid = h.get("fund_id") if isinstance(h.get("fund_id"), str) and h.get("fund_id").strip() \
            else f"__unlabeled_{i}"
        if election in _ELECTED:
            continue  # elected funds are hand-prepared, not §1291 auto-computed
        if election not in _NO_ELECTION:
            # An unrecognized election value (typo / "unknown") must NOT be treated
            # as a valid election that skips §1291 — fail closed.
            invalid_elections.append(f"{fid}: pfic_election={election!r}")
            continue
        if fid in expected:
            # Two DISTINCT taxable no-election funds sharing a fund_id would collapse
            # into one expected identity — a single computed result would then cover
            # both while the second fund's tax is never computed (Codex round 16).
            # Give the duplicate an UNMATCHABLE id so coverage fails until the
            # preparer assigns distinct fund_ids.
            expected.add(f"__duplicate_fund_id:{fid}#{i}")
        else:
            expected.add(fid)
    return expected, any_taxable, invalid_elections


def _acq_year(h: dict) -> "int | None":
    """The taxpayer's ACQUISITION calendar year of a holding, or None if the date is
    absent/unparseable. The holding period is measured from `acquisition_date` — NOT
    `pfic_start_date`, which is a separate PFIC-STATUS input and can legitimately fall
    BEFORE acquisition (a fund that was already a PFIC when the taxpayer bought it);
    using it as the acquisition would overstate the holding length (Codex round 30)."""
    v = h.get("acquisition_date")
    if isinstance(v, str) and len(v.strip()) >= 4:
        head = v.strip()[:4]
        if head.isdigit():
            return int(head)
    return None


def _pfic_holding_year_checks(profile: dict, current_year) -> "tuple[set, set, set]":
    """Cross-check each no-election taxable fund against its ACQUISITION DATE (which
    the tie-out trusts far less easily than a self-asserted count). Returns
    ``(holding_mismatch_ids, multiyear_disposition_ids, excess_distribution_multiyear_ids)``:

    - ``holding_mismatch_ids`` = funds whose asserted `preceding_holding_years` is
      LESS than the count derived from the acquisition year (current_year −
      acq_year). Understating the count shrinks the §1291(b) base window and can turn
      a real excess distribution into non-excess ordinary income — the profile
      already carries the dates, so the asserted count must not be shorter than they
      imply (Codex round 28).
    - ``multiyear_disposition_ids`` = funds sold at a positive gain that were held
      across at least one PRIOR tax year (derived ≥ 1). The entire gain is an excess
      distribution (§1291(a)(2)) allocated across the holding period, so the prior
      years MUST produce a deferred-tax add-on; a $1 current allocation with a $0
      add-on cannot be correct for a multi-year gain (Codex round 28).
    - ``excess_distribution_multiyear_ids`` = funds with a positive distribution held
      across at least one prior year — a distribution with an EXCESS (reclassified out
      of line 3b) on such a fund likewise owes prior-year deferred tax."""
    detail = profile.get("foreign_holdings_detail")
    holdings = detail.get("holdings") if isinstance(detail, dict) else None
    if not isinstance(holdings, list):
        return set(), set(), set()
    try:
        cy = int(current_year) if current_year is not None else None
    except (ValueError, TypeError):
        cy = None
    mismatch: set = set()
    multiyear_disp: set = set()
    multiyear_dist: set = set()

    def _finite_pos(v):
        return (isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) and v > 0)

    for h in holdings:
        if not isinstance(h, dict) or h.get("kind") != "fund":
            continue
        dom = h.get("fund_domicile")
        if isinstance(dom, str) and dom.strip().upper() in _US_DOMICILE:
            continue
        if h.get("pfic_election") not in _NO_ELECTION:
            continue
        fid = h.get("fund_id")
        if not (isinstance(fid, str) and fid.strip()):
            continue
        acq = _acq_year(h)
        if acq is None or cy is None:
            continue  # can't derive the holding length → other gates handle it
        derived = cy - acq  # number of prior tax years implied by the dates
        asserted = h.get("preceding_holding_years")
        if (isinstance(asserted, int) and not isinstance(asserted, bool)
                and derived >= 1 and asserted < derived):
            mismatch.add(fid)  # asserted base period too short → understates excess
        if derived >= 1:
            dby = h.get("distributions_by_year")
            amt = dby.get(str(cy), dby.get(cy)) if isinstance(dby, dict) else None
            if h.get("received_distribution") is True and _finite_pos(amt):
                multiyear_dist.add(fid)
            if h.get("disposed") is True and _finite_pos(h.get("disposition_gain_usd")):
                multiyear_disp.add(fid)
    return mismatch, multiyear_disp, multiyear_dist


_RECOMPUTE_DUMMY_RATE = 0.37  # any rate in (0,1]; the ordinary allocation is rate-independent


def _pfic_recompute_checks(profile: dict, result: dict, current_year,
                           tolerance: float,
                           historical_top_rate: "dict | None" = None,
                           underpayment_rates: "list | None" = None) -> "list[str]":
    """SECOND-READ: recompute each taxable no-election fund's §1291 result from the
    profile's holding DATES + reported gain/distribution and compare to the computed
    result, so a self-reported allocation/add-on that does not correspond to the
    actual event is caught. Reuses `compute_pfic_1291` /
    `compute_pfic_excess_distribution`, so a correctly computed return recomputes to
    the same figures.

    Verifies (both the distribution and disposition legs, summed):
    - `ordinary_income_current` == Σ each leg's recomputed CURRENT-year allocation
      (rate-independent). This is the field routed into 1040 income, so it — not the
      auxiliary `disposition_ordinary_income` — must carry the sale's inclusion
      (Codex round 30 #1/#3).
    - `nonexcess_ordinary` == the distribution's recomputed non-excess ordinary_now.
    - `disposition_ordinary_income` == the sale leg's current allocation (round 29).
    - `total_1291_addon` == Σ each leg's recomputed FULL add-on (deferred tax at the
      historical top rates PLUS the §6621 interest at the quarterly rates), when the
      recompute is COMPLETE. This is an EXACT comparison — the interest is verified,
      not just the deferred tax (Codex round 32 #3). `compute_pfic_1291`'s own
      `complete` flag decides which years need a rate (so a same-day-before-year-end
      acquisition that allocates nothing to the prior year needs no prior-year rate;
      round 32 #4). When either rate set is absent/incomplete for a year that DOES
      receive a prior-year allocation, the add-on is UNVERIFIABLE and the fund FAILS
      closed — the untrusted `complete=True` on the result can't substitute (round
      32 #1).

    A NEGATIVE current-year distribution is malformed (distributions can't be
    negative — distinct from a negative `disposition_gain_usd`, a legitimate sale
    loss) and FAILS (round 32 #2).

    Fails CLOSED: a taxable fund whose §1291 cannot be re-derived (missing/malformed
    dates, blocked mid-holding PFIC transition) BLOCKS rather than passing unverified
    (round 30 #1). Holding length is measured from `acquisition_date`; `pfic_start_date`
    is passed only as the separate PFIC-status input (round 30 #4). Returns failure
    messages."""
    failures: list = []
    detail = profile.get("foreign_holdings_detail")
    holdings = detail.get("holdings") if isinstance(detail, dict) else None
    if not isinstance(holdings, list):
        return failures
    pblock = result.get("form_8621_pfic")
    pblock = pblock if isinstance(pblock, dict) else {}
    funds = pblock.get("funds") if isinstance(pblock.get("funds"), list) else []
    # A fund_id must appear AT MOST ONCE in the computed funds[]. A dict keyed by
    # fund_id silently keeps only the last of a duplicate, but the aggregate /
    # line-24 checks sum BOTH — so two identical complete entries for one real fund
    # would double its income + §1291 add-on yet pass every recompute (Codex round
    # 33). Detect duplicates and fail.
    fund_by_id: dict = {}
    _dup_ids: set = set()
    for f in funds:
        if isinstance(f, dict) and isinstance(f.get("fund_id"), str):
            fid_ = f["fund_id"]
            if fid_ in fund_by_id:
                _dup_ids.add(fid_)
            fund_by_id[fid_] = f
    for fid_ in sorted(_dup_ids):
        failures.append(
            f"PFIC fund '{fid_}' has MORE THAN ONE entry in form_8621_pfic.funds — "
            "each fund_id must have exactly one computed result. Duplicate entries "
            "are summed by the aggregate / line-24 reconciliation, doubling the "
            "fund's income and §1291 add-on. Merge them into one entry.")
    try:
        cy = int(current_year) if current_year is not None else None
    except (ValueError, TypeError):
        cy = None
    real_rates = historical_top_rate if (isinstance(historical_top_rate, dict)
                                         and historical_top_rate) else None

    def _finite(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

    def _band(expected):
        # The recompute uses the SAME attested dates/distributions/gain as the
        # original, so a correct return matches to the cent — the allowance is
        # whole-dollar rounding accumulation only (across the two legs), NOT a
        # percentage (a 2% band would let a $1M allocation pass at $980k; Codex
        # round 31). tolerance is the whole-dollar DEFAULT_TOLERANCE.
        return max(tolerance, 0.02)

    def _leg(amount, acq_date, event_date, pfic_start, is_disposition):
        """Recompute one §1291 leg. Returns (ordinary_current, addon_or_None). `addon`
        is the EXACT `total_1291_addon` (deferred tax + §6621 interest) when the
        recompute is fully COMPLETE — `compute_pfic_1291` itself decides which years
        need a rate (round 32 #4), so a genuinely all-current-year event returns a
        complete $0 add-on. `addon` is None when the historical rates OR the §6621
        timeline don't cover a year that receives a prior-year allocation → the
        caller BLOCKS (round 32 #1/#3). Ordinary is rate-independent and always
        returned. A mid-holding PFIC transition raises to the caller."""
        acq_y = int(acq_date[:4])
        dummy = {yr: _RECOMPUTE_DUMMY_RATE for yr in range(acq_y, cy)} \
            or {cy: _RECOMPUTE_DUMMY_RATE}
        if real_rates is not None:
            try:
                rc = compute_pfic_1291(float(amount), acq_date, event_date, cy,
                                       real_rates, underpayment_rates=underpayment_rates,
                                       is_disposition=is_disposition, pfic_start_date=pfic_start)
                if rc.get("complete"):
                    return rc.get("ordinary_income_current"), rc.get("total_1291_addon")
                # rates and/or §6621 interest incomplete for an allocated prior year →
                # add-on unverifiable. Recompute ordinary (rate-independent) via dummy.
                return rc.get("ordinary_income_current"), None
            except ValueError:
                # a present-but-malformed rate — fall through to the dummy recompute
                # (a mid-holding transition re-raises there and propagates).
                pass
        rc = compute_pfic_1291(float(amount), acq_date, event_date, cy, dummy,
                               underpayment_rates=None, is_disposition=is_disposition,
                               pfic_start_date=pfic_start)
        # A COMPLETE dummy recompute (no prior-year allocation → first-year event) has
        # a real $0 add-on; otherwise the add-on is unverifiable without real rates.
        return rc.get("ordinary_income_current"), (0.0 if rc.get("complete") else None)

    for h in holdings:
        if not isinstance(h, dict) or h.get("kind") != "fund":
            continue
        dom = h.get("fund_domicile")
        if isinstance(dom, str) and dom.strip().upper() in _US_DOMICILE:
            continue
        if h.get("pfic_election") not in _NO_ELECTION:
            continue
        fid = h.get("fund_id")
        if not (isinstance(fid, str) and fid.strip()):
            continue
        gain = h.get("disposition_gain_usd")
        has_gain = h.get("disposed") is True and _finite(gain) and gain > 0
        dby = h.get("distributions_by_year")
        dist_amt = dby.get(str(cy), dby.get(cy)) if isinstance(dby, dict) and cy is not None else None
        # #2: a distribution can NEVER be negative (unlike disposition_gain_usd, which
        # may be a legitimate sale loss). A finite negative current-year distribution
        # is malformed — a mistyped sign would otherwise hide taxable income entirely
        # (skipped by both the positive and missing sets), so fail it (Codex round 32).
        if h.get("received_distribution") is True and _finite(dist_amt) and dist_amt < 0:
            failures.append(
                f"PFIC fund '{fid}' has a NEGATIVE current-year distribution "
                f"({dist_amt}) in distributions_by_year — a distribution cannot be "
                "negative. Correct the amount (a sale loss belongs in "
                "disposition_gain_usd, not here) or keep the return blocked.")
        has_dist = h.get("received_distribution") is True and _finite(dist_amt) and dist_amt > 0
        if not (has_gain or has_dist):
            continue
        # #1: a taxable fund with a positive event MUST have a derivable ACQUISITION
        # date + tax year — otherwise the holding-period allocation can't be verified.
        acq_year = _acq_year(h)
        acq_date = h.get("acquisition_date")
        if acq_year is None or cy is None or not isinstance(acq_date, str):
            failures.append(
                f"PFIC fund '{fid}' has a positive taxable event but no derivable "
                "acquisition_date + tax year — the §1291 holding-period allocation "
                "cannot be verified; supply the dates or keep the return blocked.")
            continue
        f = fund_by_id.get(fid)
        if not isinstance(f, dict):
            continue  # coverage gate handles a taxable fund with no computed result
        pfic_start = h.get("pfic_start_date")
        # Use the ASSERTED preceding_holding_years for the excess (what the skill
        # used); the round-28 mismatch gate independently blocks an understated one.
        asserted = h.get("preceding_holding_years")
        preceding = asserted if (isinstance(asserted, int) and not isinstance(asserted, bool)
                                 and asserted >= 0) else (cy - acq_year)
        exp_ordinary = 0.0   # §1291 CURRENT-year allocations only (excess + sale legs)
        exp_addon = 0.0      # Σ each leg's FULL add-on (deferred tax + §6621 interest)
        addon_unverified = False  # a leg whose add-on couldn't be fully recomputed
        broke = False

        def _acc(o, addon):
            nonlocal exp_ordinary, exp_addon, addon_unverified
            if _finite(o):
                exp_ordinary += float(o)
            if addon is None:
                addon_unverified = True
            elif _finite(addon):
                exp_addon += float(addon)

        if has_dist:
            priors, bad = [], False
            if isinstance(dby, dict):
                ymap = {}
                for k, v in dby.items():
                    try:
                        yr = int(k)
                    except (ValueError, TypeError):
                        bad = True
                        break
                    if not _finite(v):
                        bad = True
                        break
                    ymap[yr] = float(v)
                if not bad and preceding >= 1:
                    denom = min(3, preceding)
                    priors = [ymap.get(cy - off, 0.0) for off in range(denom, 0, -1)]
            try:
                ed = compute_pfic_excess_distribution(
                    float(dist_amt), priors, is_first_holding_year=(preceding == 0),
                    preceding_holding_years=preceding)
            except (ValueError, TypeError):
                ed = None
            if ed is None or not ed.get("complete"):
                failures.append(
                    f"PFIC fund '{fid}': the distribution excess could not be "
                    "recomputed from distributions_by_year + the holding length "
                    "(short/malformed history) — supply the full zero-inclusive base "
                    "period or keep the return blocked.")
                broke = True
            else:
                excess = ed.get("excess_distribution") or 0.0
                # The NON-excess portion (ordinary_now) is a normal dividend on line 3b
                # and is recorded in `nonexcess_ordinary`, NOT `ordinary_income_current`
                # — verify it against THAT field only; it does NOT belong in the
                # §1291 current-year total (Codex round 31).
                recorded_ne = f.get("nonexcess_ordinary")
                rne = float(recorded_ne) if _finite(recorded_ne) else None
                exp_ne = float(ed.get("ordinary_now") or 0.0)
                if rne is None or abs(rne - exp_ne) > _band(exp_ne):
                    failures.append(
                        f"PFIC fund '{fid}': nonexcess_ordinary {recorded_ne!r} does "
                        f"not match the recomputed non-excess portion ~${exp_ne:,.2f} "
                        "of the distribution.")
                if excess > tolerance:
                    dist_date = h.get("distribution_date") or h.get("event_date")
                    try:
                        _acc(*_leg(excess, acq_date, dist_date, pfic_start, False))
                    except Exception:  # noqa: BLE001
                        failures.append(
                            f"PFIC fund '{fid}': could not recompute the distribution "
                            "excess §1291 allocation from the dates — verify the "
                            "acquisition/distribution dates.")
                        broke = True

        if has_gain and not broke:
            disp_date = h.get("disposition_date") or h.get("event_date")
            try:
                o, addon = _leg(gain, acq_date, disp_date, pfic_start, True)
            except Exception:  # noqa: BLE001
                failures.append(
                    f"PFIC fund '{fid}': could not recompute the disposition's §1291 "
                    f"allocation from the dates + ${gain:,.2f} gain — verify the dates "
                    "(a blocked mid-holding PFIC transition must be hand-prepared).")
                broke = True
                o = addon = None
            if _finite(o):
                # disposition_ordinary_income (the sale-specific field) must equal the
                # sale leg's current allocation (round 29).
                rec_disp = f.get("disposition_ordinary_income")
                rd = float(rec_disp) if _finite(rec_disp) else None
                if rd is None or abs(rd - float(o)) > _band(float(o)):
                    failures.append(
                        f"PFIC fund '{fid}': disposition_ordinary_income {rec_disp!r} "
                        f"does not match the §1291 current-year allocation of the "
                        f"${gain:,.2f} gain (expected ~${float(o):,.2f}). A multi-year "
                        "gain cannot be reported as a token current-year amount.")
            if not broke:
                _acc(o, addon)

        if broke:
            continue
        # #1/#3: ordinary_income_current (the field reconciled into 1040 income) must
        # equal the SUM of both legs' current allocations — matching the auxiliary
        # disposition field alone does not prove the inclusion reached this total.
        rec_oic = f.get("ordinary_income_current")
        roic = float(rec_oic) if _finite(rec_oic) else None
        if roic is None or abs(roic - exp_ordinary) > _band(exp_ordinary):
            failures.append(
                f"PFIC fund '{fid}': ordinary_income_current {rec_oic!r} does not "
                f"match the recomputed §1291 current-year ordinary allocation "
                f"~${exp_ordinary:,.2f} (distribution + disposition legs). The "
                "current-year inclusion routed into 1040 income is wrong.")
        # §1291 ADD-ON (deferred tax + §6621 interest): if a leg's add-on couldn't be
        # fully recomputed (historical rate OR §6621 timeline missing for an allocated
        # prior year), it is UNVERIFIABLE — the untrusted result `complete=True` can't
        # substitute, so block until the rates are pinned (Codex round 32 #1/#3).
        if addon_unverified:
            failures.append(
                f"PFIC fund '{fid}': the §1291 deferred tax + §6621 interest add-on "
                "cannot be verified — federal.historical_top_rate and/or "
                "federal.underpayment_rates do not cover every prior holding year that "
                "receives an allocation. Pin the historical top rates AND the §6621 "
                "quarterly rates for all prior years, or keep the return blocked.")
            continue
        # The recorded add-on must EXACTLY match the recomputed deferred tax + interest
        # (both from the same rates/dates), not merely cover the deferred tax — so an
        # add-on that omits the §6621 interest FAILS (round 32 #3). Compare EVEN when
        # the recompute is $0 (a wholly-current-year event owes no deferred tax): a
        # nonzero add-on there would add §1291 tax the statute does not impose (Codex
        # round 33). The add-on is verified here (addon_unverified already blocked).
        rec_addon = f.get("total_1291_addon")
        ra = float(rec_addon) if _finite(rec_addon) else None
        if ra is None or abs(ra - exp_addon) > _band(exp_addon):
            failures.append(
                f"PFIC fund '{fid}': total_1291_addon {rec_addon!r} does not match "
                f"the recomputed §1291 add-on ${exp_addon:,.2f} (deferred tax at the "
                "historical top rates + §6621 interest; $0 when the event is wholly "
                "allocated to the current year). The deferred tax or its interest is "
                "mis-stated — recompute the per-year allocation.")
    return failures


def _pfic_positive_event_ids(profile: dict, current_year) -> "tuple[set, set, set]":
    """Classify each no-election taxable fund's ATTESTED event and return
    ``(positive_ids, missing_amount_ids, disposition_positive_ids)``:

    - ``positive_ids`` = funds with a current-year distribution > 0 or a
      disposition_gain_usd > 0 — the computed §1291 result MUST carry a positive
      amount (an all-zero result silently drops it; Codex round 20). A below-
      threshold broker-reported distribution is NOT exempt: it must record its
      nonexcess amount in `nonexcess_ordinary` so it flows through the income
      reconciliation + the 1099-DIV parse-ledger tie-out (Codex round 27 retired the
      unverifiable `distribution_in_parsed_docs` exemption).
    - ``missing_amount_ids`` = funds whose event MAGNITUDE is unknown — a
      `disposed` sale with no finite `disposition_gain_usd`, or a
      `received_distribution` with no finite current-year `distributions_by_year`
      entry. Without the amount the tie-out cannot tell a real gain/positive
      distribution from a loss/zero, so it fails closed (Codex round 21).
    - ``disposition_positive_ids`` = EVERY fund with a positive disposition gain
      (whether or not it also had a distribution). Its computed entry must carry a
      positive DISPOSITION-specific `disposition_ordinary_income` (the sale leg's
      current-year §1291 ordinary allocation, ALWAYS > 0 for a gain). The all-zero
      check sums total_1291_addon + ordinary_income_current + nonexcess_ordinary, but
      `nonexcess_ordinary` is a DISTRIBUTION-only concept — so a sale-at-gain fund
      with a stray $10 nonexcess and zero real §1291 would otherwise pass while the
      gain's tax was omitted (Codex round 25/26). Requiring the disposition-specific
      allocation proves the sale itself was run through §1291, for pure-disposition
      AND same-year distribution+sale funds alike.
    A LOSS/zero-gain disposition and a $0 distribution legitimately compute $0.
    Mirrors the filtering in `_expected_pfic_fund_ids` (fund, non-US, no-election,
    real fund_id)."""
    detail = profile.get("foreign_holdings_detail")
    holdings = detail.get("holdings") if isinstance(detail, dict) else None
    if not isinstance(holdings, list):
        return set(), set(), set()
    positive: set = set()
    missing: set = set()
    disp_positive: set = set()

    def _finite(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

    for h in holdings:
        if not isinstance(h, dict) or h.get("kind") != "fund":
            continue
        dom = h.get("fund_domicile")
        if isinstance(dom, str) and dom.strip().upper() in _US_DOMICILE:
            continue
        if h.get("pfic_election") not in _NO_ELECTION:
            continue
        fid = h.get("fund_id")
        if not (isinstance(fid, str) and fid.strip()):
            continue  # unlabeled funds already fail coverage
        if h.get("received_distribution") is True:
            dby = h.get("distributions_by_year")
            amt = dby.get(str(current_year), dby.get(current_year)) \
                if isinstance(dby, dict) and current_year is not None else None
            if not _finite(amt):
                missing.add(fid)  # distribution magnitude unknown
            elif amt > 0:
                positive.add(fid)
        if h.get("disposed") is True:
            gain = h.get("disposition_gain_usd")
            if not _finite(gain):
                missing.add(fid)  # gain/loss unknown → can't tell gain from loss
            elif gain > 0:
                positive.add(fid)
                disp_positive.add(fid)
    return positive, missing, disp_positive


def category_sums(ledger: "list[LedgerRecord]") -> "dict[str, float]":
    """Total per canonical category across the ledger — the ONE reading of it.

    A SKIPPED record contributes nothing: its `skipped` reason is the whole
    record, and counting a skip's boxes would give coverage credit to amounts
    the parse deliberately did not take. `return_builder` derives the 1040's
    record-backed income lines from this, and `evaluate` compares the return
    against it; one function so the return can never be built from a different
    reading of the ledger than the gate checks it against.
    """
    sums: "dict[str, float]" = {}
    for rec in ledger:
        if rec.skipped is not None:
            continue
        for cat, amount in rec.key_boxes:
            sums[cat] = sums.get(cat, 0.0) + amount
    return sums


def evaluate(ledger: "list[LedgerRecord]", result: dict,
             document_files: "list[str]",
             tolerance: float = DEFAULT_TOLERANCE,
             pfic_event_expected: bool = False,
             expected_pfic_fund_ids: "set | None" = None,
             any_taxable_pfic_recorded: bool = False,
             invalid_pfic_elections: "list | None" = None,
             pfic_positive_event_ids: "set | None" = None,
             pfic_missing_amount_ids: "set | None" = None,
             pfic_disposition_positive_ids: "set | None" = None,
             pfic_holding_mismatch_ids: "set | None" = None,
             pfic_multiyear_disposition_ids: "set | None" = None,
             pfic_recompute_failures: "list | None" = None) -> TieOutReport:
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

    sums = category_sums(ledger)

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

    # PFIC §1291 completeness (machine-checked, not just the tax-compute doc): a
    # §1291 result that computed but returned complete=False (short distribution
    # history, missing historical top rate, or an uncovered §6621 quarter) is
    # punitive-and-understated — it must NOT let the return complete. The compute
    # writes form_8621_pfic.all_complete (and per-fund `complete`); a present
    # block that is not all-complete FAILS the gate here rather than relying on
    # the acknowledgment path (Codex: an acknowledged pfic_taxable_event otherwise
    # exits cleanly regardless of the computed completeness).
    pfic_incomplete = []
    pfic_block = result.get("form_8621_pfic") if isinstance(result.get("form_8621_pfic"), dict) else {}
    funds = pfic_block.get("funds")
    # A COMPUTED §1291 result carries actual funds. /tax-setup copies
    # result_template.json which ships the scaffold (all_complete: false, funds:
    # []) on EVERY return, so an empty funds list is NOT a computed result —
    # gating on it would fail every non-PFIC return (Codex P1).
    computed_funds = [f for f in funds if isinstance(f, dict)] if isinstance(funds, list) else []
    expected_ids = expected_pfic_fund_ids if isinstance(expected_pfic_fund_ids, set) else set()
    # A PFIC taxable event was acknowledged (we only reach the tie-out with the
    # capability gate passed) but NO taxable fund is recorded in
    # foreign_holdings_detail — the attested holding dates / distributions /
    # election / fund identity the §1291 computation requires are missing. Fail
    # closed REGARDLESS of whether a funds[] entry exists (Codex round 11): a
    # fabricated complete entry with no recorded holding must not pass. An
    # elected-only event (QEF/MTM, hand-prepared Form 8621, no §1291 result) DOES
    # record its fund, so any_taxable_pfic_recorded is True and this does not fire.
    if pfic_event_expected and not any_taxable_pfic_recorded:
        pfic_incomplete.append(
            "A PFIC taxable event was acknowledged (flags.has_pfic_taxable_event) "
            "but NO taxable fund is recorded in foreign_holdings_detail — the "
            "attested holding dates / distributions / election / fund_id the §1291 "
            "computation requires are missing. Record the taxable fund (and compute "
            "it, unless QEF/MTM-elected) or keep the return blocked; do not file "
            "with the event unreported.")
    # Flag/holding CONTRADICTION: foreign_holdings_detail records a taxable fund
    # (distribution/disposition) but the flag says there is no PFIC event. A stale
    # or contradictory flag would then bypass the capability BLOCK entirely — an
    # elected (QEF/MTM) taxable fund escapes its hand-prepared Form 8621, a
    # no-election fund escapes §1291 (Codex round 12). Derive the event from the
    # recorded holding: fail closed on the mismatch so the flag must be corrected
    # (which re-arms the capability gate).
    if any_taxable_pfic_recorded and not pfic_event_expected:
        pfic_incomplete.append(
            "foreign_holdings_detail records a taxable foreign fund (distribution "
            "or disposition) but flags.has_pfic_taxable_event is not set — a "
            "flag/holding contradiction. Set the flag (so the capability gate "
            "blocks and the fund is §1291-computed, or hand-prepared if QEF/MTM) or "
            "correct the holding; a stale flag must not bypass Form 8621.")
    # An UNRECOGNIZED pfic_election value (a typo like "unknown") must not be
    # silently treated as a valid QEF/MTM election that skips §1291 (Codex round 15).
    if invalid_pfic_elections:
        pfic_incomplete.append(
            "foreign_holdings_detail has a taxable fund with an unrecognized "
            "pfic_election (" + "; ".join(str(x) for x in invalid_pfic_elections) +
            ") — only 'none' (§1291-computed) or 'qef'/'mtm' (hand-prepared) are "
            "valid. Correct the election; an unknown value must not skip Form 8621.")
    if computed_funds:
        incomplete_funds = [f for f in computed_funds if f.get("complete") is not True]
        if pfic_block.get("all_complete") is not True or incomplete_funds:
            pfic_incomplete.append(
                "form_8621_pfic is present but not all_complete — the §1291 "
                "tax is incomplete (short distribution history / missing "
                "historical top rate / uncovered §6621 quarter). The return "
                "must not be marked complete; fetch the missing law or "
                "prepare Form 8621 with a preparer.")
        # Every COMPLETE computed fund MUST carry finite amount fields — a fund
        # marked complete with only a fund_id lets _fund_sum read the missing
        # amounts as 0, so a positive distribution/gain leaves the aggregates at
        # their template default and skips both reconciliations (Codex round 15).
        # $0 is valid (a genuine zero-tax loss disposition), but the fields must be
        # PRESENT and finite.
        amount_missing = []
        for f in computed_funds:
            if f.get("complete") is not True:
                continue
            for key in ("total_1291_addon", "ordinary_income_current", "nonexcess_ordinary"):
                v = f.get(key)
                if not (isinstance(v, (int, float)) and not isinstance(v, bool)
                        and math.isfinite(v)):
                    amount_missing.append(f"{f.get('fund_id', '?')!r}.{key}")
        if amount_missing:
            pfic_incomplete.append(
                "a COMPLETE form_8621_pfic fund is missing a finite amount field (" +
                ", ".join(amount_missing) + ") — every computed fund must carry "
                "total_1291_addon, ordinary_income_current, AND nonexcess_ordinary "
                "(the non-excess normal-dividend portion; 0 is valid for a zero-tax "
                "event or a disposition, but the fields must be present) so the "
                "line-15/16 reconciliation cannot be skipped by treating absent "
                "amounts as $0.")
    # Per-fund coverage BY IDENTITY: every no-election taxable fund
    # (foreign_holdings_detail) must have a COMPLETE computed result carrying its
    # `fund_id`. A bare count let two entries for one fund (or generic
    # {complete: true} entries with no id) satisfy the check while a second taxable
    # fund was silently omitted — the exact underreporting this gate prevents
    # (Codex round 10). Match on fund_id so duplicates and unlabeled entries can't
    # cover a missing fund.
    complete_ids = {f.get("fund_id") for f in computed_funds
                    if f.get("complete") is True and isinstance(f.get("fund_id"), str)}
    missing_ids = expected_ids - complete_ids
    if missing_ids:
        shown = ", ".join(sorted(str(m) for m in missing_ids))
        pfic_incomplete.append(
            f"no-election taxable PFIC fund(s) [{shown}] in foreign_holdings_detail "
            "have NO complete computed §1291 result with a matching fund_id — a "
            "taxable fund was left uncomputed, unlabeled, or DUPLICATE-labeled "
            "(a __duplicate_fund_id token means two taxable funds share a fund_id). "
            "Compute §1291 for every taxable fund (a loss disposition still needs "
            "its complete zero-tax entry) and give each holding + computed fund a "
            "stable, DISTINCT fund_id, or keep the return blocked.")
    def _fund_num(f, key):
        """A computed fund's numeric field as a FINITE float, or None."""
        v = f.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            return float(v)
        return None

    def _fund_sum(key):
        return sum(n for f in computed_funds if (n := _fund_num(f, key)) is not None)

    # A fund whose ATTESTED event is POSITIVE (a distribution > 0 or a disposition
    # GAIN > 0, per foreign_holdings_detail) must produce a computed result with a
    # POSITIVE amount — its complete fund cannot be all-zero (Codex round 20). An
    # all-zero result is legitimate only for a documented zero distribution or a
    # LOSS/zero-gain disposition. Without this, a real taxable event could carry an
    # all-zero §1291 fund, pass ID coverage, skip the positive-only reconciliation
    # branches, and contribute no income or tax.
    positive_ids = pfic_positive_event_ids if isinstance(pfic_positive_event_ids, set) else set()
    missing_ids2 = pfic_missing_amount_ids if isinstance(pfic_missing_amount_ids, set) else set()
    disposition_positive_ids = pfic_disposition_positive_ids \
        if isinstance(pfic_disposition_positive_ids, set) else set()
    if positive_ids:
        zero_result_ids = []
        for f in computed_funds:
            fid = f.get("fund_id")
            if not (isinstance(fid, str) and fid in positive_ids):
                continue
            # A broker-reported BELOW-THRESHOLD distribution is NOT exempt from this
            # check: its entire (nonexcess) amount must be recorded in
            # `nonexcess_ordinary` (> 0), which keeps the fund out of the all-zero set
            # AND routes the income through the ordinary-income → line 9/11/15
            # reconciliation below. That, together with the parse-ledger 1099-DIV
            # tie-out, is what proves the income is actually on the return — an
            # unverifiable `distribution_in_parsed_docs` flag never could (a
            # foreign-statement distribution NOT in any parsed 1099-DIV would
            # otherwise be marked broker-reported and omitted entirely; Codex
            # round 27). There is no broker-reported exemption.
            total = sum(v for k in ("total_1291_addon", "ordinary_income_current",
                                    "nonexcess_ordinary")
                        if (v := _fund_num(f, k)) is not None)
            if total <= tolerance:
                zero_result_ids.append(fid)
        if zero_result_ids:
            pfic_incomplete.append(
                "PFIC fund(s) [" + ", ".join(sorted(zero_result_ids)) + "] have an "
                "attested POSITIVE event (a distribution > 0 or a disposition gain "
                "> 0 in foreign_holdings_detail) but an ALL-ZERO computed §1291 "
                "result (total_1291_addon + ordinary_income_current + "
                "nonexcess_ordinary = 0). A real taxable event must produce income "
                "or tax; recompute it (zero results are only for a $0 distribution or "
                "a loss/zero-gain disposition). A below-threshold broker-reported "
                "distribution must record its nonexcess amount in nonexcess_ordinary "
                "so it flows through the income reconciliation and the 1099-DIV "
                "parse-ledger tie-out.")
    # A recorded sale with NO finite gain/loss, or a distribution with NO finite
    # current-year amount, leaves the event magnitude unknown — the tie-out cannot
    # tell a taxable gain/positive distribution from a loss/zero, so it must fail
    # closed rather than accept an all-zero result (Codex round 21).
    if missing_ids2:
        pfic_incomplete.append(
            "PFIC fund(s) [" + ", ".join(sorted(str(m) for m in missing_ids2)) +
            "] have a recorded disposition or distribution with NO finite amount "
            "(disposition_gain_usd / the current-year distributions_by_year entry) "
            "in foreign_holdings_detail — the event magnitude is unknown, so the "
            "§1291 result cannot be verified. Supply the gain/loss and the "
            "current-year distribution amount, or keep the return blocked.")

    # DISPOSITION GAIN: EVERY fund sold at a positive gain must carry a positive
    # disposition-specific `disposition_ordinary_income` (the sale leg's current-year
    # §1291 ordinary allocation, ALWAYS > 0 for a gain). The all-zero check sums
    # total_1291_addon + ordinary_income_current + nonexcess_ordinary, but
    # `nonexcess_ordinary` is a DISTRIBUTION-only concept — so a sale-at-gain fund
    # with a stray $10 nonexcess and zero real §1291 would pass while the gain's tax
    # was omitted (Codex round 26), and a same-year distribution+sale fund could hide
    # the sale behind a small distribution result (round 25). Requiring the
    # disposition-specific allocation proves the sale itself was run through §1291.
    if disposition_positive_ids:
        disp_bad = []
        for f in computed_funds:
            fid = f.get("fund_id")
            if not (isinstance(fid, str) and fid in disposition_positive_ids):
                continue
            disp_ord = _fund_num(f, "disposition_ordinary_income")
            if disp_ord is None or disp_ord <= tolerance:
                disp_bad.append(fid)
        if disp_bad:
            pfic_incomplete.append(
                "PFIC fund(s) [" + ", ".join(sorted(disp_bad)) + "] were sold at a "
                "positive gain but the computed entry does not carry a positive "
                "disposition_ordinary_income (the sale leg's current-year §1291 "
                "ordinary allocation, always > 0 for a gain). Neither the all-zero "
                "check nor the ordinary/add-on reconciliation can prove the sale's "
                "§1291 was computed — a stray nonexcess_ordinary (a distribution-only "
                "field) or a small distribution result would otherwise mask an omitted "
                "gain. Compute the disposition (is_disposition=True) and record "
                "disposition_ordinary_income, or the sale's deferred tax + interest "
                "may be silently omitted.")

    # HOLDING-PERIOD CROSS-CHECK: the asserted preceding_holding_years must not be
    # shorter than the acquisition date implies. Understating it shrinks the §1291(b)
    # base window and can turn a real excess distribution into non-excess ordinary
    # income (Codex round 28). Derived from the profile dates in _main.
    holding_mismatch_ids = pfic_holding_mismatch_ids \
        if isinstance(pfic_holding_mismatch_ids, set) else set()
    if holding_mismatch_ids:
        pfic_incomplete.append(
            "PFIC fund(s) [" + ", ".join(sorted(str(m) for m in holding_mismatch_ids)) +
            "] assert a preceding_holding_years SHORTER than their acquisition date "
            "implies (current tax year − acquisition year). A too-short base period "
            "raises the §1291(b) 125% threshold and can wrongly reclassify a real "
            "excess distribution as non-excess ordinary income. Set "
            "preceding_holding_years to the full holding length (with a $0 "
            "distributions_by_year entry for every year in between).")

    # MULTI-YEAR DISPOSITION add-on: the entire gain on a sale is an excess
    # distribution allocated across the holding period, so a gain on a fund held ≥ 1
    # prior tax year MUST produce a prior-year deferred-tax add-on. A $1
    # disposition_ordinary_income with a $0 add-on cannot be right for a multi-year
    # gain — the disposition-specific field alone doesn't prove the prior-year §1291
    # tax was computed (Codex round 28).
    multiyear_disp_ids = pfic_multiyear_disposition_ids \
        if isinstance(pfic_multiyear_disposition_ids, set) else set()
    if multiyear_disp_ids:
        no_addon = []
        for f in computed_funds:
            fid = f.get("fund_id")
            if not (isinstance(fid, str) and fid in multiyear_disp_ids):
                continue
            addon = _fund_num(f, "total_1291_addon")
            if addon is None or addon <= tolerance:
                no_addon.append(fid)
        if no_addon:
            pfic_incomplete.append(
                "PFIC fund(s) [" + ", ".join(sorted(no_addon)) + "] were sold at a "
                "gain after being held across at least one PRIOR tax year, but carry "
                "a ZERO total_1291_addon. The gain is an excess distribution (§1291"
                "(a)(2)) allocated across the holding period, so the prior years owe "
                "deferred tax + §6621 interest — a multi-year gain cannot have a $0 "
                "add-on. Recompute the disposition's per-year §1291 allocation.")

    # RECOMPUTE-VERIFY: per-fund §1291 allocations re-derived from the profile dates +
    # gain/distribution (in _main via _pfic_recompute_checks) that do NOT match the
    # computed result — a disposition_ordinary_income that doesn't correspond to the
    # gain, a per-fund excess with a $0 add-on, or a taxable fund with no derivable
    # holding dates. This replaces the round-28 AGGREGATE excess-add-on guard with a
    # PER-FUND one (another fund's add-on can no longer cover a fund whose excess was
    # untaxed; Codex round 29).
    for msg in (pfic_recompute_failures or []):
        if isinstance(msg, str) and msg:
            pfic_incomplete.append(msg)

    def _agg_nonfinite(key):
        """True iff pfic_block[key] is PRESENT as a non-finite number (NaN/Inf)."""
        raw = pfic_block.get(key)
        return (isinstance(raw, (int, float)) and not isinstance(raw, bool)
                and not math.isfinite(raw))

    # §1291 deferred tax + interest and current ordinary income are ALL amounts
    # that only INCREASE tax — a negative or non-finite figure is malformed
    # (deferred tax / §6621 interest / an excess-distribution ordinary portion can
    # never be negative or NaN) and would sail past the reconciliation below,
    # understating the return (Codex round 12/13/14). Check EACH fund individually
    # — a bare aggregate lets a -500 in one fund be masked by a +1000 in another
    # (round 13) — AND the aggregates, INCLUDING a present-but-non-finite aggregate
    # that _get_num turns into None (round 14).
    _addon_raw = _get_num(pfic_block, "total_1291_addon")
    _ord_raw = _get_num(pfic_block, "total_ordinary_income_current")
    _nonexcess_raw = _get_num(pfic_block, "total_nonexcess_ordinary")
    neg_bits = []
    for agg_key in ("total_1291_addon", "total_ordinary_income_current",
                    "total_nonexcess_ordinary"):
        if _agg_nonfinite(agg_key):
            neg_bits.append(f"{agg_key}={pfic_block.get(agg_key)!r} (non-finite)")
    if _addon_raw is not None and _addon_raw < -tolerance:
        neg_bits.append(f"total_1291_addon {_addon_raw:,.2f}")
    if _ord_raw is not None and _ord_raw < -tolerance:
        neg_bits.append(f"total_ordinary_income_current {_ord_raw:,.2f}")
    if _nonexcess_raw is not None and _nonexcess_raw < -tolerance:
        neg_bits.append(f"total_nonexcess_ordinary {_nonexcess_raw:,.2f}")
    for f in computed_funds:
        for key in ("total_1291_addon", "ordinary_income_current", "nonexcess_ordinary"):
            raw = f.get(key)
            # A present-but-non-finite value is malformed; a finite negative is
            # mis-signed. Both must fail (a NaN would otherwise defeat every guard).
            if raw is None:
                continue
            n = _fund_num(f, key)
            if n is None or n < -tolerance:
                neg_bits.append(f"fund {f.get('fund_id', '?')!r} {key}={raw!r}")
    if neg_bits:
        pfic_incomplete.append(
            "form_8621_pfic has a NEGATIVE or non-finite §1291 figure (" +
            "; ".join(neg_bits) + ") — deferred tax, §6621 interest, and the "
            "current-year ordinary portion can never be negative or NaN. A "
            "malformed/mis-signed result must not pass; recompute (a loss "
            "disposition is a Schedule D capital loss, not a §1291 add-on).")

    # Line-24 (TOTAL TAX) reconciliation: a complete nonzero §1291 add-on must
    # actually be IN the filed tax, and by the right amount. all_complete proves the
    # funds are computed, not that total_1291_addon reached Form 1040 (Codex round
    # 10). Two self-reported fields that merely agree do NOT prove the add-on
    # actually INCREASED the tax (round 11).
    #
    # The §1291 deferred tax + §6621 interest is an ADDITIONAL TAX added to TOTAL TAX
    # (Form 1040 line 24) AFTER nonrefundable credits — the deferred tax entered to
    # the LEFT of line 24, the interest via Schedule 2 line 17p -> line 23 (IRS
    # Instructions for Form 8621, Rev. Dec 2025, Part V line 16). It must NOT be
    # folded into line 16 regular tax: line 16 feeds the pre-credit base (line 22 =
    # line 16 + line 17 − credits), so a §1291 charge on line 16 would be eroded by
    # the CTC/other nonrefundable credits and UNDERSTATE the tax (Codex round 23).
    # So (a) line 16 carries NO add-on — form_1040.line_16_tax == line_16_regular_tax
    # — and (b) form_1040.line_24_total_tax == line_24_regular_total_tax (the
    # counterfactual total tax WITHOUT the add-on) + Σ per-fund add-ons. Trigger when
    # EITHER the aggregate OR the per-fund sum is nonzero (round 14).
    fund_addons = _fund_sum("total_1291_addon")
    if (_addon_raw is not None and _addon_raw > tolerance) or fund_addons > tolerance:
        line_16 = _get_num(result, "form_1040.line_16_tax")
        line_16_reg = _get_num(pfic_block, "line_16_regular_tax")
        line_24 = _get_num(result, "form_1040.line_24_total_tax")
        recorded_addon = _get_num(pfic_block, "line_24_addon")
        with_addon = _get_num(pfic_block, "line_24_with_addon")
        regular_24 = _get_num(pfic_block, "line_24_regular_total_tax")
        ok = (_addon_raw is not None and abs(_addon_raw - fund_addons) <= tolerance
              and recorded_addon is not None and abs(recorded_addon - fund_addons) <= tolerance
              # line 16 must NOT include the add-on (credits would erode it):
              and line_16 is not None and line_16_reg is not None
              and abs(line_16 - line_16_reg) <= tolerance
              # the add-on lands in TOTAL TAX (line 24), after credits:
              and with_addon is not None and line_24 is not None
              and abs(with_addon - line_24) <= tolerance
              and regular_24 is not None
              and abs((regular_24 + fund_addons) - line_24) <= tolerance)
        if not ok:
            pfic_incomplete.append(
                f"§1291 add-on (Σ funds = {fund_addons:,.2f}) is not arithmetically "
                "reconciled into Form 1040 TOTAL TAX (line 24): require "
                "total_1291_addon == Σ funds' total_1291_addon == line_24_addon, "
                "form_1040.line_16_tax == line_16_regular_tax (the add-on is NOT on "
                "line 16 — credits would erode it), line_24_regular_total_tax + "
                "add-on == form_1040.line_24_total_tax, and line_24_with_addon == "
                "form_1040.line_24_total_tax. The §1291 deferred tax + interest is an "
                "additional tax added AFTER nonrefundable credits (to the left of "
                "line 24 / Schedule 2 line 17p); on line 16 it would be reduced by the "
                "CTC and understate the filed tax.")

    # Current-year ORDINARY income reconciliation: TWO ordinary components are
    # taxed at normal rates THIS year and BOTH must reach taxable income — the
    # excess-distribution current-year allocation (`ordinary_income_current`) AND
    # the NON-excess normal-dividend portion (`nonexcess_ordinary`, from
    # compute_pfic_excess_distribution). A below-threshold distribution has $0
    # excess (so total_1291_addon and ordinary_income_current are 0) but a positive
    # nonexcess_ordinary that would otherwise be omitted entirely when supplied via
    # foreign_holdings_detail rather than a parsed 1099-DIV (Codex round 17). Anchor
    # the FULL current PFIC ordinary (both components) ARITHMETICALLY to the filed
    # line 15 (round 12/14/15). Trigger on any aggregate OR per-fund component.
    fund_ord = _fund_sum("ordinary_income_current")
    fund_nonexcess = _fund_sum("nonexcess_ordinary")
    pfic_ordinary = fund_ord + fund_nonexcess
    triggered = ((_ord_raw is not None and _ord_raw > tolerance)
                 or (_nonexcess_raw is not None and _nonexcess_raw > tolerance)
                 or fund_ord > tolerance or fund_nonexcess > tolerance)
    if triggered:
        in_line15 = _get_num(pfic_block, "line_15_pfic_ordinary_income")
        line_15 = _get_num(result, "form_1040.line_15_taxable_income")
        regular_15 = _get_num(pfic_block, "line_15_regular_taxable_income")
        # line_15_regular_taxable_income is the COUNTERFACTUAL line 15 WITHOUT the
        # PFIC ordinary income. Routing PFIC income through gross income/AGI can
        # push line 15 up by MORE than the PFIC amount itself — more taxable Social
        # Security once provisional income crosses a threshold, AGI-limited
        # deductions shrinking, etc. (Codex round 20). So require line 15 to grow by
        # AT LEAST the PFIC ordinary income (>=), not EXACTLY — an exact-equality
        # check false-rejects a correctly-recomputed threshold return, while `>=`
        # still catches PFIC income OMITTED from line 15 (which leaves line 15 short).
        ok = (_ord_raw is not None and abs(_ord_raw - fund_ord) <= tolerance
              and _nonexcess_raw is not None and abs(_nonexcess_raw - fund_nonexcess) <= tolerance
              and in_line15 is not None and abs(in_line15 - pfic_ordinary) <= tolerance
              and line_15 is not None and regular_15 is not None
              and line_15 >= (regular_15 + pfic_ordinary) - tolerance)
        if not ok:
            pfic_incomplete.append(
                f"§1291 current-year ordinary income (Σ funds' ordinary_income_"
                f"current {fund_ord:,.2f} + nonexcess_ordinary {fund_nonexcess:,.2f} "
                f"= {pfic_ordinary:,.2f}) is not reconciled into taxable income: "
                "require total_ordinary_income_current == Σ funds' "
                "ordinary_income_current, total_nonexcess_ordinary == Σ funds' "
                "nonexcess_ordinary, line_15_pfic_ordinary_income == their SUM, AND "
                "form_1040.line_15_taxable_income >= line_15_regular_taxable_income "
                "(the counterfactual line 15 WITHOUT PFIC income) + that sum — line "
                "15 must grow by AT LEAST the PFIC ordinary income (more if it also "
                "raised taxable Social Security or shrank AGI-limited deductions). A "
                "non-excess PFIC distribution or the excess current-year portion "
                "omitted from line 15 understates the tax.")
        # The line-15 check alone does not prove the income flowed through GROSS
        # income → AGI; a preparer could bolt it onto line 15 while leaving lines
        # 9/11 unchanged, omitting its effect on taxable SS, NIIT/MAGI, and
        # AGI-limited items (Codex round 21). Require line 9 (total income) AND line
        # 11 (AGI) to each grow by AT LEAST the PFIC ordinary income, measured from
        # the recorded counterfactual (WITHOUT PFIC income) baselines.
        line_9 = _get_num(result, "form_1040.line_9_total_income")
        regular_9 = _get_num(pfic_block, "line_9_regular_total_income")
        line_11 = _get_num(result, "form_1040.line_11_agi")
        regular_11 = _get_num(pfic_block, "line_11_regular_agi")
        agi_ok = (line_9 is not None and regular_9 is not None
                  and line_9 >= (regular_9 + pfic_ordinary) - tolerance
                  and line_11 is not None and regular_11 is not None
                  and line_11 >= (regular_11 + pfic_ordinary) - tolerance)
        if not agi_ok:
            pfic_incomplete.append(
                f"§1291 current-year ordinary income ({pfic_ordinary:,.2f}) is not "
                "reconciled into GROSS income / AGI: require form_1040.line_9_total_"
                "income >= line_9_regular_total_income + it AND form_1040.line_11_agi "
                ">= line_11_regular_agi + it (both counterfactuals WITHOUT PFIC "
                "income). PFIC ordinary income bolted onto line 15 but missing from "
                "lines 9/11 leaves taxable Social Security, NIIT/MAGI, and "
                "AGI-limited deductions/credits computed on a pre-PFIC AGI.")

    failed = bool(missing_records or phantom_files or pending_skips
                  or missing_targets or pfic_incomplete
                  or any(not c.ok and not c.advisory for c in sum_checks))
    return TieOutReport(
        records=len(ledger), skipped=sum(1 for r in ledger if r.skipped is not None),
        missing_records=missing_records, phantom_files=phantom_files,
        pending_skips=pending_skips,
        sum_checks=tuple(sum_checks),
        missing_result_targets=tuple(missing_targets),
        pfic_incomplete=tuple(pfic_incomplete),
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
    if report.pfic_incomplete:
        lines += ["## ⛔ PFIC §1291 computation INCOMPLETE", ""]
        lines += [f"- {t}" for t in report.pfic_incomplete]
        lines.append("")
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
    # mkdir(exist_ok=True) accepts a symlink (or, on Windows, a junction —
    # is_reparse_point() catches both; see secure_fs.py's module docstring
    # for why is_symlink() alone misses a junction) pointing at an existing
    # directory without complaint (is_dir() follows it), and the chmod below
    # would then resolve through the link and mutate the external target's
    # permissions — before _validate_subpath's resolve()-based check further
    # down ever gets a chance to reject the path as outside workspace.
    # is_reparse_point() fails CLOSED — everything but FileNotFoundError
    # propagates rather than certifying an uninspected path as safe — and this
    # call sits OUTSIDE any try, so an untranslated OSError would escape
    # write_report as a bare traceback past every caller's `except TieOutError`
    # (including _main's).
    try:
        summaries_is_reparse = is_reparse_point(summaries)
    except OSError as e:
        raise TieOutError(
            f"Could not determine whether {redact(summaries)} is a symlink "
            f"or junction: {redact(e)} — refusing to write through a path "
            f"that could not be inspected.") from e
    if summaries_is_reparse:
        raise TieOutError(
            f"{redact(summaries)} is a symlink or junction, not a real "
            f"directory — refusing to write through it.")
    summaries.mkdir(mode=0o700, exist_ok=True)
    # summaries/ holds one file per source document, named after it, so the
    # listing discloses on its own. A chmod failure here fails the write
    # rather than continuing into a directory that could not be secured —
    # archive.py and scenario.py hold the same line for the runtime paths
    # they create (AGENTS.md SECURITY §1).
    try:
        secure_path(summaries)
    except OSError as e:
        raise TieOutError(
            f"Could not secure {redact(summaries)} (chmod 700): "
            f"{redact(e)}") from None
    target = _validate_subpath((summaries / "_tie_out.md").resolve(), workspace)
    # `mode` on os.open is a create-only argument — POSIX ignores it when
    # target already exists (a re-run of the tie-out gate over a report from
    # before this hardening), so opening alone does not guarantee 600. The
    # explicit secure_fd right after open, before any content is written,
    # closes that window instead of leaving the report at its old, possibly
    # looser mode for the whole write — scenario.py holds the same line.
    # secure_fd prefers fchmod on the open descriptor where the platform has
    # it; it does not exist on Windows, a supported platform, so it falls
    # back there to the same PowerShell-ACL-based enforcement secure_path
    # uses everywhere else on that platform.
    # ONE writer for every runtime artifact (V10, `secure_fs.atomic_write`).
    # This wrote IN PLACE, and it is the HARD GATE's own record — the artifact
    # V8's receipt binds a filing package to — so an interrupted write left a
    # truncated report under the name the next reader trusts.
    try:
        with atomic_write(target, encoding="utf-8") as f:
            f.write(markdown)
    except OSError as e:
        raise TieOutError(f"Could not write {redact(target)}: {redact(e)}") from e
    return target


def _read_json(path: Path, what: str) -> dict:
    if not path.exists():
        raise TieOutError(f"{what} not found at {redact(path)}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise TieOutError(f"{what} is malformed: {redact(e)}")


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
        # BEFORE anything is read: the digests this gate's verdict will be
        # bound to. Hashing after the verdict would bind a PASS to a document
        # that arrived while the report was being written — a gate certifying a
        # file it never saw, which is what the receipt exists to catch (V8).
        gate_inputs = receipt.gate_inputs(workspace, "tie_out")
        ledger = load_ledger(_read_json(ledger_path, "parse_ledger.json"))
        result = _read_json(result_path, "result.json")
        # A PFIC taxable event acknowledged in the profile REQUIRES a computed
        # §1291 result — read the flag so the gate can fail an event that reached
        # compute (gate acknowledged) but produced no fund. Also count the
        # no-election foreign funds that had a taxable distribution/disposition so
        # the gate can require ONE computed result per taxable fund (not just one).
        pfic_event_expected = False
        expected_pfic_fund_ids: set = set()
        any_taxable_pfic_recorded = False
        invalid_pfic_elections: list = []
        pfic_positive_event_ids: set = set()
        pfic_missing_amount_ids: set = set()
        pfic_disposition_positive_ids: set = set()
        pfic_holding_mismatch_ids: set = set()
        pfic_multiyear_disposition_ids: set = set()
        pfic_recompute_failures: list = []
        profile_path = (workspace / "taxpayer_profile.json").resolve()
        if profile_path.exists():
            prof = _read_json(_validate_subpath(profile_path, workspace),
                              "taxpayer_profile.json")
            flags = prof.get("flags") if isinstance(prof.get("flags"), dict) else {}
            pfic_event_expected = flags.get("has_pfic_taxable_event") is True
            (expected_pfic_fund_ids, any_taxable_pfic_recorded,
             invalid_pfic_elections) = _expected_pfic_fund_ids(prof)
            _cy = result.get("meta", {}).get("tax_year") if isinstance(result.get("meta"), dict) else None
            (pfic_positive_event_ids, pfic_missing_amount_ids,
             pfic_disposition_positive_ids) = _pfic_positive_event_ids(prof, _cy)
            (pfic_holding_mismatch_ids, pfic_multiyear_disposition_ids,
             _mydist) = _pfic_holding_year_checks(prof, _cy)
            # Load the fetched historical top rates + §6621 quarterly rates so the
            # recompute can verify the per-fund deferred tax AND interest (the full
            # add-on), not just the deferred lower bound. Absent/incomplete → a
            # multi-year fund's add-on is unverifiable and the recompute FAILS closed.
            _hist_rate, _under_rates = None, None
            if _cy is not None:
                _law_path = (workspace / f"tax_law_{_cy}.json").resolve()
                if _law_path.exists():
                    try:
                        _law = _read_json(_validate_subpath(_law_path, workspace),
                                          f"tax_law_{_cy}.json")
                        _fed = _law.get("federal") if isinstance(_law.get("federal"), dict) else {}
                        _htr = _fed.get("historical_top_rate")
                        if isinstance(_htr, dict) and _htr:
                            _hist_rate = _htr
                        _ur = _fed.get("underpayment_rates")
                        if isinstance(_ur, list) and _ur:
                            _under_rates = _ur
                    except TieOutError:
                        _hist_rate, _under_rates = None, None
            pfic_recompute_failures = _pfic_recompute_checks(
                prof, result, _cy, args.tolerance, historical_top_rate=_hist_rate,
                underpayment_rates=_under_rates)
        report = evaluate(ledger, result, list_document_files(workspace),
                          tolerance=args.tolerance,
                          pfic_event_expected=pfic_event_expected,
                          expected_pfic_fund_ids=expected_pfic_fund_ids,
                          any_taxable_pfic_recorded=any_taxable_pfic_recorded,
                          invalid_pfic_elections=invalid_pfic_elections,
                          pfic_positive_event_ids=pfic_positive_event_ids,
                          pfic_missing_amount_ids=pfic_missing_amount_ids,
                          pfic_disposition_positive_ids=pfic_disposition_positive_ids,
                          pfic_holding_mismatch_ids=pfic_holding_mismatch_ids,
                          pfic_multiyear_disposition_ids=pfic_multiyear_disposition_ids,
                          pfic_recompute_failures=pfic_recompute_failures)
        markdown = render_markdown(report)
    except (TieOutError, RolloverError, receipt.ReceiptError, OSError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    # The VERDICT is printed, and its exit code decided, BEFORE the report is
    # persisted. write_report deliberately fails CLOSED on a chmod/write error
    # (an unsecured report is worse than no report), but that failure must not
    # be allowed to suppress a verdict already computed, nor to downgrade a
    # FAILED gate to exit 1 — which /tax-compute Step 12.7 documents as
    # "parse_ledger.json is missing or malformed", sending the agent to fix a
    # ledger that is fine while the dropped-1099 failure this gate exists to
    # raise is never printed at all. A permissions fault must not be able to
    # mask a tax-correctness fault.
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

    exit_code = 2 if report.failed else 0
    try:
        write_report(workspace, markdown)
        # ...and the receipt, for the same reason and under the same rule: it
        # is written AFTER the verdict is printed, a failure here never lowers
        # the verdict's exit code, and a FAILED gate is RECORDED as failed
        # rather than left unwritten — /tax-generate can tell "this gate failed"
        # from "this gate never ran", and both refuse a filing package (V8).
        receipt.record(workspace, "tie_out",
                       receipt.PASS if not report.failed else "FAILED",
                       inputs=gate_inputs)
    except (TieOutError, RolloverError, receipt.ReceiptError, OSError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        # Never below 1 (the write really did fail, so this is not a clean
        # pass) and never below the verdict's own code — 2 outranks it, so a
        # FAILED tie-out still exits 2 even when its report could not be
        # written.
        return max(exit_code, 1)
    return exit_code


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
