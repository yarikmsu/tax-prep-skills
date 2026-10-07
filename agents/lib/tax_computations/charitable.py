"""Vintage accounting for reviewed charitable carryover worksheets.

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
