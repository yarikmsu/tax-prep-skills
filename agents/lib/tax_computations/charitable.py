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


def current_itemized_gifts(result: dict) -> tuple[float, float] | None:
    """Cash and noncash gifts before income limits. None means unknown."""
    schedule = result.get("schedule_a") or {}
    if not isinstance(schedule, dict):
        return None
    if schedule.get("use_itemized") is not True:
        return 0.0, 0.0
    inputs = schedule.get("inputs") or {}
    if not isinstance(inputs, dict):
        return None
    cash = inputs.get("charitable_cash")
    noncash = inputs.get("charitable_noncash")
    if cash is None and noncash is None and not schedule.get("charitable_deductible"):
        return 0.0, 0.0
    try:
        cash, noncash = _amount(cash), _amount(noncash)
        claimed = _amount(schedule.get("charitable_deductible", 0))
    except (ValueError, OverflowError):
        return None
    # The builder separately reconciles a recorded vintage settlement. With
    # no such settlement, the current gifts must cover the deduction.
    if claimed > round(cash + noncash, 2) and not schedule.get("charitable_ledger"):
        return None
    return cash, noncash


def substantiation_limit(tax_law: dict, key: str) -> float:
    """Use the source-pinned amount for the covered year."""
    federal = tax_law.get("federal") or {}
    figures = federal.get("charitable_substantiation") or {}
    limit = _amount(figures.get(key))
    if limit <= 0:
        raise ValueError("Fetch the covered-year charitable substantiation limits.")
    return limit


def form_8283_required(result: dict, tax_law: dict) -> bool:
    gifts = current_itemized_gifts(result)
    if gifts is None or gifts[1] <= 0:
        return False
    try:
        return gifts[1] > substantiation_limit(tax_law, "form_8283_threshold")
    except (ValueError, TypeError, AttributeError, OverflowError):
        # Drafts can print when law is missing. The filing law gate blocks
        # that case before a final package reaches this function.
        return False


def substantiation_issues(result: dict, law: dict, sources: set[str]) -> list[str]:
    gifts = current_itemized_gifts(result)
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
        if not isinstance(row, dict) or row.get("kind") not in ("cash", "noncash", "publicly_traded_securities"):
            return [f"Classify charitable gift #{number} as cash, noncash or publicly traded securities."]
        kind = row["kind"]
        try:
            amount = _amount(row.get("amount"))
        except (ValueError, OverflowError):
            return [f"Record the reviewed amount of charitable gift #{number}."]
        if amount <= 0:
            return [f"Record a positive amount for charitable gift #{number}."]
        totals["cash" if kind == "cash" else "noncash"].append(amount)
        evidence = "acknowledgment_ref" if amount >= acknowledgment else "source_ref"
        path = row.get(evidence)
        if not isinstance(path, str) or path not in sources:
            document = "contemporaneous written acknowledgment" if evidence == "acknowledgment_ref" else "supporting record"
            issues.append(f"Bind the {document} for charitable gift #{number}.")
        if kind == "noncash":
            group = row.get("similar_group")
            if not isinstance(group, str) or not group.strip():
                return [f"Assign a reviewed similar-property group to charitable gift #{number}."]
            groups.setdefault(group, []).append((number, amount, row.get("appraisal_ref")))
    if (round(math.fsum(totals["cash"]), 2), round(math.fsum(totals["noncash"]), 2)) != gifts:
        issues.append("Reconcile the gift inventory to the current cash and noncash Schedule A amounts.")
    for members in groups.values():
        if round(math.fsum(amount for _, amount, _ in members), 2) > appraisal:
            for number, _, path in members:
                if not isinstance(path, str) or path not in sources:
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
