"""W10 generic rule contracts; all figures are synthetic worksheet examples."""

import copy
import datetime
from dataclasses import replace

import pytest

from agents.lib import projection as P
from agents.lib import tax_law_fetcher as F
from agents.lib.tax_computations import (
    compute_state_modifications,
    state_local_requirements,
    state_rules_for_year,
)
from agents.lib.tax_computations.state.generic import (
    validate_state_local,
    validate_state_modifications,
)
from tests.test_projection import _proj, _tax_law_2026

YEAR = 2026
SOURCE = "https://www.example.gov/2026/instructions"


def modifications():
    return {"tax_year": YEAR, "coverage_confirmed": True, "rules": [
        {"id": "bond_interest", "description": "Reviewed interest addition", "direction": "add"},
        {"id": "treasury_interest", "description": "Reviewed interest exclusion", "direction": "subtract"},
    ]}


def local(method="rate"):
    return {"tax_year": YEAR, "coverage_confirmed": True, "jurisdictions": [{
        "id": "city", "name": "Synthetic municipality", "form": "LOCAL-1",
        "tax_base": "wages", "method": method,
        "rate": 0.03 if method == "rate" else None,
        "brackets": [{"min": 0, "max": None, "rate": 0.03}] if method == "brackets" else [],
    }]}


def law(kind, block):
    data = _tax_law_2026()
    data["tax_year"] = YEAR
    data["state"]["code"] = "CA"
    data["state"][kind] = block
    data.setdefault("_sources", {})[f"state.CA.{kind}"] = SOURCE
    return data


def test_hand_computed_adjustments_and_negative_agi():
    # Worksheet: 80,000 + 1,200.25 - 3,000.10 = 78,200.15.
    amounts = {"bond_interest": 1200.25, "treasury_interest": 3000.10}
    report = compute_state_modifications(80000, amounts, modifications(), YEAR)
    assert report["state_agi"] == 78200.15
    assert report["additions"] == 1200.25
    assert report["subtractions"] == 3000.10
    assert report["workpaper"][1]["amount"] == 3000.10
    assert compute_state_modifications(-100, amounts, modifications(), YEAR)["state_agi"] == -1899.85


@pytest.mark.parametrize("bad", [None, {}, {"bond_interest": 0},
                                   {"bond_interest": 0, "treasury_interest": 0, "typo": 0}])
def test_every_adjustment_needs_an_explicit_amount(bad):
    with pytest.raises(ValueError):
        compute_state_modifications(80000, bad, modifications(), YEAR)


@pytest.mark.parametrize("bad", [None, True, "0", -1, float("nan"), float("inf"), -float("inf")])
def test_bad_amounts_refuse(bad):
    with pytest.raises(ValueError):
        compute_state_modifications(80000, {"bond_interest": bad, "treasury_interest": 0},
                                    modifications(), YEAR)


def test_overflow_refuses():
    block = modifications()
    block["rules"][1]["direction"] = "add"
    with pytest.raises(ValueError):
        compute_state_modifications(0, {"bond_interest": 1e308, "treasury_interest": 1e308}, block, YEAR)


@pytest.mark.parametrize("kind,validator,block", [
    ("modifications", validate_state_modifications, modifications()),
    ("local", validate_state_local, local()),
])
@pytest.mark.parametrize("mutation", ["year", "confirmation", "extra", "duplicate", "id"])
def test_schema_mutations_refuse(kind, validator, block, mutation):
    block = copy.deepcopy(block)
    field = "rules" if kind == "modifications" else "jurisdictions"
    if mutation == "year":
        block["tax_year"] = YEAR - 1
    elif mutation == "confirmation":
        block["coverage_confirmed"] = False
    elif mutation == "extra":
        block["typo"] = 0
    elif mutation == "duplicate":
        block[field].append(copy.deepcopy(block[field][0]))
    else:
        block[field][0]["id"] = "Wrong ID"
    with pytest.raises(ValueError):
        validator(block, YEAR)


@pytest.mark.parametrize("method", ["rate", "brackets", "manual"])
def test_local_methods_route_to_manual_forms_without_pricing(method):
    rules = local(method)
    original = copy.deepcopy(rules)
    assert state_local_requirements(rules, {"city": True}, YEAR) == {
        "applicable": ["city"], "manual_forms": ["LOCAL-1"]}
    assert state_local_requirements(rules, {"city": False}, YEAR)["manual_forms"] == []
    assert rules == original


@pytest.mark.parametrize("answer", [None, {}, {"city": None}, {"city": 0}, {"city": "false"},
                                      {"city": False, "typo": False}])
def test_local_applicability_is_never_inferred(answer):
    with pytest.raises(ValueError):
        state_local_requirements(local(), answer, YEAR)


@pytest.mark.parametrize("mutation", ["method", "base", "rate_nan", "rate_zero", "brackets"])
def test_local_parameters_are_validated(mutation):
    rules = local()
    row = rules["jurisdictions"][0]
    if mutation == "method":
        row["method"] = "guess"
    elif mutation == "base":
        row["tax_base"] = "agi"
    elif mutation == "rate_nan":
        row["rate"] = float("nan")
    elif mutation == "rate_zero":
        row["rate"] = 0
    else:
        row.update(method="brackets", rate=None, brackets=[{"min": 100, "max": 500, "rate": 0.03}])
    with pytest.raises(ValueError):
        validate_state_local(rules, YEAR)


@pytest.mark.parametrize("kind,block", [("modifications", modifications()), ("local", local())])
def test_setter_pins_complete_blocks_and_rejects_partial_attestations(tmp_path, kind, block):
    data = F.create_empty_tax_law(YEAR)
    data["state"] = F.create_state_template("CA", YEAR)
    F.save_tax_law(YEAR, data, str(tmp_path))
    F.set_figure(YEAR, f"state.{kind}", block, SOURCE, str(tmp_path))
    saved = F.load_tax_law(YEAR, str(tmp_path))
    assert state_rules_for_year(saved, YEAR, kind) == block
    assert saved["_provenance"][f"state.CA.{kind}"]["applies_to"] == YEAR
    assert (f"state.CA.{kind}", f"state.{kind}") in F._expected_pins(saved)
    assert not any(f"state.{kind}" in p for p in F.validate_cache(saved, YEAR))
    before = F.get_tax_law_path(YEAR, str(tmp_path)).read_bytes()
    with pytest.raises(F.TaxLawSetError):
        F.set_figure(YEAR, f"state.{kind}.tax_year", YEAR, SOURCE, str(tmp_path))
    with pytest.raises(F.TaxLawSetError):
        F.set_figure(YEAR, f"state.{kind}", dict(block, tax_year=YEAR - 1), SOURCE, str(tmp_path))
    assert F.get_tax_law_path(YEAR, str(tmp_path)).read_bytes() == before
    saved["state"][kind]["tax_year"] = YEAR - 1
    assert any(f"state.{kind}" in p for p in F.validate_cache(saved, YEAR))


def test_pinning_and_identity_cannot_be_borrowed_from_parent():
    data = law("modifications", modifications())
    data["_sources"] = {"state.CA": SOURCE}
    with pytest.raises(ValueError, match="pin"):
        state_rules_for_year(data, YEAR, "modifications")
    data["_sources"]["state.CA.modifications"] = "https://example.com/2026"
    with pytest.raises(ValueError):
        state_rules_for_year(data, YEAR, "modifications")
    data["state"]["code"] = "ZZ"
    with pytest.raises(ValueError):
        state_rules_for_year(data, YEAR, "modifications")


def test_confirmed_empty_lists_are_complete_and_scaffold_is_unchanged(tmp_path):
    seed = F.create_empty_tax_law(YEAR)
    assert "modifications" not in seed["state"] and "local" not in seed["state"]
    for kind, field in (("modifications", "rules"), ("local", "jurisdictions")):
        block = {"tax_year": YEAR, "coverage_confirmed": True, field: []}
        data = law(kind, block)
        F.save_tax_law(YEAR, data, str(tmp_path))
        report = F.check_freshness(YEAR, str(tmp_path))
        assert not any(f"state.{kind}" in str(p) for p in report["null_fields"])
    assert compute_state_modifications(123.45, {}, {
        "tax_year": YEAR, "coverage_confirmed": True, "rules": []}, YEAR)["state_agi"] == 123.45


def test_projection_applies_reviewed_annual_amounts_and_preserves_other_inputs():
    data = law("modifications", modifications())
    baseline = P.project_state_tax(_proj(), None, _tax_law_2026(), "MFJ", resident_state="CA")
    projected = replace(_proj(), state_modification_amounts={"bond_interest": 1200, "treasury_interest": 3000})
    changed = P.project_state_tax(projected, None, data, "MFJ", resident_state="CA")
    assert changed.agi == baseline.agi - 1800
    assert changed.taxable_income == baseline.taxable_income - 1800
    assert changed.withholding == baseline.withholding
    with pytest.raises(P.ProjectionError):
        P.project_state_tax(_proj(), None, data, "MFJ", resident_state="CA")


def test_supplied_answers_cannot_disappear_when_the_cache_block_is_absent():
    for projected in (
        replace(_proj(), state_modification_amounts={"bond_interest": 1000}),
        replace(_proj(), state_local_applicability={"city": False}),
    ):
        with pytest.raises(P.ProjectionError, match="Fetch state"):
            P.project_state_tax(projected, None, _tax_law_2026(), "MFJ", resident_state="CA")


def test_annual_answers_are_not_scaled_from_ytd():
    forecast = {"state_modification_amounts": {"bond_interest": 1200, "treasury_interest": 0},
                "state_local_applicability": {"city": False}}
    projected = P.annualize({}, datetime.date(YEAR, 6, 30), "biweekly", forecast, {}, {})
    assert projected.state_modification_amounts == forecast["state_modification_amounts"]
    assert projected.state_local_applicability == forecast["state_local_applicability"]


def test_applicable_local_tax_blocks_a_false_refund_from_combined_withholding():
    data = law("local", local())
    projected = replace(_proj(state_withholding=100000), state_local_applicability={"city": True})
    with pytest.raises(P.ProjectionError, match="LOCAL-1"):
        P.project_state_tax(projected, None, data, "MFJ", resident_state="CA")
    nonresident = replace(projected, state_local_applicability={"city": False})
    assert P.project_state_tax(nonresident, None, data, "MFJ", resident_state="CA") is not None


@pytest.mark.parametrize("field,value", [
    ("state_modification_amounts", {"x": None}), ("state_modification_amounts", {"x": float("nan")}),
    ("state_local_applicability", {"city": "false"}), ("state_local_applicability", []),
])
def test_projection_parse_boundary_rejects_bad_answers(field, value):
    with pytest.raises(P.ProjectionError):
        P.annualize({}, datetime.date(YEAR, 6, 30), "biweekly", {field: value}, {}, {})
