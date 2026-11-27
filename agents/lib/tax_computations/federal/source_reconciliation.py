"""Reconcile information-return copies, corrections, and business receipts."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple

from agents.lib.secure_fs import redact

if TYPE_CHECKING:
    from agents.lib.tie_out import LedgerRecord

BOOK_GROSS = "self_employment_book_gross"
BUSINESS_GROSS = "self_employment_gross"
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,96}")


def form_code(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", name).upper().removeprefix("FORM")


def _clean_code(name: str) -> str:
    # "Corrected 1099-K" and "1099-K Corrected" are one form. Removing the word
    # only as a suffix left "CORRECTED1099K", which matched no business rule,
    # so one payment on a 1099-NEC and a corrected 1099-K counted twice.
    return form_code(name).replace("CORRECTED", "").removeprefix("FORM")


def _base_form(name: str) -> str:
    code = _clean_code(name)
    for business_form in ("1099NEC", "1099K"):
        if code.startswith(business_form):
            return business_form
    return "W2" if code == "W2C" else code


def _marked_corrected(name: str) -> bool:
    """The form says it is a correction: W-2c, or a 1099 marked CORRECTED."""
    return "CORRECTED" in form_code(name) or _clean_code(name) == "W2C"


def _information_return(form: str) -> bool:
    return form == "W2" or "1099" in form


def _consolidated(form: str) -> bool:
    return form == "1099" or "CONSOLIDATED" in form or "COMPOSITE" in form


def _related_forms(first: str, second: str) -> bool:
    """One form, or a consolidated 1099 beside a form it can contain."""
    return first == second or (("1099" in first and "1099" in second)
                               and (_consolidated(first) or _consolidated(second)))


def _related_payers(first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    # "Example Fund" and "Example Fund Inc" are one issuer. Exact names kept
    # two downloads of one form apart, so both were counted.
    return bool(first and second) and (set(first) <= set(second) or set(second) <= set(first))


def _opaque_id(value: object) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value)) and redact(value) == value


def validate_form(value: object, skipped: bool) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {
        "tax_year", "series_id", "account_last4", "corrected", "duplicate_of", "superseded_by", "included_in",
    }:
        raise ValueError("source_form must use the filing year, series, correction and disposition fields")
    if "tax_year" in value and (type(value["tax_year"]) is not int or not 2000 <= value["tax_year"] <= 2099):
        raise ValueError("source_form.tax_year must be a four-digit filing year")
    for field in ("series_id", "duplicate_of", "superseded_by", "included_in"):
        if field in value and not _opaque_id(value[field]):
            raise ValueError(f"source_form.{field} must be an opaque local identifier")
    if "corrected" in value and type(value["corrected"]) is not bool:
        raise ValueError("source_form.corrected must be true or false")
    if "account_last4" in value and (not isinstance(value["account_last4"], str)
                                    or not re.fullmatch(r"[0-9]{4}", value["account_last4"])):
        raise ValueError("source_form.account_last4 must record only four account digits")
    links = {"duplicate_of", "superseded_by", "included_in"} & set(value)
    if len(links) > 1 or (links and not skipped):
        raise ValueError("a duplicate, superseded or included form must be a skip with one retained record")
    return value


def _cents(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("business receipts must be nonnegative finite amounts")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < 0:
        raise ValueError("business receipts must be nonnegative finite amounts")
    cents = Decimal(str(value)) * 100
    if cents != cents.to_integral_value():
        raise ValueError("business receipts must be recorded to the cent")
    return int(cents)


def validate_book(rows: object, boxes: dict, person: str, skipped: bool) -> list[dict]:
    if rows is None and BOOK_GROSS not in boxes:
        return []
    if skipped or person not in {"primary", "spouse"} or not isinstance(rows, list) or not rows:
        raise ValueError("business receipts need a non-skipped book record and one taxpayer owner")
    if set(boxes) != {BOOK_GROSS}:
        raise ValueError("a business book record carries only its book gross category")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"payment_id", "amount", "reported_by"}:
            raise ValueError("each book receipt needs payment_id, amount and reported_by")
        reporters = row["reported_by"]
        if (not _opaque_id(row["payment_id"]) or not isinstance(reporters, list)
                or not all(_opaque_id(doc_id) for doc_id in reporters)
                or len(set(reporters)) != len(reporters)):
            raise ValueError("book receipts require an opaque payment id and distinct source ids")
    if sum(_cents(row["amount"]) for row in rows) != _cents(boxes[BOOK_GROSS]):
        raise ValueError("business-book receipt amounts must reconcile to the book gross")
    return rows


class SourceReview(NamedTuple):
    problems: tuple[str, ...]
    disclosures: tuple[str, ...]
    business_gross: float | None


def _payer_identity(name: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[^\W_]+", name.casefold()))


def _review_forms(ledger: list[LedgerRecord], year: object) -> tuple[list[str], list[str]]:
    problems, disclosures = [], []
    records = {r.doc_id: (number, r) for number, r in enumerate(ledger, 1)}
    digests: dict[str, list[tuple[int, LedgerRecord]]] = defaultdict(list)
    active: list[tuple[int, LedgerRecord, str, tuple[str, ...]]] = []
    unlinked: list[tuple[int, LedgerRecord, str, tuple[str, ...]]] = []
    for number, record in records.values():
        details = record.source_form or {}
        source_year = details.get("tax_year")
        serial = details.get("series_id")
        form = _base_form(record.form_type)
        payer = _payer_identity(record.payer)
        if source_year is not None and year is not None and source_year != year:
            problems.append(f"Document #{number} names a different filing year.")
        if record.source_sha256:
            digests[record.source_sha256].append((number, record))
        if record.skipped is None:
            if form == "1099K" and not {BUSINESS_GROSS, "personal_1099k", "capital_proceeds"} & dict(record.key_boxes).keys():
                problems.append(f"Document #{number} needs the routed gross from Form 1099-K.")
            if _information_return(form):
                active.append((number, record, form, payer))
            continue
        copy_of, superseded_by = details.get("duplicate_of"), details.get("superseded_by")
        included_in = details.get("included_in")
        link = copy_of or superseded_by or included_in
        if not link:
            if form == "1099K":
                problems.append(f"Document #{number} is a skipped Form 1099-K. Record its disposition or route the form.")
            elif _information_return(form):
                unlinked.append((number, record, form, payer))
            continue
        retained = records.get(link)
        if retained is None or retained[1].skipped is not None:
            problems.append(f"Document #{number} must name an active retained form.")
            continue
        kept_number, kept = retained
        kept_details = kept.source_form or {}
        kept_form = _base_form(kept.form_type)
        same_owner = record.person == kept.person and _related_payers(payer, _payer_identity(kept.payer))
        same_source = (record.person == kept.person and form == kept_form
                       and payer == _payer_identity(kept.payer))
        same_bytes = bool(record.source_sha256 and record.source_sha256 == kept.source_sha256)
        if copy_of and same_source and same_bytes:
            disclosures.append(f"Document #{number} was an exact copy of retained document #{kept_number}.")
        elif (superseded_by and same_source and not same_bytes and source_year is not None and source_year == year
              and source_year == kept_details.get("tax_year") and serial and serial == kept_details.get("series_id")
              and kept_details.get("corrected") is True and _marked_corrected(kept.form_type)
              and record.source_sha256 and kept.source_sha256):
            disclosures.append(f"Document #{kept_number} replaced superseded document #{number} for the same filer, year and statement.")
        elif (included_in and same_owner and "1099" in form and not _consolidated(form)
              and _consolidated(kept_form) and record.source_sha256 and kept.source_sha256):
            disclosures.append(f"Document #{number} is reported within consolidated document #{kept_number}.")
        else:
            problems.append(f"Document #{number} has no matching evidence for its duplicate, superseded or included disposition.")
    for group in digests.values():
        counted = [(number, record) for number, record in group if record.skipped is None]
        if len(counted) > 1:
            numbers = ', '.join(str(number) for number, _ in counted)
            problems.append(f"Exact duplicate sources are both counted (documents {numbers}).")
        elif len(counted) == 1 and len(group) > 1:
            _, retained_record = counted[0]
            for number, record in group:
                if record.skipped and (record.source_form or {}).get("duplicate_of") != retained_record.doc_id:
                    problems.append(f"Document #{number} needs an exact-copy disposition linked to the retained form.")
    problems += _shared_statements(active)
    for number, record, form, payer in unlinked:
        # A skip reason alone dropped a real second W-2 or a standalone 1099-B
        # beside a consolidated form: name the record it duplicates, replaces
        # or sits inside, so the disposition is checked.
        if any(_related(record, form, payer, other, other_form, other_payer)
               for _, other, other_form, other_payer in active):
            problems.append(f"Document #{number} is skipped beside an active form from the same payer and "
                            "recipient. Record duplicate_of, superseded_by or included_in.")
    return problems, disclosures


def _related(record, form, payer, other, other_form, other_payer) -> bool:
    return (record.person == other.person and _related_forms(form, other_form)
            and _related_payers(payer, other_payer))


def _shared_statements(active: list) -> list[str]:
    """Active forms that may be one statement need distinct reviewed identities.

    Different bytes do not make different statements: a W-2 downloaded twice,
    a PDF and its CSV export, a form and its correction. Equal amounts do not
    make one statement either. Only a reviewed `series_id` on each tells them
    apart, so every related group of two or more needs one per record."""
    groups = list(range(len(active)))

    def root(index: int) -> int:
        while groups[index] != index:
            index = groups[index]
        return index

    for i, (_, first, first_form, first_payer) in enumerate(active):
        for j in range(i + 1, len(active)):
            _, second, second_form, second_payer = active[j]
            if _related(first, first_form, first_payer, second, second_form, second_payer):
                groups[root(j)] = root(i)
    members: dict[int, list[tuple[int, LedgerRecord]]] = defaultdict(list)
    for index, (number, record, _, _) in enumerate(active):
        members[root(index)].append((number, record))
    problems = []
    for group in members.values():
        if len(group) < 2:
            continue
        serials = [(record.source_form or {}).get("series_id") for _, record in group]
        if None in serials or len(set(serials)) != len(serials):
            numbers = ", ".join(str(number) for number, _ in sorted(group, key=lambda item: item[0]))
            problems.append(f"Documents {numbers} share a payer, recipient and form. Record a distinct reviewed "
                            "series_id on each, or a disposition for the copy.")
    return problems


def _book_total(ledger: list[LedgerRecord], year: object) -> tuple[float | None, bool]:
    reported = {r.doc_id: r for r in ledger if r.skipped is None and BUSINESS_GROSS in dict(r.key_boxes)}
    classes = {_base_form(r.form_type) for r in reported.values() if dict(r.key_boxes)[BUSINESS_GROSS] > 0}
    books = [r for r in ledger if r.business_receipts]
    if not books:
        if {"1099NEC", "1099K"} <= classes:
            raise ValueError("Reconcile business Forms 1099-NEC and 1099-K to a receipt-level book before final export.")
        return None, False
    if type(year) is not int or any((r.source_form or {}).get("tax_year") != year for r in books):
        raise ValueError("business books must identify the covered tax year")
    if any(not book.source_sha256 for book in books):
        raise ValueError("re-parse the business books to bind their source evidence")
    # This result representation holds one Schedule C. Separate businesses
    # need their own receipts and return paths.
    owners = {r.person for r in books}
    if len(owners) != 1 or any(r.person not in owners for r in reported.values()):
        raise ValueError("business books and reported receipts must belong to one Schedule C owner")
    seen: set[str] = set()
    totals: dict[str, int] = defaultdict(int)
    gross, overlap = 0, False
    for book in books:
        for row in book.business_receipts or []:
            payment_id, reporters = row["payment_id"], row["reported_by"]
            if payment_id in seen:
                raise ValueError("a business payment occurs more than once in the books")
            seen.add(payment_id)
            cents = _cents(row["amount"])
            gross += cents
            for doc_id in reporters:
                if doc_id not in reported or not reported[doc_id].source_sha256:
                    raise ValueError("a book receipt names an unbound or inactive business source")
                totals[doc_id] += cents
            overlap |= cents > 0 and len(reporters) > 1
    for doc_id, record in reported.items():
        if totals[doc_id] != _cents(dict(record.key_boxes)[BUSINESS_GROSS]):
            raise ValueError("the books do not reconcile the full reported amount of each business form")
    return gross / 100, overlap


def review(ledger: list[LedgerRecord], tax_year: object) -> SourceReview:
    problems, disclosures = _review_forms(ledger, tax_year)
    try:
        gross, overlap = _book_total(ledger, tax_year)
    except ValueError as exc:
        problems.append(str(exc))
        gross, overlap = None, False
    if overlap:
        disclosures.append("Payments reported on overlapping business forms were counted once from the reviewed books.")
    return SourceReview(tuple(problems), tuple(disclosures), gross)
