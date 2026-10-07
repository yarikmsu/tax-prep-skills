"""Individual IRA basis worksheets, source allocations and legacy migration.

No household pro-rata pool exists. Raw reviewed inputs are retained so the
completion gate can reproduce both worksheets, and the intake gate can bind
all IRA distributions to their own recipient's source records.
"""
from __future__ import annotations

import copy
import math

import agents.lib  # noqa: F401 — enforce the workspace interpreter on direct execution

SCHEMA = "individual-8606/v1"
OWNERS = {"primary", "spouse"}
IRA_KINDS = {"traditional_ira", "sep_ira", "simple_ira"}
RETIREMENT_KINDS = IRA_KINDS | {"pension", "roth_ira", "inherited_ira"}
STATUSES = {"Single", "MFJ", "MFS", "HOH", "QSS"}
EXCLUSIONS = {"qcd", "rollover", "hsa_funding", "returned_contribution", "recharacterization"}


def amount(value, where):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{where} must be finite, numeric and non-negative")
    return float(value)


def opening_basis(profile):
    """Resolve individual opening balances without assigning a joint scalar."""
    if not isinstance(profile, dict):
        raise ValueError("Individual IRA profile must be an object")
    status = profile.get("filing_status")
    if status not in STATUSES:
        raise ValueError("IRA basis needs a canonical filing status")
    prior = profile.get("prior_year") or {}
    if not isinstance(prior, dict):
        raise ValueError("prior_year must be an object")
    if prior.get("ira_basis_unresolved"):
        raise ValueError("IRA basis ownership is unresolved; confirm the prior individual worksheets")
    raw = prior.get("ira_basis_by_taxpayer", {})
    if not isinstance(raw, dict) or set(raw) - OWNERS:
        raise ValueError("ira_basis_by_taxpayer must contain only primary/spouse")
    balances = {owner: amount(raw.get(owner, 0), "individual opening IRA basis") for owner in OWNERS}
    legacy = amount(prior.get("ira_basis", 0), "legacy IRA basis")
    if legacy:
        owner = prior.get("ira_basis_owner")
        if owner is None and status != "MFJ":
            owner = "primary"  # one individual files this return, including MFS
        if owner not in OWNERS or (status != "MFJ" and owner != "primary"):
            raise ValueError("Legacy joint IRA basis requires confirmed ira_basis_owner primary/spouse")
        if owner in raw and balances[owner] != legacy:
            raise ValueError("Legacy IRA basis conflicts with the individual opening balance")
        balances[owner] = legacy
    if status != "MFJ" and balances["spouse"]:
        raise ValueError("Spouse IRA basis belongs on the spouse's separate return")
    return balances


def _text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a non-empty evidence identifier")
    return value


def compute_individual_8606(inputs, profile):
    """Compute independent regular Part I/II worksheets and derive 1040 routing.

    `inputs.by_taxpayer` contains ONLY owners on this return. Each owner affirms
    a complete Dec 31 traditional/SEP/SIMPLE account list and the regular
    worksheet's scope, supplies individual contributions, outstanding IRA-to-IRA
    rollovers, and allocates each 1099-R to line 7, conversion, or a reviewed
    exclusion. Special worksheets, inherited IRAs and Roth distributions remain
    outside this wrapper's scope. Exclusion tax treatment is explicitly reviewed,
    never inferred from a 1099-R's often-unknown taxable box.
    """
    from agents.lib.tax_computations import compute_form_8606

    if not isinstance(profile, dict):
        raise ValueError("Individual IRA profile must be an object")
    if not isinstance(inputs, dict) or set(inputs) != {"tax_year", "by_taxpayer"}:
        raise ValueError("Individual IRA inputs need tax_year and by_taxpayer")
    year = inputs["tax_year"]
    if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 9999:
        raise ValueError("Individual IRA tax_year must be an integer year")
    if isinstance(profile.get("tax_year"), bool) or profile.get("tax_year") != year:
        raise ValueError("IRA worksheet year differs from the profile")
    basis = opening_basis(profile)
    status = profile["filing_status"]
    people = inputs["by_taxpayer"]
    allowed = OWNERS if status == "MFJ" else {"primary"}
    if not isinstance(people, dict) or not people or set(people) - allowed:
        raise ValueError("Individual IRA worksheets need eligible primary/spouse owners")
    if any(basis[owner] and owner not in people for owner in allowed):
        raise ValueError("An individual opening IRA basis has no worksheet")
    by_taxpayer = {}
    seen_accounts, seen_docs = set(), set()
    total_gross = total_taxable = 0.0
    for owner, raw in people.items():
        required = {"regular_worksheet_confirmed", "accounts_complete", "accounts", "distributions",
                    "nondeductible_contrib_current", "contrib_made_by_deadline", "outstanding_rollovers"}
        if not isinstance(raw, dict) or set(raw) != required:
            raise ValueError("Individual IRA worksheet fields must match the reviewed input contract")
        if raw["regular_worksheet_confirmed"] is not True or raw["accounts_complete"] is not True:
            raise ValueError("Confirm regular Form 8606 scope and all of this owner's IRA balances")
        accounts = raw["accounts"]
        if not isinstance(accounts, list):
            raise ValueError("IRA accounts must be a list (empty is an explicit zero balance)")
        value = amount(raw["outstanding_rollovers"], "outstanding IRA-to-IRA rollovers")
        for account in accounts:
            if not isinstance(account, dict) or set(account) != {"account_id", "person", "kind", "year_end_value"}:
                raise ValueError("IRA account needs account_id, person, kind and year_end_value")
            account_id = _text(account["account_id"], "IRA account_id")
            if account_id in seen_accounts:
                raise ValueError("Duplicate IRA account evidence; never count one balance twice")
            seen_accounts.add(account_id)
            if account["person"] != owner or account["kind"] not in IRA_KINDS:
                raise ValueError("Aggregate only this individual's traditional/SEP/SIMPLE IRAs")
            value += amount(account["year_end_value"], "IRA year-end value")
        distributions = raw["distributions"]
        if not isinstance(distributions, list):
            raise ValueError("IRA distributions must be a list")
        ordinary = converted = excluded = excluded_taxable = 0.0
        for row in distributions:
            fields = {"doc_id", "ordinary", "converted", "excluded", "excluded_taxable", "exclusion_reason"}
            if not isinstance(row, dict) or set(row) != fields:
                raise ValueError("IRA source allocation fields must match the distribution contract")
            doc_id = _text(row["doc_id"], "IRA doc_id")
            if doc_id in seen_docs:
                raise ValueError("Duplicate IRA source allocation; a document has one recipient")
            seen_docs.add(doc_id)
            ordinary += amount(row["ordinary"], "ordinary IRA distributions")
            converted += amount(row["converted"], "IRA conversions")
            ex = amount(row["excluded"], "excluded IRA gross")
            tx = amount(row["excluded_taxable"], "excluded IRA taxable amount")
            if tx > ex:
                raise ValueError("Excluded IRA taxable amount exceeds its gross")
            if ex and row["exclusion_reason"] not in EXCLUSIONS:
                raise ValueError("Excluded IRA distribution needs a reviewed supported reason")
            if not ex and (tx or row["exclusion_reason"] is not None):
                raise ValueError("An empty IRA exclusion must have null reason and zero taxable amount")
            if tx and row["exclusion_reason"] != "returned_contribution":
                raise ValueError("Only reviewed returned-contribution earnings may be taxable in exclusions")
            excluded += ex
            excluded_taxable += tx
        computed = compute_form_8606(
            amount(raw["nondeductible_contrib_current"], "nondeductible IRA contributions"),
            basis[owner], value, ordinary, converted,
            amount(raw["contrib_made_by_deadline"], "following-year IRA contributions"))
        computed.update(line_4_following_year_contributions=round(raw["contrib_made_by_deadline"], 2),
                        line_16_converted=computed["line_8_converted"],
                        line_17_nontaxable_conversion=computed["line_11_nontaxable_conversion"])
        gross = round(amount(ordinary + converted + excluded, "IRA gross total"), 2)
        taxable = round(computed["taxable_to_1040"] + excluded_taxable, 2)
        by_taxpayer[owner] = {"inputs": copy.deepcopy(raw), **computed,
                              "gross_to_1040": gross, "total_taxable_to_1040": taxable}
        total_gross += gross
        total_taxable += taxable
    return {"schema": SCHEMA, "tax_year": year, "filing_status": status,
            "inputs": copy.deepcopy(inputs), "by_taxpayer": by_taxpayer,
            "gross_to_1040": round(amount(total_gross, "household IRA gross"), 2),
            "taxable_to_1040": round(amount(total_taxable, "household IRA taxable total"), 2)}


def validate_individual_8606(result, profile):
    """Reproduce all owner worksheets and bind their totals to IRA-only lines."""
    section = result.get("form_8606") or {}
    if not isinstance(section, dict):
        raise ValueError("Form 8606 must be an object")
    if section.get("schema") not in (None, SCHEMA):
        raise ValueError("Unknown individual IRA worksheet schema")
    if section.get("schema") != SCHEMA:
        basis = opening_basis(profile)  # unresolved legacy ownership must not bypass the gate
        flags = profile.get("flags", {})
        if flags is None:
            flags = {}
        if not isinstance(flags, dict):
            raise ValueError("IRA screening flags must be an object")
        for flag in ("has_roth_conversion", "has_backdoor_roth"):
            if flags.get(flag) is not None and not isinstance(flags[flag], bool):
                raise ValueError("IRA conversion screening flags must be boolean")
        if profile.get("filing_status") == "MFJ" and (
                any(basis.values()) or flags.get("has_roth_conversion") is True
                or flags.get("has_backdoor_roth") is True or any(
                    section.get(k) for k in ("basis_remaining", "taxable_to_1040", "line_8_converted"))):
            raise ValueError("A legacy joint IRA basis/conversion requires individual worksheets; never assign its basis by guess")
        if ((profile.get("prior_year") or {}).get("ira_basis_by_taxpayer")
                and any(opening_basis(profile).values())):
            raise ValueError("Individual opening IRA basis requires individual Form 8606 worksheets")
        return
    expected = compute_individual_8606(section.get("inputs"), profile)
    for key, value in expected.items():
        if section.get(key) != value:
            raise ValueError("Form 8606 differs from its individual reviewed inputs")
    meta = result.get("meta") or {}
    if meta.get("tax_year") != expected["tax_year"] or meta.get("filing_status") != expected["filing_status"]:
        raise ValueError("IRA worksheet year/status differs from the return")
    form = result.get("form_1040") or {}
    for line, key in (("line_4a_ira_distributions", "gross_to_1040"),
                      ("line_4b_taxable_ira", "taxable_to_1040")):
        if abs(amount(form.get(line), line) - expected[key]) > 0.01:
            raise ValueError("1040 IRA line differs from the sum of individual IRA worksheets")


def tie_individual_8606(ledger, result):
    """Hard source coverage/recipient/gross check beyond household box totals."""
    section = result.get("form_8606") or {}
    if not isinstance(section, dict) or section.get("schema") != SCHEMA:
        if (result.get("meta") or {}).get("filing_status") == "MFJ" and any(
                record.skipped is None and record.retirement_kind in IRA_KINDS
                and dict(record.key_boxes).get("retirement_gross", 0) != 0 for record in ledger):
            raise ValueError("Joint IRA source distributions require individual worksheets before tie-out")
        return
    allocations = {}
    people = section.get("by_taxpayer")
    if not isinstance(people, dict):
        raise ValueError("Individual IRA worksheets are missing")
    for owner, worksheet in people.items():
        if owner not in OWNERS or not isinstance(worksheet, dict):
            raise ValueError("Invalid individual IRA worksheet")
        for row in (worksheet.get("inputs") or {}).get("distributions", []):
            if not isinstance(row, dict):
                raise ValueError("Invalid IRA source allocation")
            doc = row.get("doc_id")
            if not isinstance(doc, str) or doc in allocations:
                raise ValueError("Missing or duplicate IRA source allocation")
            allocations[doc] = (owner, sum(amount(row.get(k), "IRA source gross")
                                           for k in ("ordinary", "converted", "excluded")))
    covered = set()
    for record in ledger:
        boxes = dict(record.key_boxes)
        if record.skipped is not None or "retirement_gross" not in boxes:
            continue
        kind = record.retirement_kind
        if kind not in RETIREMENT_KINDS:
            raise ValueError("Classify each retirement source as IRA or pension before the IRA tie-out")
        if kind == "pension":
            continue
        if kind not in IRA_KINDS:
            raise ValueError("Inherited IRA and Roth-distribution ordering are outside the individual IRA worksheet scope")
        if record.person not in OWNERS:
            raise ValueError("An IRA cannot have joint ownership; confirm its individual recipient")
        allocation = allocations.get(record.doc_id)
        if allocation is None or allocation[0] != record.person:
            raise ValueError("IRA document missing from its own recipient's worksheet")
        if abs(allocation[1] - boxes["retirement_gross"]) > 0.01:
            raise ValueError("Individual IRA allocated gross differs from its source document")
        covered.add(record.doc_id)
    if covered != set(allocations):
        raise ValueError("IRA worksheet names a source with no eligible IRA distribution record")
