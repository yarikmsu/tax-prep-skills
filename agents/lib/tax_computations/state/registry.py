"""The state REGISTRY: one row per state section the toolkit knows (W10).

Six parallel per-state tables in four modules each had to be found and
extended by hand when a state was added, and nothing failed when one was
missed — a projection that could not tell a prior return was computed, a
year-over-year diff that never saw the section appear. Each module now DERIVES
its table from these rows, and `tests/test_state_registry.py` refuses a
literal that lists two registered sections anywhere else, which is what a
local copy looks like.

It also holds the PTET regime windows, a table of their own (NJ has a PTET
and no result section).

Shared metadata and variant selection. Behaviour that differs by state — the capability gate's notes, the
calendar's estimated-tax events, the tie-out's withholding categories — stays
in the module that owns the question, keyed by `code`; this holds what those
modules would otherwise each restate. It imports only the `agents.lib`
package and standard library (the host-interpreter guard `test_host_guard` requires of every engine
file), so any engine module can read it without a cycle.
"""

from dataclasses import dataclass
from types import MappingProxyType

import agents.lib  # noqa: F401 — the host-interpreter guard


@dataclass(frozen=True)
class StateSection:
    """What the toolkit knows about one state's result section.

    `evidence_fields` are outputs of the state COMPUTATION, never a figure
    that can arrive from parsing, so a non-zero one proves the section was
    computed (`projection._section_computed`); empty where the toolkit does
    not compute the state's return. `mover_fields` are `/tax-review`'s
    year-over-year rows, (label, dotted path). `informational_key` names the
    one string field whose non-empty value means a section that holds only a
    statement was populated (`yoy_diff._section_present`).
    `package_forms` are the (result section, title) pairs `/tax-generate`'s
    filing package assembles after the federal forms, in order; empty for a
    state that files no return.
    """
    code: str
    section: str
    evidence_fields: "tuple[str, ...]" = ()
    mover_fields: "tuple[tuple[str, str], ...]" = ()
    informational_key: "str | None" = None
    package_forms: "tuple[tuple[str, str], ...]" = ()
    regime: str = "graduated_income"
    variant: str | None = None


# In DETECTION order: `projection._detect_prior_state` takes the first section
# a prior return holds.
STATE_SECTIONS: "tuple[StateSection, ...]" = (
    StateSection(
        code="CA", section="california_540",
        package_forms=(
            ("california_540", "California Form 540"),
            ("schedule_ca_itemized", "California Schedule CA (540)"),
        ),
        # A genuinely computed CA return always carries a non-zero
        # ca_standard_deduction (statutory minimum ~$5K), even at zero income.
        evidence_fields=("ca_total_tax", "ca_agi", "ca_taxable_income",
                         "ca_standard_deduction", "ca_tax_from_table"),
        mover_fields=(
            ("State taxable income", "california_540.ca_taxable_income"),
            ("State total tax", "california_540.ca_total_tax"),
            ("State refund", "california_540.ca_refund"),
        ),
    ),
    StateSection(
        code="FL", section="florida_state", regime="none",
        # Florida's section is a no-income-tax STATEMENT plus zero/bool
        # scaffold, so the generic non-empty scan would read a populated one
        # as absent; this string is what proves it was written.
        informational_key="no_income_tax_statement",
    ),
    StateSection(
        code="NY", section="new_york_it201",
        package_forms=(("new_york_it201", "New York Form IT-201"),),
        # Line 72's box 17 withholding lands before IT-201 is computed, so it
        # proves nothing; line 34's standard deduction has a floor for every
        # status, and line 33 may be negative, which is non-zero too.
        evidence_fields=("line_46_total_state_tax", "line_33_nyagi",
                         "line_37_taxable_income", "line_34_deduction",
                         "line_39_ny_tax"),
        # Line 46 is the STATE total, not line 39's tax before credits; line
        # 58's City, Yonkers and MCTMT total has no California counterpart,
        # and a City resident's move is often the larger one.
        mover_fields=(
            ("State taxable income", "new_york_it201.line_37_taxable_income"),
            ("State total tax", "new_york_it201.line_46_total_state_tax"),
            ("City / Yonkers / MCTMT",
             "new_york_it201.line_58_total_city_and_yonkers"),
            ("State refund", "new_york_it201.line_78_refund"),
        ),
    ),
    StateSection(
        code="NC", section="north_carolina_d400",
        package_forms=(("north_carolina_d400", "North Carolina Form D-400"),),
        evidence_fields=("line_15_nc_tax", "line_14_nc_taxable_income",
                         "line_12b", "line_11_deduction"),
        mover_fields=(
            ("State taxable income", "north_carolina_d400.line_14_nc_taxable_income"),
            ("State total tax", "north_carolina_d400.line_19"),
            ("State refund", "north_carolina_d400.line_34_refund"),
        ),
    ),
)

STATE_SECTIONS += (StateSection(
    code="WA", section="washington_ltcg_excise", regime="capital_gains_excise",
    evidence_fields=("computed_tax_year",),
    mover_fields=(("State excise taxable gain", "washington_ltcg_excise.taxable_wa_gain"),
                  ("State excise total tax", "washington_ltcg_excise.total_tax")),
    package_forms=(("washington_ltcg_excise", "Washington capital-gains excise (My DOR)"),),
),)

STATE_SECTIONS += (StateSection(
    code="NY", section="new_york_it203", variant="IT203",
    evidence_fields=("computed_tax_year", "line_33_deduction"),
    mover_fields=(("State taxable income", "new_york_it203.line_37_taxable_income"),
                  ("State allocated tax", "new_york_it203.line_50_state_tax"),
                  ("City / MCTMT", "new_york_it203.line_55_city_and_mctmt"),
                  ("State refund", "new_york_it203.line_68_refund")),
    package_forms=(("new_york_it203", "New York Form IT-203"),),
),)

STATE_SECTIONS += (StateSection(
    code="CA", section="california_540nr", variant="540NR",
    evidence_fields=("computed_tax_year",),
    mover_fields=(("State taxable income", "california_540nr.line_35"),
                  ("State total tax", "california_540nr.line_74"),
                  ("State refund", "california_540nr.line_125")),
    package_forms=(("california_540nr", "California Form 540NR"),),
),)

# Primary full-year rows retain their identities. Variants cannot overwrite them.
BY_CODE: "dict[str, StateSection]" = {s.code: s for s in STATE_SECTIONS if s.variant is None}
BY_SECTION: dict[str, StateSection] = {s.section: s for s in STATE_SECTIONS}


def selected_ca_return(result: dict) -> str | None:
    """Select a single CA variant without treating withholding as computation."""
    import math
    computed = []
    for row in (s for s in STATE_SECTIONS if s.code == "CA"):
        node = result.get(row.section)
        if node is None:
            continue
        if not isinstance(node, dict):
            raise ValueError("CA return section must be an object")
        if row.variant is not None and node.get("computed_tax_year") is not None:
            year = node["computed_tax_year"]
            if type(year) is not int or not 2000 <= year <= 2099:
                raise ValueError("CA variant computation witness must be an integer tax year")
        for path in row.evidence_fields:
            value: object = node
            for part in path.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value != 0:
                computed.append(row.variant or "540")
                break
    meta = result.get("meta")
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ValueError("result metadata must be an object")
    selected = meta.get("ca_return_type")
    if selected is not None and (not isinstance(selected, str) or selected not in {"540", "540NR"}):
        raise ValueError("unknown California return variant")
    if len(computed) > 1 or (selected and computed and selected != computed[0]):
        raise ValueError("competing California return variants; select and compute one return")
    return selected or (computed[0] if computed else None)


def ca_section_key(form: str = "540") -> str:
    """Read the registered CA section for a validated form name."""
    if form == "540":
        return BY_CODE["CA"].section
    row = next((s for s in STATE_SECTIONS if s.code == "CA" and s.variant == form), None)
    if row is None:
        raise ValueError("unknown California return variant")
    return row.section


def selected_ny_return(result: dict) -> str | None:
    """Refuse competing NY computation evidence; select one filing form."""
    import math
    rows = [s for s in STATE_SECTIONS if s.code == "NY"]
    computed: list[str] = []
    for row in rows:
        node = result.get(row.section) or {}
        if not isinstance(node, dict):
            raise ValueError("NY return section must be an object")
        for path in row.evidence_fields:
            value: object = node
            for part in path.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value != 0:
                computed.append(row.variant or "IT201")
                break
    meta = result.get("meta") or {}
    if not isinstance(meta, dict):
        raise ValueError("Result metadata must be an object")
    selected = meta.get("ny_return_type")
    if selected is not None and (not isinstance(selected, str) or selected not in {"IT201", "IT203"}):
        raise ValueError("Unknown NY return variant")
    if len(computed) > 1 or (selected and computed and selected != computed[0]):
        raise ValueError("Competing NY return variants; select and compute one return")
    return selected or (computed[0] if computed else None)


# Per-state effective tax-year windows of the elective pass-through-entity tax
# regimes the toolkit models — (first_valid_ty, last_valid_ty or None for "no
# sunset to date"). A SEPARATE table from the sections above: NJ has a PTET
# the toolkit prices and no result section. `/tax-ptet` and `/tax-calendar`
# both read this one copy (W10; they each carried their own, "MUST stay in
# sync"). A year outside its window means the regime did not exist or has
# sunset, so neither prices it nor schedules it. Sources:
#   CA: AB 150 (2021-2025) + SB 132 (2026-2030) — sunset after TY2030
#   NY: NY Tax Law §860 — created TY2021, no sunset
#   NJ: P.L. 2019 c.320 — created TY2020, no sunset
# Read-only: two modules share it, and neither may move a year for the other.
PTET_WINDOWS: "MappingProxyType[str, tuple[int, int | None]]" = MappingProxyType({
    "CA": (2021, 2030),
    "NY": (2021, None),
    "NJ": (2020, None),
})
PTET_STATES: frozenset = frozenset(PTET_WINDOWS)


def ptet_window_active(code: str, tax_year: int) -> bool:
    """True when `code`'s PTET regime exists for `tax_year`; KeyError for a
    state not in `PTET_WINDOWS`, so a caller tests membership first."""
    first, last = PTET_WINDOWS[code]
    return first <= tax_year and (last is None or tax_year <= last)
