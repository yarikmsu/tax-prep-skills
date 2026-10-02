"""W10: the per-state tables derive from one registry, and nothing restates it.

Six parallel tables in four modules each had to be extended by hand when a
state was added, and nothing failed when one was missed. These pin that the
derived tables ARE the registry's projections, that every row the registry
holds is real in the result template, and that no other engine module carries
a literal listing two registered sections — which is what a local copy looks
like, and how the next drift would start.
"""

import ast
import json
from pathlib import Path

from agents.lib import projection, yoy_diff
from agents.lib.tax_computations.state import registry
from agents.lib.tax_computations.state.registry import STATE_SECTIONS
from tests.engine_tree import engine_files, engine_relpath

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = json.loads(
    (REPO / "agents" / "templates" / "result_template.json").read_text())


def _get(d, dotted):
    for part in dotted.split("."):
        if not isinstance(d, dict) or part not in d:
            return KeyError
        d = d[part]
    return d


def test_the_registry_holds_the_states_the_toolkit_knows():
    """California and New York computed in depth, Florida's statement — a
    state added or dropped is a decision, so the set is pinned exactly."""
    assert [s.code for s in STATE_SECTIONS] == ["CA", "FL", "NY"]
    assert set(registry.BY_CODE) == {"CA", "FL", "NY"}
    assert registry.BY_CODE["NY"].section == "new_york_it201"


def test_every_derived_table_is_the_registry_projection():
    assert projection.STATE_SECTION_KEYS == [s.section for s in STATE_SECTIONS]
    assert projection._STATE_SECTION_CODES == {
        s.section: s.code for s in STATE_SECTIONS}
    # IDENTITY, not equality: an equal local copy is what this refuses.
    assert projection._CA_COMPUTED_EVIDENCE_FIELDS is registry.BY_CODE[
        "CA"].evidence_fields
    assert projection._NY_COMPUTED_EVIDENCE_FIELDS is registry.BY_CODE[
        "NY"].evidence_fields
    from agents.lib import filing_package
    assert list(filing_package._STATE_SECTIONS.items()) == [
        f for s in STATE_SECTIONS for f in s.package_forms]
    assert set(s.section for s in STATE_SECTIONS) <= set(
        yoy_diff.OPTIONAL_SECTIONS)
    assert yoy_diff.INFORMATIONAL_SECTIONS == {"florida_state":
                                               "no_income_tax_statement"}
    assert set(yoy_diff.STATE_MOVER_FIELDS) == {"california_540",
                                                "new_york_it201"}
    for s in STATE_SECTIONS:
        assert yoy_diff.STATE_MOVER_FIELDS.get(s.section, []) == list(
            s.mover_fields), s.code


def test_every_registered_field_is_real_in_the_result_template():
    """A row naming a field the template does not carry is evidence nothing
    can produce, or a mover that always reads zero."""
    for s in STATE_SECTIONS:
        section = TEMPLATE.get(s.section)
        assert isinstance(section, dict), s.section
        for field in s.evidence_fields:
            assert _get(section, field) is not KeyError, (s.code, field)
        for _label, path in s.mover_fields:
            assert _get(TEMPLATE, path) is not KeyError, (s.code, path)
        if s.informational_key:
            assert s.informational_key in section, (s.code,
                                                    s.informational_key)
        for key, _title in s.package_forms:
            assert isinstance(TEMPLATE.get(key), dict), (s.code, key)


def _literal_copies(source: str) -> "list[tuple[int, list[str]]]":
    sections = {s.section for s in STATE_SECTIONS}
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            elements = node.elts
        elif isinstance(node, ast.Dict):
            elements = [k for k in node.keys if k is not None]
        else:
            continue
        named = sorted({e.value for e in elements
                        if isinstance(e, ast.Constant) and e.value in sections})
        if len(named) >= 2:
            found.append((node.lineno, named))
    return found


def test_no_engine_module_keeps_a_local_copy_of_the_registry():
    """A literal listing two registered sections is a parallel table: the next
    state added to the registry would not reach it, and nothing would fail."""
    copies = {}
    for path in engine_files():
        if path.resolve() == Path(registry.__file__).resolve():
            continue
        hits = _literal_copies(path.read_text(encoding="utf-8"))
        if hits:
            copies[engine_relpath(path)] = hits
    assert not copies, (
        f"per-state tables outside the registry: {copies}. Derive them from "
        "agents/lib/tax_computations/state/registry.py instead")


def test_the_copy_finder_finds_a_copy():
    """Non-vacuity: the check above passes only because it can fail."""
    assert _literal_copies(
        'X = ["california_540", "new_york_it201"]\n'
        'Y = {"florida_state": 1, "california_540": 2}\n'
        'Z = ("california_540",)\n') == [
            (1, ["california_540", "new_york_it201"]),
            (2, ["california_540", "florida_state"])]


def _window_copies(source: str) -> "list[int]":
    """Lines of a dict literal mapping two PTET states to a (year, year|None)
    pair — the shape of a regime-window table, whatever it is named."""
    def is_window(v):
        return (isinstance(v, ast.Tuple) and len(v.elts) == 2
                and all(isinstance(e, ast.Constant)
                        and (e.value is None or type(e.value) is int)
                        for e in v.elts))
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            hits = [k for k, v in zip(node.keys, node.values)
                    if isinstance(k, ast.Constant)
                    and k.value in registry.PTET_STATES and is_window(v)]
            if len(hits) >= 2:
                found.append(node.lineno)
    return found


def test_ptet_windows_have_one_copy():
    """`/tax-ptet` and `/tax-calendar` each carried their own PTET windows,
    commented "MUST stay in sync"; both now read the registry's. A window
    table anywhere else, under any name, is the next drift."""
    copies = {engine_relpath(p): hits for p in engine_files()
              if p.resolve() != Path(registry.__file__).resolve()
              and (hits := _window_copies(p.read_text(encoding="utf-8")))}
    assert not copies, f"PTET window tables outside the registry: {copies}"
    assert registry.PTET_STATES == {"CA", "NY", "NJ"}


def test_the_window_finder_finds_a_copy():
    """Non-vacuity, and the shape it must NOT match: a rate schedule."""
    assert _window_copies(
        'W = {"CA": (2021, 2030), "NJ": (2020, None)}\n'
        'R = {"CA": [(0.0, 0.093, 0.0)], "NY": [(0.0, 0.0685, 0.0)]}\n') == [1]


def test_the_shared_windows_are_read_only():
    import pytest
    with pytest.raises(TypeError):
        registry.PTET_WINDOWS["CA"] = (2021, 2035)  # type: ignore[index]


def test_ptet_window_edges():
    """CA's SB 132 sunset after TY2030 and the first year of each regime."""
    active = registry.ptet_window_active
    assert not active("CA", 2020) and active("CA", 2021)
    assert active("CA", 2030) and not active("CA", 2031)
    assert not active("NY", 2020) and active("NY", 2099)
    assert not active("NJ", 2019) and active("NJ", 2020)
