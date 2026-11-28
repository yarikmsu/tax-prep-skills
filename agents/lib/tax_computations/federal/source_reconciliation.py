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
K_REPORTED_GROSS = "form_1099k_box_1a"
_K_ROUTES = (BUSINESS_GROSS, "personal_1099k", "capital_proceeds")
_BUSINESS_FORMS = {"1099NEC", "1099K"}
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,96}")
_EMPLOYER_EIN = re.compile(r"[0-9]{2}-[0-9]{7}")


def form_code(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", name).upper().removeprefix("FORM")


def _clean_code(name: str) -> str:
    # "Corrected 1099-K" and "1099-K Corrected" are one form. Removing the word
    # only as a suffix left "CORRECTED1099K", which matched no business rule,
    # so one payment on a 1099-NEC and a corrected 1099-K counted twice.
    return form_code(name).replace("CORRECTED", "").removeprefix("FORM")


def _base_form(name: str) -> str:
    code = _clean_code(name)
    # Containment, as the 1099-DA rule reads it: "Substitute 1099-K" or
    # "PayPal 1099-K" matched no business rule as a prefix test.
    for business_form in ("1099NEC", "1099K"):
        if business_form in code and not _consolidated(code):
            return business_form
    return "W2" if code == "W2C" else code


def _marked_corrected(name: str) -> bool:
    """The form says it is a correction: W-2c, or a 1099 marked CORRECTED."""
    return "CORRECTED" in form_code(name) or _clean_code(name) == "W2C"


def _crypto_csv_copy(record: LedgerRecord, kept: LedgerRecord) -> bool:
    """A second download of an imported exchange CSV, byte for byte.

    Every crypto CSV record is a skip (its rows reach the return through the
    importer), so a copy's link could never name an active form, and the
    tie-out's digest check counted the copy as a second import.
    """
    return (form_code(record.form_type) == form_code(kept.form_type) == "CRYPTOCSV"
            and bool(record.source_sha256) and record.source_sha256 == kept.source_sha256
            and not (kept.source_form or {}).get("duplicate_of"))


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


def validate_form(value: object, skipped: bool, form_type: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {
        "included_boxes",
        "tax_year", "series_id", "account_last4", "corrected", "duplicate_of", "superseded_by", "included_in",
        "employer_ein",
    }:
        raise ValueError("source_form must use the filing year, series, employer, correction and disposition fields")
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
    if "employer_ein" in value and (not isinstance(value["employer_ein"], str)
                                    or not _EMPLOYER_EIN.fullmatch(value["employer_ein"])):
        raise ValueError("source_form.employer_ein must use the W-2 Box b format NN-NNNNNNN")
    links = {"duplicate_of", "superseded_by", "included_in"} & set(value)
    if len(links) > 1 or (links and not skipped):
        raise ValueError("a duplicate, superseded or included form must be a skip with one retained record")
    if "included_boxes" in value:
        boxes = value["included_boxes"]
        if ("included_in" not in links or _base_form(form_type) not in _BUSINESS_FORMS
                or not isinstance(boxes, dict) or not boxes or boxes.keys() - {BUSINESS_GROSS, K_REPORTED_GROSS}):
            raise ValueError("source_form.included_boxes must record business amounts on an included 1099-K or 1099-NEC")
        try:
            for amount in boxes.values():
                _cents(amount)
        except ValueError as exc:
            raise ValueError(f"source_form.included_boxes: {exc}") from None
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
    from agents.lib.material_evidence import MaterialEvidenceError, _source_path

    if skipped or person not in {"primary", "spouse"} or not isinstance(rows, list) or not rows:
        raise ValueError("business receipts need a non-skipped book record and one taxpayer owner")
    if set(boxes) != {BOOK_GROSS}:
        raise ValueError("a business book record carries only its book gross category")
    required = {"payment_id", "amount", "reported_by"}
    for row in rows:
        if not isinstance(row, dict) or not required <= row.keys() <= required | {"evidence_file"}:
            raise ValueError("each book receipt needs payment_id, amount and reported_by")
        reporters = row["reported_by"]
        if (not _opaque_id(row["payment_id"]) or not isinstance(reporters, list)
                or not all(_opaque_id(doc_id) for doc_id in reporters)
                or len(set(reporters)) != len(reporters)):
            raise ValueError("book receipts require an opaque payment id and distinct source ids")
        if "evidence_file" in row:
            try:
                _source_path(row["evidence_file"])
            except MaterialEvidenceError:
                raise ValueError("business receipt evidence_file must be a safe workspace input") from None
    if sum(_cents(row["amount"]) for row in rows) != _cents(boxes[BOOK_GROSS]):
        raise ValueError("business-book receipt amounts must reconcile to the book gross")
    return rows


class SourceReview(NamedTuple):
    problems: tuple[str, ...]
    disclosures: tuple[str, ...]
    business_gross: float | None


def _payer_identity(name: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[^\W_]+", name.casefold()))


def _review_1099k(record: LedgerRecord, number: int,
                  personal_sources: dict[str, str] | None, *, own_form: bool = True) -> list[str]:
    boxes = dict(record.key_boxes)
    # Inside another statement, capital_proceeds is also its Form 1099-B
    # total, so it cannot show how much of Box 1a went to Schedule D.
    route_names = _K_ROUTES if own_form else (BUSINESS_GROSS, "personal_1099k")
    routes = {route: boxes[route] for route in route_names if route in boxes}
    reported = boxes.get(K_REPORTED_GROSS)
    if reported is None:
        return [f"Document #{number} needs verified Form 1099-K Box 1a in {K_REPORTED_GROSS}."]
    # Every dollar of Box 1a has a line (IRS, "What to do with Form 1099-K").
    # Routing only part of it dropped the rest from Schedule C unseen, and
    # leaving it unrouted needed less review than calling it personal.
    routed = round(sum(routes.values()), 2)
    if routed != round(reported, 2):
        if not own_form:
            return [f"Document #{number} routes ${routed:,.2f} of its ${reported:,.2f} Form 1099-K Box 1a "
                    "to business or personal receipts. Route all of it, or record a Form 1099-K with "
                    "capital receipts as its own document."]
        return [f"Document #{number} routes ${routed:,.2f} of its ${reported:,.2f} Form 1099-K Box 1a. "
                "Route all of it to business, personal or capital receipts."]
    if (personal_sources is not None and (routes.get("personal_1099k") or routes.get("capital_proceeds"))
            and (not record.source_sha256 or personal_sources.get(record.file) != record.source_sha256)):
        return [f"Document #{number} needs a reviewed input.1099k_personal_routing outcome "
                "bound to the form and parse ledger before treating its receipts as personal."]
    return []


def _included_business_amount(record: LedgerRecord, form: str) -> int:
    boxes = (record.source_form or {}).get("included_boxes", {})
    business = boxes.get(BUSINESS_GROSS)
    if form == "1099NEC":
        if boxes.keys() != {BUSINESS_GROSS}:
            raise ValueError(f"record Form 1099-NEC Box 1 (Box 1a from TY2026) in included_boxes.{BUSINESS_GROSS}")
    elif boxes.keys() != {BUSINESS_GROSS, K_REPORTED_GROSS} or business != boxes[K_REPORTED_GROSS]:
        raise ValueError("record Form 1099-K Box 1a in included_boxes and route the full amount to "
                         "business receipts for this link. Keep a mixed or nonbusiness Form 1099-K active "
                         "and route its amounts separately")
    return _cents(business)


def _included_in_active(records: dict, record: LedgerRecord) -> bool:
    """A form reported inside an active consolidated statement.

    Its copy or its superseded original names it, never the statement: the
    statement's sum check would otherwise count one form's receipts twice.
    """
    target = records.get((record.source_form or {}).get("included_in"))
    return target is not None and target[1].skipped is None


def _own_1099k_section(record: LedgerRecord) -> bool:
    """A statement other than a Form 1099-K that reports Box 1a itself."""
    return _base_form(record.form_type) != "1099K" and dict(record.key_boxes).get(K_REPORTED_GROSS, 0) > 0


def _review_forms(ledger: list[LedgerRecord], year: object,
                  personal_sources: dict[str, str] | None
                  ) -> tuple[list[str], list[str], set[tuple[str, str]]]:
    problems, disclosures = [], []
    records = {r.doc_id: (number, r) for number, r in enumerate(ledger, 1)}
    digests: dict[str, list[tuple[int, LedgerRecord]]] = defaultdict(list)
    active: list[tuple[int, LedgerRecord, str, tuple[str, ...]]] = []
    unlinked: list[tuple[int, LedgerRecord, str, tuple[str, ...]]] = []
    included_business: dict[str, list[tuple[int, str, int, str | None]]] = defaultdict(list)
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
            # The box decides, not the label: a consolidated statement's
            # Form 1099-K section otherwise passed with Box 1a half routed.
            if form == "1099K" or K_REPORTED_GROSS in dict(record.key_boxes):
                problems.extend(_review_1099k(record, number, personal_sources, own_form=form == "1099K"))
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
        if copy_of and retained is not None and _crypto_csv_copy(record, retained[1]):
            continue
        if retained is None or (retained[1].skipped is not None and not (
                (copy_of or superseded_by) and _included_in_active(records, retained[1]))):
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
            if form in _BUSINESS_FORMS:
                try:
                    cents = _included_business_amount(record, form)
                except ValueError as exc:
                    problems.append(f"Document #{number} in consolidated document #{kept_number}: {exc}.")
                else:
                    included_business[kept.doc_id].append((number, form, cents, record.source_sha256))
            else:
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
    included_classes: set[tuple[str, str]] = set()
    for kept_id, included in included_business.items():
        kept_number, kept = records[kept_id]
        numbers = ", ".join(f"#{number}" for number, _, _, _ in included)
        amount = dict(kept.key_boxes).get(BUSINESS_GROSS)
        # One download saved twice is one form: its receipts count once, and
        # copies that disagree cannot both be its reading.
        receipts: dict[str | None, int] = {}
        if any(receipts.setdefault(digest, cents) != cents for _, _, cents, digest in included):
            problems.append(f"Consolidated document #{kept_number}: copies of one included form "
                            f"in documents {numbers} record different amounts.")
            continue
        try:
            covered = amount is not None and _cents(amount) >= sum(receipts.values())
        except ValueError as exc:
            problems.append(f"Consolidated document #{kept_number} {BUSINESS_GROSS}: {exc}.")
            continue
        if not covered:
            problems.append(f"Consolidated document #{kept_number} needs {BUSINESS_GROSS} covering "
                            f"all included business receipts from documents {numbers}. A copy or the "
                            "original of an included form names that form in duplicate_of or superseded_by.")
            continue
        classes = {form for _, form, cents, _ in included if cents}
        # The statement's own Form 1099-K section shares its gross with the
        # included forms, so Box 1a matched with a 1099-NEC's receipts in it.
        if _own_1099k_section(kept):
            classes.add("1099K")
        if _BUSINESS_FORMS <= classes:
            problems.append(f"Consolidated document #{kept_number} includes both Forms 1099-NEC and 1099-K "
                            f"in documents {numbers}. Keep the business forms active separately to trace overlap.")
            continue
        included_classes.update((kept.person, form) for form in classes)
        for number, _, _, _ in included:
            disclosures.append(f"Document #{number} is reported within consolidated document #{kept_number}.")
    return problems, disclosures, included_classes


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


def _book_total(ledger: list[LedgerRecord], year: object,
                overlap_sources: dict[str, str],
                included_classes: set[tuple[str, str]]) -> tuple[float | None, int, int]:
    reported = {r.doc_id: r for r in ledger if r.skipped is None and BUSINESS_GROSS in dict(r.key_boxes)}
    # Only forms sent to one recipient can report one payment twice. Pooling
    # the household asked a couple with one form each for a book that the
    # one-owner rule below then refused.
    classes: dict[str, set[str]] = defaultdict(set)
    for person, form in included_classes:
        classes[person].add(form)
    for r in reported.values():
        if dict(r.key_boxes)[BUSINESS_GROSS] > 0:
            classes[r.person].add("1099K" if _own_1099k_section(r) else _base_form(r.form_type))
    # A form recorded for both spouses may report a payment either one's form
    # reports too.
    joint = classes.pop("joint", set())
    books = [r for r in ledger if r.business_receipts]
    if not books:
        if any(_BUSINESS_FORMS <= forms | joint for forms in [*classes.values(), set()]):
            raise ValueError("Reconcile business Forms 1099-NEC and 1099-K to a receipt-level book before final export.")
        return None, 0, 0
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
    # The evidence is the processor's report or the client's remittance, never
    # a document the ledger already holds: a superseded copy of the 1099-K, or
    # the parser's own summary of it, vouched for the overlap it was to prove.
    reported_files = {r.file.casefold() for r in ledger}
    reported_files.update({"parse_ledger.json", "result.json", "taxpayer_profile.json", f"tax_law_{year}.json"})
    reported_digests = {None, *(r.source_sha256 for r in ledger)}
    gross, overlap_count, overlap_cents = 0, 0, 0
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
            if len(reporters) > 1:
                evidence = row.get("evidence_file")
                if (not isinstance(evidence, str) or evidence.casefold() in reported_files
                        or evidence.casefold().startswith("summaries/")
                        or overlap_sources.get(evidence) in reported_digests):
                    raise ValueError("An overlapping business receipt needs a separate evidence_file "
                                     "bound to input.business_receipt_overlap and the current parse ledger.")
                if cents > 0:
                    overlap_count += 1
                    overlap_cents += cents
    for doc_id, record in reported.items():
        if totals[doc_id] != _cents(dict(record.key_boxes)[BUSINESS_GROSS]):
            raise ValueError("the books do not reconcile the full reported amount of each business form")
    return gross / 100, overlap_count, overlap_cents


def review(ledger: list[LedgerRecord], tax_year: object, *,
           personal_1099k_sources: dict[str, str] | None = None,
           overlap_sources: dict[str, str] | None = None) -> SourceReview:
    problems, disclosures, included_classes = _review_forms(ledger, tax_year, personal_1099k_sources)
    try:
        gross, count, cents = _book_total(ledger, tax_year, overlap_sources or {}, included_classes)
    except ValueError as exc:
        problems.append(str(exc))
        return SourceReview(tuple(problems), tuple(disclosures), None)
    if count:
        unit = "receipt" if count == 1 else "receipts"
        disclosures.append("Payments reported on overlapping business forms were counted once "
                           f"from the reviewed books ({count} {unit}, ${cents / 100:,.2f}).")
    return SourceReview(tuple(problems), tuple(disclosures), gross)
