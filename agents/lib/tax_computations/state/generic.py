"""Generic state adjustments and local-tax routing, not a filing module."""

import copy
import math
import re

import agents.lib  # noqa: F401 — enforce the workspace interpreter
from agents.lib.tax_computations._shared import _STATE_NAME_TO_CODE


def _amount(value, *, negative=False):
    try:
        finite = math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        finite = False
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not finite or (not negative and value < 0)):
        raise ValueError("State worksheet amounts must be finite numbers in the allowed range")
    return round(float(value), 2)


def _rules(block, kind, year=None):
    field = "rules" if kind == "modifications" else "jurisdictions"
    if (not isinstance(block, dict)
            or set(block) != {"tax_year", "coverage_confirmed", field}
            or block.get("coverage_confirmed") is not True
            or type(block.get("tax_year")) is not int or block["tax_year"] < 1
            or (year is not None and block["tax_year"] != year)
            or not isinstance(block.get(field), list)):
        raise ValueError(f"Fetch a complete state.{kind} block for the covered year")
    ids = set()
    for row in block[field]:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", row["id"])
                or row["id"] in ids):
            raise ValueError(f"state.{kind} requires unique lower-case rule ids")
        ids.add(row["id"])
    return block[field]


def validate_state_modifications(block, tax_year=None):
    for row in _rules(block, "modifications", tax_year):
        if (set(row) != {"id", "description", "direction"}
                or not isinstance(row["description"], str) or not row["description"].strip()
                or row["direction"] not in ("add", "subtract")):
            raise ValueError("State modification rules require a description and add/subtract direction")


def validate_state_local(block, tax_year=None):
    from agents.lib.tax_law_fetcher import _check_brackets
    for row in _rules(block, "local", tax_year):
        if (set(row) != {"id", "name", "form", "tax_base", "method", "rate", "brackets"}
                or any(not isinstance(row[k], str) or not row[k].strip() for k in ("name", "form"))
                or row["tax_base"] not in ("local_taxable_income", "wages", "state_tax")
                or row["method"] not in ("rate", "brackets", "manual")):
            raise ValueError("Invalid local jurisdiction, tax base or method")
        if row["method"] == "rate":
            rate = row["rate"]
            if (isinstance(rate, bool) or not isinstance(rate, (int, float))
                    or not math.isfinite(rate) or not 0 < rate <= 1 or row["brackets"] != []):
                raise ValueError("Local rate method requires a fetched rate and no brackets")
        elif row["method"] == "brackets":
            if row["rate"] is not None or row["tax_base"] == "state_tax":
                raise ValueError("Local bracket method requires an income base and no flat rate")
            try:
                _check_brackets("state.local.jurisdictions.brackets", copy.deepcopy(row["brackets"]))
            except ValueError:
                raise ValueError("Local bracket method requires a valid fetched schedule") from None
        elif row["rate"] is not None or row["brackets"] != []:
            raise ValueError("Manual local rules must not carry unused rates or brackets")


def state_rules_for_year(tax_law: dict, tax_year: int, kind: str) -> dict:
    """Read an independently pinned optional block, never infer a missing one."""
    from agents.lib.tax_law_fetcher import _source_host_authoritative
    if kind not in ("modifications", "local"):
        raise ValueError("Unknown optional state rule block")
    if (not isinstance(tax_law, dict) or type(tax_year) is not int
            or type(tax_law.get("tax_year")) is not int or tax_law["tax_year"] != tax_year):
        raise ValueError("State rule cache must name the covered year")
    state = tax_law.get("state")
    if not isinstance(state, dict) or not isinstance(state.get("code"), str):
        raise ValueError("State rule cache must identify its state")
    code = state["code"]
    if code not in _STATE_NAME_TO_CODE.values():
        raise ValueError("State rule cache must identify a valid state")
    pin = f"state.{code}.{kind}"
    sources = tax_law.get("_sources")
    if not isinstance(sources, dict) or not _source_host_authoritative(sources.get(pin)):
        raise ValueError(f"Fetch and pin {pin} to a primary authority")
    block = state.get(kind)
    if not isinstance(block, dict):
        raise ValueError(f"Fetch a complete state.{kind} block")
    validator = validate_state_modifications if kind == "modifications" else validate_state_local
    validator(block, tax_year)
    return block


def compute_state_modifications(federal_agi: float, amounts: dict, rules: dict,
                                tax_year: int) -> dict:
    """Sum reviewed worksheet outputs; missing inputs are never zero."""
    if type(tax_year) is not int:
        raise ValueError("State worksheet must name its tax year")
    validate_state_modifications(rules, tax_year)
    federal_agi = _amount(federal_agi, negative=True)
    rows = rules["rules"]
    if not isinstance(amounts, dict) or set(amounts) != {row["id"] for row in rows}:
        raise ValueError("State modification amounts must answer every rule id exactly, including zeroes")
    workpaper = []
    additions = subtractions = 0.0
    for row in rows:
        amount = _amount(amounts[row["id"]])
        if row["direction"] == "add":
            additions += amount
        else:
            subtractions += amount
        workpaper.append({"id": row["id"], "direction": row["direction"], "amount": amount})
    return {"federal_agi": federal_agi, "additions": _amount(additions),
            "subtractions": _amount(subtractions),
            "state_agi": _amount(federal_agi + additions - subtractions, negative=True),
            "workpaper": workpaper}


def state_local_requirements(rules: dict, applicability: dict, tax_year: int) -> dict:
    """Applicable local taxes require their dedicated module or manual form.

    This routes work; it never prices a city return from a state AGI proxy.
    """
    if type(tax_year) is not int:
        raise ValueError("Local worksheet must name its tax year")
    validate_state_local(rules, tax_year)
    rows = rules["jurisdictions"]
    if (not isinstance(applicability, dict)
            or set(applicability) != {row["id"] for row in rows}
            or any(type(v) is not bool for v in applicability.values())):
        raise ValueError("Answer applicability explicitly for every local jurisdiction")
    selected = [row for row in rows if applicability[row["id"]]]
    return {"applicable": [row["id"] for row in selected],
            "manual_forms": list(dict.fromkeys(row["form"] for row in selected))}
