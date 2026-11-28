"""Charitable gift substantiation and vintage accounting for carryovers.

This is accounting, not a replacement for Pub. 526's carryover worksheets.
Category ordering, exceptional carry periods, standard-deduction absorption
and the covered year's floors must be resolved in the supplied worksheet.
No aggregate establishes a vintage, and no tax-law figure is inferred here.
"""

import copy
import math

import agents.lib  # noqa: F401 — enforce the workspace interpreter on direct execution


CATEGORIES = {"cash_public", "cash_other", "noncash_appreciated_public",
              "noncash_other", "conservation"}

SUBSTANTIATION_LIMITS = ("acknowledgment_minimum", "form_8283_threshold", "qualified_appraisal_threshold")
# Form 8283 Section A takes a vehicle at any claimed value when its deduction
# is limited to the gross proceeds of the donee's sale and the donor has the
# contemporaneous written acknowledgment (Form 1098-C): no appraisal applies.
VEHICLE_SOLD_BY_DONEE = "vehicle_sold_by_donee"
GIFT_KINDS = ("cash", "noncash", "publicly_traded_securities", VEHICLE_SOLD_BY_DONEE)
_NONCASH_CATEGORIES = tuple(sorted(c for c in CATEGORIES if not c.startswith("cash")))


def _claimed_gifts(section: dict, inputs) -> tuple[float, float] | None:
    """Cash and noncash gifts behind one itemized charitable deduction."""
    if not isinstance(inputs, dict):
        return None
    cash = inputs.get("charitable_cash")
    noncash = inputs.get("charitable_noncash")
    if cash is None and noncash is None and not section.get("charitable_deductible"):
        return 0.0, 0.0
    try:
        cash, noncash = _amount(cash), _amount(noncash)
        claimed = _amount(section.get("charitable_deductible", 0))
    except (ValueError, OverflowError):
        return None
    # The builder separately reconciles a recorded vintage settlement. With
    # no such settlement, the current gifts must cover the deduction.
    if claimed > round(cash + noncash, 2) and not section.get("charitable_ledger"):
        return None
    return cash, noncash


def current_itemized_gifts(result: dict) -> tuple[float, float] | None:
    """Federal cash and noncash gifts before income limits. None means unknown."""
    schedule = result.get("schedule_a") or {}
    if not isinstance(schedule, dict):
        return None
    if schedule.get("use_itemized") is not True:
        return 0.0, 0.0
    return _claimed_gifts(schedule, schedule.get("inputs") or {})


def substantiated_gifts(result: dict) -> tuple[float, float] | None:
    """Gifts behind a federal OR California itemized deduction. None means unknown.

    Schedule CA (540) Part II starts from the federal Schedule A amounts and
    adjusts only the differences it lists, none of them substantiation, so a
    California-only itemizer's gifts need the same evidence. One inventory
    serves both: the federal gifts when the federal return deducts them.
    """
    federal = current_itemized_gifts(result)
    ca = result.get("schedule_ca_itemized") or {}
    if not isinstance(ca, dict):
        return None
    if federal is None or ca.get("use_itemized") is not True:
        return federal
    inputs = ca.get("charitable_inputs")
    if inputs is None:
        inputs = (result.get("schedule_a") or {}).get("inputs") or {}
    state = _claimed_gifts(ca, inputs)
    if state is None:
        return None
    return federal if any(federal) else state


def substantiation_limit(tax_law: dict, key: str) -> float:
    """Use the source-pinned amount for the covered year."""
    federal = tax_law.get("federal") or {}
    figures = federal.get("charitable_substantiation") or {}
    limit = _amount(figures.get(key))
    if limit <= 0:
        raise ValueError("Fetch the covered-year charitable substantiation limits.")
    return limit


def _positive(value) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value > 0)


def _noncash_carryover_used(schedule: dict) -> bool:
    """A prior-year noncash vintage deducted this year (section 170(d))."""
    ledger = schedule.get("charitable_ledger")
    if not isinstance(ledger, dict) or not isinstance(ledger.get("entries"), list):
        return False
    year = ledger.get("as_of_year")
    return type(year) is int and any(
        isinstance(row, dict) and row.get("category") in _NONCASH_CATEGORIES
        and type(row.get("origin_year")) is int and row["origin_year"] < year
        and _positive(row.get("used_this_year")) for row in ledger["entries"])


def form_8283_required(result: dict, tax_law: dict) -> bool:
    """Form 8283 for this year's noncash gifts over the threshold, and for
    any carryover year described in section 170(d) (its "When To File")."""
    schedule = result.get("schedule_a")
    if (isinstance(schedule, dict) and schedule.get("use_itemized") is True
            and _noncash_carryover_used(schedule)):
        return True
    gifts = current_itemized_gifts(result)
    if gifts is None or gifts[1] <= 0:
        return False
    try:
        return gifts[1] > substantiation_limit(tax_law, "form_8283_threshold")
    except (ValueError, TypeError, AttributeError, OverflowError):
        # Drafts can print when law is missing. The filing law gate blocks
        # that case before a final package reaches this function.
        return False


def form_1098c_attached(result: dict) -> bool:
    """A deducted vehicle limited to the donee's sale proceeds: the return
    carries its Form 1098-C (or other contemporaneous acknowledgment)."""
    gifts = current_itemized_gifts(result)
    rows = (result.get("schedule_a") or {}).get("charitable_substantiation")
    return (gifts is not None and any(gifts) and isinstance(rows, list)
            and any(isinstance(row, dict) and row.get("kind") == VEHICLE_SOLD_BY_DONEE for row in rows))


def _bound(path, sources: set[str]) -> bool:
    return isinstance(path, str) and path in sources


def substantiation_issues(result: dict, law: dict, sources: set[str]) -> list[str]:
    gifts = substantiated_gifts(result)
    if gifts is None:
        return ["Record Schedule A's current cash and noncash gifts separately from carryovers."]
    try:
        acknowledgment = substantiation_limit(law, "acknowledgment_minimum")
        appraisal = substantiation_limit(law, "qualified_appraisal_threshold")
    except (ValueError, TypeError, AttributeError, OverflowError):
        return ["Fetch the covered-year charitable substantiation limits."]
    rows = (result.get("schedule_a") or {}).get("charitable_substantiation")
    if not isinstance(rows, list) or not rows:
        return ["Record the current-year gifts and bind their reviewed evidence."]

    issues = []
    totals: dict[str, list[float]] = {"cash": [], "noncash": []}
    groups: dict[str, list[tuple[int, float, object]]] = {}
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or row.get("kind") not in GIFT_KINDS:
            return [f"Classify charitable gift #{number} as cash, noncash, publicly traded securities "
                    "or a vehicle sold by the donee."]
        kind = row["kind"]
        try:
            amount = _amount(row.get("amount"))
        except (ValueError, OverflowError):
            return [f"Record the reviewed amount of charitable gift #{number}."]
        if amount <= 0:
            return [f"Record a positive amount for charitable gift #{number}."]
        totals["cash" if kind == "cash" else "noncash"].append(amount)
        refs: tuple[str, ...]
        if kind == VEHICLE_SOLD_BY_DONEE:
            refs, document = ("acknowledgment_ref",), "Form 1098-C or other contemporaneous written acknowledgment"
        elif amount >= acknowledgment:
            refs, document = ("acknowledgment_ref",), "contemporaneous written acknowledgment"
        else:
            # Below the minimum, the donee's letter is as good a record as any.
            refs, document = ("source_ref", "acknowledgment_ref"), "supporting record"
        if not any(_bound(row.get(ref), sources) for ref in refs):
            issues.append(f"Bind the {document} for charitable gift #{number}.")
        if kind == "noncash":
            group = row.get("similar_group")
            if not isinstance(group, str) or not group.strip():
                return [f"Assign a reviewed similar-property group to charitable gift #{number}."]
            # A similar-property group is a category, not a spelling.
            key = " ".join(group.split()).casefold()
            groups.setdefault(key, []).append((number, amount, row.get("appraisal_ref")))
    if (round(math.fsum(totals["cash"]), 2), round(math.fsum(totals["noncash"]), 2)) != gifts:
        issues.append("Reconcile the gift inventory to the current cash and noncash Schedule A amounts.")
    for members in groups.values():
        if round(math.fsum(amount for _, amount, _ in members), 2) > appraisal:
            for number, _, path in members:
                if not _bound(path, sources):
                    issues.append(f"Bind the qualified appraisal for charitable gift #{number}, including its similar-property group.")
    return issues


def _year(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 2000 <= value <= 2099:
        raise ValueError("Charitable ledger needs a covered tax year")
    return value


def _amount(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or round(value, 2) != value):
        raise ValueError("Charitable ledger amounts must be finite, nonnegative cents")
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Charitable ledger requires worksheet/source evidence")
    return value


def validate_charitable_ledger(ledger, year, jurisdiction):
    """Validate a positive, reviewed end-of-year declaration, including zero."""
    _year(year)
    if jurisdiction not in {"federal", "CA"}:
        raise ValueError("Charitable ledger jurisdiction is unsupported")
    if (not isinstance(ledger, dict) or ledger.get("verified") is not True
            or ledger.get("as_of_year") != year):
        raise ValueError("Resolve charitable carryovers from prior worksheets explicitly")
    _year(ledger["as_of_year"])
    _text(ledger.get("worksheet_ref"))
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Charitable ledger entries must be a list")
    ids = set()
    for row in entries:
        if not isinstance(row, dict):
            raise ValueError("Charitable ledger entry must be an object")
        ident = _text(row.get("id"))
        if ident in ids:
            raise ValueError("Duplicate charitable vintage id")
        ids.add(ident)
        origin = _year(row.get("origin_year"))
        expiry = _year(row.get("expires_after_year"))
        if origin > year or expiry < origin:
            raise ValueError("Charitable vintage/expiry years are inconsistent")
        if (not isinstance(row.get("category"), str) or row["category"] not in CATEGORIES
                or row.get("jurisdiction") != jurisdiction):
            raise ValueError("Charitable category or jurisdiction is inconsistent")
        if row.get("eligibility_verified") is not True:
            raise ValueError("Charitable vintage eligibility needs explicit resolution")
        _text(row.get("source_ref"))
        amounts = [_amount(row.get(k)) for k in
                   ("original_amount", "used_amount", "lapsed_amount", "remaining_amount")]
        if round(sum(amounts[1:]), 2) != amounts[0]:
            raise ValueError("Charitable vintage balances do not reconcile")
        if year > expiry and amounts[-1]:
            raise ValueError("Expired charitable vintage still has a balance")
    return copy.deepcopy(ledger)


def settle_charitable_ledger(prior, worksheet, *, tax_year, jurisdiction,
                             agi, charitable_cash, charitable_noncash):
    """Apply explicit, reviewed per-vintage consumption without guessing law.

    Worksheet allocations report amounts absorbed under the charitable rules,
    even in a standard-deduction year. `deductible` is the Schedule A/CA
    candidate before the overall itemized reduction; the engine still chooses
    the election and requires it to agree with the worksheet.
    """
    _year(tax_year)
    if prior is None:
        if not isinstance(worksheet, dict) or worksheet.get("no_opening_carryover_confirmed") is not True:
            raise ValueError("Confirm no opening charitable carryover or supply its verified ledger")
        prior = {"as_of_year": tax_year - 1, "verified": True,
                 "worksheet_ref": "Explicitly confirmed no opening carryover", "entries": []}
    opening = validate_charitable_ledger(prior, tax_year - 1, jurisdiction)
    if (not isinstance(worksheet, dict) or worksheet.get("verified") is not True
            or worksheet.get("tax_year") != tax_year
            or worksheet.get("jurisdiction") != jurisdiction
            or worksheet.get("agi") != agi
            or not isinstance(worksheet.get("itemizes"), bool)):
        raise ValueError("Charitable worksheet must be reviewed for this year, AGI and jurisdiction")
    _year(worksheet["tax_year"])
    if isinstance(agi, bool) or not isinstance(agi, (int, float)) or not math.isfinite(agi):
        raise ValueError("Charitable worksheet AGI must be finite")
    ref = _text(worksheet.get("worksheet_ref"))
    # Lazy because the cache imports this package's federal constants.
    from agents.lib.tax_law_fetcher import _source_host_authoritative
    if (not _source_host_authoritative(worksheet.get("law_source_url"))
            or _year(worksheet.get("law_source_year")) != tax_year):
        raise ValueError("Charitable worksheet requires authoritative covered-year law evidence")
    new = worksheet.get("new_entries")
    if not isinstance(new, list):
        raise ValueError("Charitable worksheet requires current-year vintage entries")
    validate_charitable_ledger({"as_of_year": tax_year, "verified": True,
                               "worksheet_ref": ref, "entries": new}, tax_year, jurisdiction)
    for row in new:
        if (row["origin_year"] != tax_year or row["used_amount"] or row["lapsed_amount"]):
            raise ValueError("New charitable entries must be unconsumed current-year gifts")
    cash = round(sum(r["original_amount"] for r in new if r["category"].startswith("cash")), 2)
    noncash = round(sum(r["original_amount"] for r in new
                        if not r["category"].startswith("cash")), 2)
    if cash != _amount(charitable_cash) or noncash != _amount(charitable_noncash):
        raise ValueError("Current charitable vintages do not match source gift amounts")
    entries = opening["entries"] + copy.deepcopy(new)
    if len({r["id"] for r in entries}) != len(entries):
        raise ValueError("Current charitable id duplicates an opening vintage")
    allocations = worksheet.get("allocations")
    if not isinstance(allocations, list) or len(allocations) != len(entries):
        raise ValueError("Charitable worksheet must resolve every vintage")
    by_id = {}
    for allocation in allocations:
        if not isinstance(allocation, dict):
            raise ValueError("Charitable allocation must be an object")
        ident = _text(allocation.get("id"))
        if ident in by_id:
            raise ValueError("Duplicate charitable allocation")
        by_id[ident] = allocation
    if set(by_id) != {r["id"] for r in entries}:
        raise ValueError("Charitable allocations do not match vintage ids")
    used_total = 0
    for row in entries:
        allocation = by_id[row["id"]]
        used = _amount(allocation.get("used_this_year"))
        lapsed = _amount(allocation.get("lapsed_this_year"))
        if round(used + lapsed, 2) > row["remaining_amount"]:
            raise ValueError("Charitable allocation exceeds the vintage balance")
        if tax_year > row["expires_after_year"] and used:
            raise ValueError("Cannot use an expired charitable vintage")
        row["used_amount"] = round(row["used_amount"] + used, 2)
        row["lapsed_amount"] = round(row["lapsed_amount"] + lapsed, 2)
        row["remaining_amount"] = round(row["remaining_amount"] - used - lapsed, 2)
        row["used_this_year"] = used
        row["lapsed_this_year"] = lapsed
        used_total += used
    candidate = _amount(worksheet.get("deductible"))
    if candidate != round(used_total, 2):
        raise ValueError("Charitable worksheet deduction does not reconcile to vintage usage")
    ledger = {"as_of_year": tax_year, "verified": True, "worksheet_ref": ref, "entries": entries}
    validate_charitable_ledger(ledger, tax_year, jurisdiction)
    return {"charitable_deductible": candidate,
            "charitable_carryforward": round(sum(r["remaining_amount"] for r in entries), 2),
            "charitable_ledger": ledger}
