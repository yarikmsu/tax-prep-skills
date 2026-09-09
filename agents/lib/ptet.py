#!/usr/bin/env python3
"""PTET (Pass-Through Entity Tax) election analyzer for /tax-ptet (Backlog #9).

The Tax Cuts and Jobs Act capped the federal itemized deduction for state and
local taxes (SALT) at $10,000. A pass-through entity (partnership / S-corp) can
elect to pay its owners' state income tax AT THE ENTITY LEVEL, where it is fully
deductible against the federal pass-through income — bypassing the individual
$10,000 cap. The owner then claims a state credit (or income exclusion) for the
PTET paid, so the state isn't double-taxed. Net federal benefit ≈
`federal_marginal_rate × PTET_paid` whenever the owner's $10,000 SALT cap is
already exhausted by other state/local taxes (the common case for the high-income
pass-through owners this regime targets).

This module is the analyzer behind `/tax-ptet` and the PTET sections the backlog
calls for in `/tax-review` and `/tax-project`. It mirrors the established
read-only planning pattern (projection.py / withholding.py / yoy_diff.py): a pure
compute core + markdown renderer + CLI `_main`, reusing `scenario.py` to ANCHOR
the federal benefit to the real `result.json` line 24 (the entity deduction is
modeled as a negative `other_income` delta through the `compute_scenario`
keystone, so bracket positioning and additional-Medicare interactions are priced
automatically; the §199A QBI clawback is priced as an explicit haircut when
`--qbi-eligible` is set, since the keystone can't recompute QBI for income that
flows as `other_income`). It NEVER mutates `result.json` or `taxpayer_profile.json`, and —
unlike the scenario/harvest/roth-ladder skills — writes NO artifact at all: it is
advisory output, like a `/tax-review` section.

Federal-only v1. State savings is reported as $0 with a caveat: the owner's PTET
credit generally offsets the entity tax dollar-for-dollar at the state level
(CA's credit is nonrefundable with a 5-year carryforward; NJ's is refundable;
NY's is refundable), so PTET is roughly state-revenue-neutral to the owner — the
whole point is the FEDERAL deduction.

Scope: CA / NY / NJ only (the three states with mature elective-PTET regimes the
toolkit models elsewhere, e.g. tax_calendar.py). Graduated NY/NJ rates are applied
to the OWNER's supplied qualifying income as a per-owner approximation; for
multi-owner entities whose entity-level pool sits in a different bracket, pass
`--ptet-rate STATE=0.0xx` to override. The estimate assumes the $10,000 SALT cap
is already fully consumed by the owner's other state/local taxes — pass
`--salt-room` to model unused cap headroom (which reduces the federal benefit).
"""

from __future__ import annotations

import argparse
import datetime
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.projection import (  # noqa: E402
    ProjectionError,
    _money,
    _read_json,
)
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_year_arg,
)
from agents.lib.scenario import (  # noqa: E402
    ScenarioError,
    _rate_kwargs,
    extract_baseline_inputs,
    read_tax_year,
)
from agents.lib.tax_computations import compute_scenario  # noqa: E402
from agents.lib.irs_dates import next_business_day_state  # noqa: E402
from agents.lib.secure_fs import redact  # noqa: E402
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    resolve_under_workspace,
    resolve_workspace,
)


class PtetError(Exception):
    """Raised when /tax-ptet cannot safely proceed."""


# States with mature elective pass-through-entity tax regimes that the toolkit
# models. Other states either have no PTET or differ enough that a blanket
# estimate would mislead — those are surfaced as a "not modeled" note.
_PTET_STATES = frozenset({"CA", "NY", "NJ"})

# Per-state effective tax-year windows for the PTE elective tax regime —
# (first_valid_ty, last_valid_ty or None for "no sunset to date"). MUST stay in
# sync with tax_calendar._PTET_WINDOWS (same sources): a tax_year outside its
# state's window means the regime didn't exist / has sunset, so we surface a
# "not modeled" note instead of confidently pricing an expired/not-yet-created
# regime. Sources:
#   CA: AB 150 (2021-2025) + SB 132 (2026-2030) — sunset after TY2030
#   NY: NY Tax Law §860 — created TY2021, no sunset
#   NJ: P.L. 2019 c.320 — created TY2020, no sunset
_PTET_WINDOWS = {
    "CA": (2021, 2030),
    "NY": (2021, None),
    "NJ": (2020, None),
}


def _window_active(state: str, tax_year: int) -> bool:
    first, last = _PTET_WINDOWS[state]
    if tax_year < first:
        return False
    return last is None or tax_year <= last

# Per-state graduated PTET rate schedules, as (lower_bound, rate,
# cumulative_tax_at_lower_bound). The tax on `income` is
# `base_tax + rate × (income − lower_bound)` for the bracket containing income.
# These are STRUCTURAL statutory constants (like the deadline dates in
# tax_calendar.py), cited below; the user can override per state with
# `--ptet-rate`. Verify against the state authority for the year — PTET rates
# have been amended (e.g. NJ's 2022 BAIT overhaul).
_PTET_RATE_SCHEDULES = {
    # CA R&TC §19900: flat 9.3% elective tax on qualified net income.
    "CA": [(0.0, 0.093, 0.0)],
    # NY Tax Law §860 / §864: graduated on PTE taxable income.
    #   ≤$2M: 6.85%; $2M–$5M: 9.65%; $5M–$25M: 10.30%; >$25M: 10.90%.
    "NY": [
        (0.0, 0.0685, 0.0),
        (2_000_000.0, 0.0965, 137_000.0),
        (5_000_000.0, 0.1030, 426_500.0),
        (25_000_000.0, 0.1090, 2_486_500.0),
    ],
    # NJ BAIT, N.J.S.A. 54A:12-3 as amended by P.L. 2021 c.419, effective TY2022:
    #   ≤$250K: 5.675%; $250K–$1M: 6.52%; >$1M: 10.9%.
    # The 2022 revision COLLAPSED the legacy 9.12% $1M–$5M and 10.9% >$5M brackets
    # into a single 10.9% over $1M (NJ Division of Taxation PTE/BAIT rate schedule).
    "NJ": [
        (0.0, 0.05675, 0.0),
        (250_000.0, 0.0652, 14_187.50),
        (1_000_000.0, 0.1090, 63_087.50),
    ],
}

# NJ's pre-2022 (TY2020–2021) schedule kept a separate 9.12% $1M–$5M bracket.
# Used only when tax_year < 2022 (see _schedule_for); the current schedule above
# is the default for the forward-looking planning case.
_NJ_SCHEDULE_LEGACY_2020 = [
    (0.0, 0.05675, 0.0),
    (250_000.0, 0.0652, 14_187.50),
    (1_000_000.0, 0.0912, 63_087.50),
    (5_000_000.0, 0.1090, 427_887.50),
]


def _schedule_for(state: str, tax_year: "int | None") -> list:
    """Return the built-in statutory PTET schedule for a state in a given tax
    year. The FALLBACK when no fetched schedule is present (see
    _resolve_schedule).

    Only NJ's schedule is year-dependent (the 2022 BAIT bracket collapse); CA's
    flat 9.3% and NY's graduated rates have been stable across their windows.
    """
    if state == "NJ" and tax_year is not None and tax_year < 2022:
        return _NJ_SCHEDULE_LEGACY_2020
    return _PTET_RATE_SCHEDULES[state]


# Statutory vintage of each built-in fallback schedule — named in the
# loud-degrade warning so a reader knows exactly which un-fetched schedule
# priced their PTET and can verify it against the state authority.
_SCHEDULE_VINTAGE = {
    "CA": "CA R&TC §19900 flat 9.3%",
    "NY": "NY Tax Law §860/§864 graduated (≤$2M 6.85% … >$25M 10.9%)",
    "NJ": "NJ BAIT N.J.S.A. 54A:12-3 as amended by P.L. 2021 c.419 (TY2022+, top 10.9% over $1M)",
    "NJ_LEGACY": "NJ BAIT legacy TY2020-2021 4-bracket schedule",
}


def _validate_fetched_schedule(state: str, block) -> list:
    """Validate a fetched tax_law.ptet.<state> block into a
    [(lower, rate, base_tax)] schedule. A PRESENT block that is malformed is a
    hard error (a corrupt cache must never silently price the wrong PTET) — the
    salt_cap corrupt-value precedent, applied to the whole schedule.
    """
    if not isinstance(block, dict):
        raise PtetError(f"tax_law.ptet.{state} must be an object, "
                        f"got {type(block).__name__}")
    rows = block.get("schedule")
    if not isinstance(rows, list) or not rows:
        raise PtetError(f"tax_law.ptet.{state}.schedule must be a non-empty list")
    schedule: list = []
    prev_lower = None
    for i, row in enumerate(rows):
        where = f"tax_law.ptet.{state}.schedule[{i}]"
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise PtetError(f"{where} must be [lower, rate, base_tax]")
        lower, rate, base = row
        lower = _strict_non_negative(lower, f"{where}.lower")
        base = _strict_non_negative(base, f"{where}.base_tax")
        # rate: finite fraction in [0, 1]; reuse the non-negative gate then bound.
        rate = _strict_non_negative(rate, f"{where}.rate")
        if rate > 1:
            raise PtetError(f"{where}.rate must be a fraction ≤ 1, got {rate}")
        if i == 0 and lower != 0:
            raise PtetError(f"{where}: the first bracket must start at lower=0")
        if prev_lower is not None and lower <= prev_lower:
            raise PtetError(f"{where}: bracket lowers must strictly increase")
        prev_lower = lower
        schedule.append((lower, rate, base))
    return schedule


def _resolve_schedule(state: str, tax_year: "int | None",
                      ptet_cfg: "dict | None") -> "tuple[list, str | None]":
    """Resolve the PTET schedule for `state`, preferring a fetched
    tax_law.ptet.<state> block over the built-in statutory schedule.

    Returns (schedule, degrade_note). degrade_note is None when a fetched
    schedule was used (or the state has no fallback); it is a LOUD warning
    naming the statutory vintage when the built-in schedule was used because
    nothing was fetched — never a silent price. A present-but-corrupt fetched
    block hard-errors (via _validate_fetched_schedule).
    """
    if isinstance(ptet_cfg, dict):
        # Case-normalize keys so a fetched {"ca": ...} still matches "CA".
        block = None
        for k, v in ptet_cfg.items():
            if isinstance(k, str) and k.strip().upper() == state:
                block = v
                break
        if block is not None:
            return _validate_fetched_schedule(state, block), None
    # Fallback: built-in statutory schedule + loud vintage warning.
    vintage_key = "NJ_LEGACY" if (
        state == "NJ" and tax_year is not None and tax_year < 2022) else state
    vintage = _SCHEDULE_VINTAGE.get(vintage_key, f"{state} built-in schedule")
    note = (f"PTET rate schedule for {state} was not fetched into "
            f"tax_law.ptet.{state} — priced from the built-in statutory schedule "
            f"({vintage}). Rates may have been amended; verify against the state "
            f"authority for TY{tax_year if tax_year is not None else '?'} "
            f"(the NJ BAIT overhaul is the precedent for silent drift).")
    return _schedule_for(state, tax_year), note


def _obs_date(year: int, month: int, day: int, state: str) -> str:
    """A PTET deadline formatted with THAT state's §7503 rollover applied.

    Delegates to `irs_dates.next_business_day_state` rather than re-rolling
    here. The inline weekend-only loop this replaces was a fourth copy of a
    rule that turned out to be wrong for California: R&TC §18410 folds the
    federal legal-holiday set into CA's own, so a CA date can roll past MLK
    Day or observed DC Emancipation Day, which no weekend-only loop
    reaches. NY and NJ keep
    the weekend-only degrade — their statutes have not been read here — and
    naming the state at the call site is what keeps that a stated limitation
    instead of an accident. Renders e.g. 'June 16, 2026'.
    """
    d = next_business_day_state(datetime.date(year, month, day), state)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _election_deadline(state: str, tax_year: int) -> "tuple[str, str]":
    """Return (deadline_date, actionable note) for making a TY{tax_year} election.

    Dates carry the §7503 next-business-day rollover under the STATE's own
    holiday set (via _obs_date), matching tax_calendar.py's state deadlines.
    California's set INCLUDES the federal one (R&TC §18410), so a CA date rolls
    past DC Emancipation Day and MLK Day too; the CA-only §6700 holidays are
    not modeled either, and no date emitted here can reach one (asserted in
    tests/test_irs_dates.py). NY and NJ roll past weekends only and their
    state-specific holidays are not modeled, so a date landing on one may
    shift a further day.
    """
    if state == "CA":
        # The June 15 prepayment (greater of $1,000 or 50% of prior-year PTET) is
        # due DURING the tax year; balance + election are filed by the entity
        # return due date the following March 15. The June 15 prerequisite and the
        # consequence of missing it BOTH vary by year (FTB PTE elective tax guidance):
        #   TY2021 (AB 150 first year): NO June prepayment requirement — elect on
        #     a timely-filed original return (due March 15, 2022).
        #   TY2022-2025 (AB 150): the June 15 prepayment is a hard PREREQUISITE —
        #     miss it and you CANNOT make the election for that year.
        #   TY2026-2030 (SB 132): the election survives a missed/short payment, but
        #     the PTE credit is REDUCED by 12.5% of the unpaid June amount
        #     (R&TC §19904). Calling June 15 an election-preservation deadline
        #     would wrongly imply the election is lost after that date.
        march_next = _obs_date(tax_year + 1, 3, 15, "CA")
        june = _obs_date(tax_year, 6, 15, "CA")
        if tax_year == 2021:
            return (
                _obs_date(2022, 3, 15, "CA"),
                f"CA AB 150 (first year): there was NO June 15 prepayment "
                f"requirement for TY2021 — make the election on a timely-filed "
                f"2021 entity return (originally due "
                f"{_obs_date(2022, 3, 15, 'CA')}).")
        if tax_year >= 2026:
            note = (
                f"CA SB 132: pay the {june} prepayment (greater of "
                f"$1,000 or 50% of prior-year PTET). For TY2026-2030 the TY{tax_year} "
                f"election REMAINS AVAILABLE if you miss it, but your PTE credit is "
                f"reduced by 12.5% of the unpaid June amount; balance + election "
                f"filed by {march_next}.")
        else:  # TY2022-2025
            note = (
                f"CA AB 150: pay the {june} prepayment (greater of "
                f"$1,000 or 50% of prior-year PTET) — REQUIRED to make the "
                f"TY{tax_year} election (the election is lost if missed); balance + "
                f"election filed by {march_next}.")
        return (june, note)
    if state == "NY":
        # NY's election for the tax year is due by March 15 OF the tax year —
        # the earliest binding PTET date across the three states, and the one
        # most likely to be missed (it falls before the year is even underway
        # in practice). Backlog #9 flags this specifically.
        march = _obs_date(tax_year, 3, 15, "NY")
        return (
            march,
            f"NY Tax Law §861: the annual PTET election for TY{tax_year} is due "
            f"by {march} via the Tax Department's PTET Web "
            f"Application — it CANNOT be made on the return after the fact.",
        )
    if state == "NJ":
        # NJ's BAIT election is made on/with the entity return, due the
        # following March 15.
        march_next = _obs_date(tax_year + 1, 3, 15, "NJ")
        return (
            march_next,
            f"NJ BAIT (P.L. 2021 c.419): elect on Form PTE-100 by the entity "
            f"return due date, {march_next}.",
        )
    return ("", "")


def _strict_non_negative(value, field_name: str) -> float:
    """Coerce to a finite, non-negative float; reject bool / NaN / Infinity / <0.

    json.loads accepts NaN/Infinity and they pass isinstance(x, (int, float))
    while silently defeating `< 0` range checks (all comparisons with NaN are
    False), so the finiteness gate is load-bearing, not cosmetic.
    """
    if isinstance(value, bool):
        raise PtetError(f"{field_name} must be a number, got bool")
    if not isinstance(value, (int, float)):
        raise PtetError(f"{field_name} must be a number, got {type(value).__name__}")
    v = float(value)
    if not math.isfinite(v):
        raise PtetError(f"{field_name} must be finite, got {value!r}")
    if v < 0:
        raise PtetError(f"{field_name} must be non-negative, got {v}")
    return v


def _salt_cap(tax_year: int, filing_status: str, cfg: "dict | None" = None) -> float:
    """Federal SALT itemized-deduction cap for the year + filing status.

    OBBBA (2025) raised the cap from $10,000 to $40,000 ($20,000 MFS) for
    TY2025–2029, then it reverts to $10,000 in TY2030. The $40,000 base is
    indexed ~1%/yr (2026–2029) and phases DOWN by 30% of MAGI over $500,000
    ($250,000 MFS) but never below $10,000 — v1 returns the base cap and caveats
    the indexing + MAGI phasedown rather than modeling them (callers can pass an
    explicit --salt-room to override when MAGI is high). Source: IRS Topic 503.

    When `cfg` (the fetched tax_law `federal.salt_cap` block, {"amount": X,
    "mfs_amount": Y}) carries a value for the needed key, it WINS over the
    built-in schedule — the per-year cache is the source of truth for the
    indexed 2026-2029 amounts, which the unindexed schedule above cannot track.
    A None/absent value falls back to the schedule; a present-but-invalid value
    is a hard error (a corrupted cache must not silently price the wrong cap).
    """
    mfs = (filing_status or "").upper() == "MFS"
    if isinstance(cfg, dict):
        key = "mfs_amount" if mfs else "amount"
        val = cfg.get(key)
        if val is not None:
            return _strict_non_negative(val, f"tax_law federal.salt_cap.{key}")
    if 2025 <= tax_year <= 2029:
        return 20000.0 if mfs else 40000.0
    return 5000.0 if mfs else 10000.0


def _salt_cap_label(tax_year: int) -> str:
    return "OBBBA TY2025-2029" if 2025 <= tax_year <= 2029 else "statutory $10K cap"


def _baseline_passthrough(baseline_inputs: dict) -> float:
    """The ordinary pass-through income bucket in the baseline — Schedule 1 line 8
    (Schedule E / K-1 distributive share land here), exposed as `other_income`.

    The PTET deduction is applied to `other_income`, so this is the bucket that
    must be able to absorb it. Checking supplied K-1 income against TOTAL gross
    would let large unrelated wages mask a K-1 that isn't actually in the return,
    and the deduction could then drive pass-through income negative — phantom
    savings on income that was never taxed (Codex P2)."""
    try:
        return max(0.0, round(float(baseline_inputs.get("other_income", 0) or 0), 2))
    except (TypeError, ValueError):
        return 0.0


def _validate_rate(value, field_name: str) -> float:
    """Coerce to a finite fraction in [0, 1]; reject bool / NaN / Infinity / out of range.

    argparse `type=float` happily accepts `-1`, `nan`, `inf`, `1.5` for
    `--marginal-fed`; left unchecked these make the SALT offset negative / NaN /
    absurdly large, so the reported net savings is silently wrong. Fail fast.
    """
    if isinstance(value, bool):
        raise PtetError(f"{field_name} must be a number, got bool")
    if not isinstance(value, (int, float)):
        raise PtetError(f"{field_name} must be a number, got {type(value).__name__}")
    v = float(value)
    if not math.isfinite(v):
        raise PtetError(f"{field_name} must be finite, got {value!r}")
    if not (0.0 <= v <= 1.0):
        raise PtetError(f"{field_name} must be a fraction in [0, 1], got {v}")
    return v


def _normalize_state(code: str) -> str:
    s = (code or "").strip().upper()
    if len(s) != 2 or not s.isalpha():
        raise PtetError(f"State must be a 2-letter USPS code, got {code!r}")
    return s


def ptet_tax(state: str, qualifying_income: float,
             *, rate_override: "float | None" = None,
             tax_year: "int | None" = None,
             ptet_cfg: "dict | None" = None) -> "tuple[float, str]":
    """Compute the entity-level PTET on `qualifying_income`.

    Returns (ptet_paid, basis_note). With `rate_override` the rate is applied
    flat; otherwise the schedule is resolved via `_resolve_schedule` — a fetched
    tax_law.ptet.<state> block (ptet_cfg) is PREFERRED, else the built-in
    statutory schedule for `tax_year` is used. Caller validates the state is in
    `_PTET_STATES`. The unfetched-schedule warning is surfaced through
    `analyze`'s caveats (see _resolve_schedule); this function returns only the
    rate description so its 2-tuple contract is unchanged.
    """
    income = _strict_non_negative(qualifying_income, "qualifying_income")
    if rate_override is not None:
        rate = _strict_non_negative(rate_override, "ptet_rate")
        if rate > 1:
            raise PtetError(f"ptet_rate must be a fraction ≤ 1, got {rate}")
        tax = round(income * rate, 2)
        return tax, f"flat {rate:.3%} (user override)"

    schedule, _ = _resolve_schedule(state, tax_year, ptet_cfg)
    bracket = schedule[0]
    for entry in schedule:
        if income >= entry[0]:
            bracket = entry
        else:
            break
    lower, rate, base_tax = bracket
    tax = round(base_tax + rate * (income - lower), 2)
    if len(schedule) == 1:
        note = f"flat {rate:.3%}"
    else:
        note = f"{rate:.3%} marginal (graduated schedule)"
    return tax, note


@dataclass(frozen=True)
class StatePtetResult:
    state: str
    qualifying_income: float
    ptet_paid: float
    rate_desc: str
    gross_fed_benefit: float      # federal tax saved by the entity-level deduction
    lost_salt_offset: float       # federal benefit forgone (SALT cap headroom)
    net_fed_savings: float        # gross_fed_benefit − lost_salt_offset
    election_deadline: str
    deadline_note: str
    modeled: bool = True
    caveats: "tuple[str, ...]" = field(default_factory=tuple)


@dataclass(frozen=True)
class PtetReport:
    tax_year: int
    filing_status: str
    states: "tuple[StatePtetResult, ...]"
    total_ptet_paid: float
    total_net_fed_savings: float
    salt_room: float
    est_marginal_fed: "float | None"
    notes: "tuple[str, ...]" = field(default_factory=tuple)


_MARGINAL_PROBE = 100.0  # small, to stay local near AGI cliffs (O3)


def _est_marginal_fed(baseline_inputs: dict, rate_kwargs: dict) -> float:
    """Probe the keystone with a SMALL bump of ORDINARY non-wage income to
    estimate the marginal rate at which a forgone SALT itemized deduction dollar
    is valued.

    Probing `other_income` (not `wages`) deliberately: a lost SALT itemized
    deduction only changes taxable income, NOT Medicare wages, so a wage probe
    would fold the 0.9% Additional Medicare Tax into the rate for high earners and
    overstate the offset (understating PTET savings). other_income carries no
    payroll/Medicare component, giving the clean ordinary-bracket rate.

    The probe is a SMALL $100 (O3) rather than $1,000 so it stays LOCAL near an
    AGI cliff (CTC/education-credit/IRA/NIIT phase-outs) — a large probe can jump
    the cliff and overstate the marginal rate (understating PTET savings). It is
    still an estimate; near a cliff, pass an explicit --marginal-fed (caveated in
    the output)."""
    base = compute_scenario(baseline_inputs, {}, **rate_kwargs)["total_federal_tax"]
    bumped = compute_scenario(
        baseline_inputs, {"other_income": _MARGINAL_PROBE}, **rate_kwargs
    )["total_federal_tax"]
    return max(0.0, round((bumped - base) / _MARGINAL_PROBE, 4))


def analyze(
    *,
    baseline_inputs: dict,
    rate_kwargs: dict,
    state_incomes: dict,
    tax_year: int,
    filing_status: str,
    salt_room: float = 0.0,
    other_salt: "float | None" = None,
    marginal_fed: "float | None" = None,
    rate_overrides: "dict | None" = None,
    qbi_eligible_states: "set | None" = None,
    itemizes: bool = True,
    passthrough_income: "float | None" = None,
    salt_cap_cfg: "dict | None" = None,
    ptet_cfg: "dict | None" = None,
) -> PtetReport:
    """Estimate the federal PTET benefit per K-1 entity state.

    Federal benefit is ANCHORED: the entity-level deduction is a negative
    `other_income` delta through `compute_scenario`, so only the marginal effect
    around the real return is counted. The per-state benefit is the total
    anchored benefit allocated pro-rata to each state's modeled income reduction
    (a marginal-stacking approximation; the aggregate is exact).

    QBI clawback: for §199A-eligible pass-through income (states in
    `qbi_eligible_states`), the entity-level PTET deduction reduces the business's
    QBI, so the owner loses ~`qbi_rate × PTET` of QBI deduction. We can't recompute
    QBI through the keystone (the K-1 income flows as `other_income`, not
    `self_employment_net`), so we price the clawback directly by shrinking the
    modeled income reduction to `PTET × (1 − qbi_rate)` — the lost QBI deduction
    add-back is taxed at the same marginal rate as the income reduction, so this
    stays anchored. Simplified: ignores the §199A taxable-income and W-2-wage
    limits (caveated). Default: not QBI-eligible.
    """
    rate_overrides = {_normalize_state(k): v for k, v in (rate_overrides or {}).items()}
    qbi_eligible_states = {_normalize_state(s) for s in (qbi_eligible_states or set())}
    income_states = {_normalize_state(s) for s in state_incomes}
    # A rate override for a state with no --income can't be applied — fail fast
    # rather than silently dropping the user's intended (often multi-owner) rate.
    orphan_overrides = set(rate_overrides) - income_states
    if orphan_overrides:
        raise PtetError(
            f"--ptet-rate given for {sorted(orphan_overrides)} but no matching "
            f"--income; add the income or remove the override.")
    # SALT cap headroom. If the taxpayer's other state/local taxes (other_salt)
    # are supplied, derive the unused headroom from the year/status cap; else use
    # salt_room directly (default 0 = assume the cap is exhausted). The fetched
    # tax_law federal.salt_cap block (salt_cap_cfg) wins over the built-in
    # statutory schedule when populated.
    salt_cap = _salt_cap(tax_year, filing_status, salt_cap_cfg)
    if other_salt is not None:
        other_salt = _strict_non_negative(other_salt, "other_salt")
        salt_room = max(0.0, round(salt_cap - other_salt, 2))
    else:
        salt_room = _strict_non_negative(salt_room, "salt_room")
    if marginal_fed is not None:
        marginal_fed = _validate_rate(marginal_fed, "marginal_fed")
    qbi_rate = float(rate_kwargs.get("qbi_rate", 0.20) or 0.0)

    # Build per-state PTET-paid figures + the modeled income reduction (net of the
    # QBI clawback for §199A-eligible states).
    modeled_states: list[dict] = []
    unmodeled: list[StatePtetResult] = []
    for raw_state, raw_income in state_incomes.items():
        state = _normalize_state(raw_state)
        income = _strict_non_negative(raw_income, f"income[{state}]")
        deadline, deadline_note = _election_deadline(state, tax_year)
        if state not in _PTET_STATES:
            unmodeled.append(StatePtetResult(
                state=state, qualifying_income=income, ptet_paid=0.0,
                rate_desc="not modeled", gross_fed_benefit=0.0,
                lost_salt_offset=0.0, net_fed_savings=0.0,
                election_deadline="", deadline_note="",
                modeled=False,
                caveats=(f"{state} has no PTET regime modeled in v1 (CA/NY/NJ "
                         f"only). Check whether {state} offers an elective PTET.",),
            ))
            continue
        if not _window_active(state, tax_year):
            # The regime didn't exist / has sunset for this tax year — don't price
            # an expired or not-yet-created elective tax (mirrors tax_calendar's
            # out-of-window note). Bias direction: confidently modeling here would
            # OVERSTATE a benefit that legally isn't available.
            first, last = _PTET_WINDOWS[state]
            span = f"TY{first}-{last}" if last is not None else f"TY{first}+"
            unmodeled.append(StatePtetResult(
                state=state, qualifying_income=income, ptet_paid=0.0,
                rate_desc="not modeled (out of regime window)", gross_fed_benefit=0.0,
                lost_salt_offset=0.0, net_fed_savings=0.0,
                election_deadline="", deadline_note="",
                modeled=False,
                caveats=(f"{state}'s elective PTET regime applies to {span}; "
                         f"TY{tax_year} is outside that window (not yet enacted or "
                         f"sunset). Verify the current statute before electing.",),
            ))
            continue
        override = rate_overrides.get(state)
        ptet_paid, rate_desc = ptet_tax(state, income, rate_override=override,
                                        tax_year=tax_year, ptet_cfg=ptet_cfg)
        # Capture the unfetched-schedule LOUD warning for the caveats (only when
        # a schedule was actually used — a rate override bypasses the schedule).
        schedule_note = None
        if override is None:
            _, schedule_note = _resolve_schedule(state, tax_year, ptet_cfg)
        qbi_eligible = state in qbi_eligible_states
        income_reduction = round(ptet_paid * (1 - qbi_rate), 2) if qbi_eligible else ptet_paid
        modeled_states.append(dict(
            state=state, income=income, ptet_paid=ptet_paid, rate_desc=rate_desc,
            deadline=deadline, deadline_note=deadline_note,
            qbi_eligible=qbi_eligible, income_reduction=income_reduction,
            schedule_note=schedule_note))

    total_ptet = round(sum(m["ptet_paid"] for m in modeled_states), 2)
    total_reduction = round(sum(m["income_reduction"] for m in modeled_states), 2)

    # Anchoring sanity check: the PTET deduction reduces pass-through income that
    # must already be in result.json. Compare supplied K-1 income against the
    # K-1 bucket, NOT total gross — large unrelated income must not mask a K-1
    # that isn't in the return. The caller (`_main`) supplies `passthrough_income`
    # from the DEDICATED `schedule_e_k1_income` field (L2 — the Schedule E line-5
    # / K-1 distributive share ISOLATED from the rest of line 8), falling back to
    # the aggregate `line_8_schedule1_other` for a legacy result.json and to the
    # composite `other_income` (`_baseline_passthrough`) for direct callers. If
    # qualifying exceeds the bucket the deduction would drive pass-through income
    # negative and "save" tax on income that was never taxed. Fail fast.
    total_qualifying = round(sum(m["income"] for m in modeled_states), 2)
    baseline_passthrough = (round(max(0.0, passthrough_income), 2)
                            if passthrough_income is not None
                            else _baseline_passthrough(baseline_inputs))
    if total_qualifying > baseline_passthrough + 1.0:
        raise PtetError(
            f"Supplied K-1 income ${total_qualifying:,.0f} exceeds the K-1 / "
            f"Schedule E pass-through income in result.json "
            f"(${baseline_passthrough:,.0f}) — the return may not include this "
            f"K-1. Recompute result.json with the K-1 income (/tax-compute) "
            f"before estimating PTET.")

    # Anchored federal benefit of deducting the PTET at the entity level (net of
    # any QBI clawback already baked into total_reduction).
    gross_total = 0.0
    if total_reduction > 0:
        base_tax = compute_scenario(baseline_inputs, {}, **rate_kwargs)["total_federal_tax"]
        reduced_tax = compute_scenario(
            baseline_inputs, {"other_income": -total_reduction}, **rate_kwargs
        )["total_federal_tax"]
        gross_total = round(base_tax - reduced_tax, 2)

    # The forgone personal SALT ITEMIZED deduction: only the portion of state tax
    # that would have fit under the remaining cap yields no federal benefit from
    # PTET (it was deductible anyway). This offset applies ONLY if the taxpayer
    # itemizes — a standard-deduction filer gets NO SALT deduction regardless of
    # the cap, so there is nothing to net out and the full PTET is incremental
    # (Codex P2: applying the offset to a non-itemizer understates the benefit).
    est_marg = marginal_fed
    marginal_auto_estimated = False
    if itemizes and salt_room > 0 and est_marg is None:
        est_marg = _est_marginal_fed(baseline_inputs, rate_kwargs)
        marginal_auto_estimated = True
    salt_credited = min(salt_room, total_ptet)
    lost_salt_total = (round((est_marg or 0.0) * salt_credited, 2)
                       if (itemizes and salt_room > 0) else 0.0)

    results: list[StatePtetResult] = []
    for m in modeled_states:
        state, income, ptet_paid = m["state"], m["income"], m["ptet_paid"]
        # Gross benefit follows the modeled income reduction (QBI-clawed where
        # applicable); the SALT offset follows the PTET paid (cap is independent
        # of QBI). Both reconcile to their aggregates.
        gross_share = (m["income_reduction"] / total_reduction) if total_reduction > 0 else 0.0
        offset_share = (ptet_paid / total_ptet) if total_ptet > 0 else 0.0
        gross = round(gross_total * gross_share, 2)
        offset = round(lost_salt_total * offset_share, 2)
        caveats = []
        if income == 0:
            caveats.append(
                "No qualifying income supplied for this state — pass "
                f"--income {state}=<amount> to estimate the PTET savings.")
        if m["qbi_eligible"]:
            caveats.append(
                f"§199A QBI clawback modeled: PTET shrinks your QBI base, so the "
                f"benefit is net of the lost {qbi_rate:.0%} QBI deduction "
                f"(simplified — ignores the §199A taxable-income / W-2-wage limits).")
        elif income > 0:
            caveats.append(
                "If this K-1 income is §199A QBI-eligible, the PTET reduces your "
                "QBI deduction — actual savings is LOWER; pass "
                f"--qbi-eligible {state} to model the clawback.")
        if state in ("NY", "NJ") and income > 0 and state not in rate_overrides:
            caveats.append(
                "Graduated rate applied to YOUR share; multi-owner entities are "
                "taxed on the entity-level pool — pass --ptet-rate to override.")
        if m.get("schedule_note"):
            caveats.append(m["schedule_note"])
        results.append(StatePtetResult(
            state=state, qualifying_income=income, ptet_paid=ptet_paid,
            rate_desc=m["rate_desc"], gross_fed_benefit=gross, lost_salt_offset=offset,
            net_fed_savings=round(gross - offset, 2),
            election_deadline=m["deadline"], deadline_note=m["deadline_note"],
            caveats=tuple(caveats),
        ))
    results.extend(unmodeled)

    notes = [
        "Federal benefit is anchored to result.json line 24 (entity PTET deduction "
        "modeled as a reduction in pass-through ordinary income). The estimate "
        "assumes this K-1 income is already included in result.json — run "
        "/tax-compute with the K-1 first if not.",
        "State savings is treated as $0: the owner's PTET credit generally offsets "
        "the entity tax (CA credit nonrefundable, 5-yr carryforward; NJ/NY "
        "refundable). The benefit of PTET is the FEDERAL deduction.",
    ]
    if not itemizes:
        notes.append(
            "You take the STANDARD deduction — your state/local taxes yield no "
            "federal itemized deduction at all, so there is nothing for PTET to "
            "displace: the full PTET is incremental federal benefit (no SALT offset "
            "applied, regardless of the cap).")
    elif salt_room == 0:
        notes.append(
            f"ASSUMPTION: you itemize and your ${salt_cap:,.0f} SALT cap "
            f"({_salt_cap_label(tax_year)}) is already fully consumed by other "
            f"state/local taxes (property tax, W-2 state withholding) — so the full "
            f"PTET is incremental federal deduction. With the larger OBBBA cap this "
            f"is LESS likely to hold; pass --other-salt <your property + state income "
            f"tax> to derive the unused headroom, or --salt-room directly. (If you "
            f"take the standard deduction, pass --standard-deduction.)")
    else:
        notes.append(
            f"Modeled ${salt_room:,.0f} of unused SALT cap headroom (cap "
            f"${salt_cap:,.0f}, {_salt_cap_label(tax_year)}) at a "
            f"{(est_marg or 0):.1%} marginal rate — that portion of state tax was "
            f"federally deductible anyway (you itemize), so it is netted out of the "
            f"benefit.")
        if marginal_auto_estimated:
            notes.append(
                "The SALT-offset marginal rate was AUTO-ESTIMATED from a small "
                "$100 income probe. Near an AGI cliff (CTC / education-credit / "
                "IRA / NIIT phase-outs) the true marginal rate can differ — pass "
                "an explicit --marginal-fed for a precise offset.")
    if itemizes and 2025 <= tax_year <= 2029:
        notes.append(
            f"The OBBBA ${salt_cap:,.0f} cap is indexed ~1%/yr (2026-2029) and "
            f"phases DOWN by 30% of MAGI over $500,000 ($250,000 MFS) toward a "
            f"$10,000 floor — not modeled here. If your MAGI is high your true cap "
            f"may be lower (more headroom consumed); pass --salt-room to override.")

    return PtetReport(
        tax_year=tax_year, filing_status=filing_status, states=tuple(results),
        total_ptet_paid=total_ptet,
        total_net_fed_savings=round(sum(r.net_fed_savings for r in results), 2),
        salt_room=salt_room, est_marginal_fed=est_marg, notes=tuple(notes),
    )


def render_markdown(report: PtetReport) -> str:
    lines = [
        f"# PTET Election Analysis — TY{report.tax_year} ({report.filing_status})",
        "",
        "Pass-through entity tax: a SALT-cap workaround that moves your state tax "
        "on K-1 income to the entity level, where it dodges the $10,000 federal "
        "itemized-deduction cap.",
        "",
    ]
    modeled = [s for s in report.states if s.modeled]
    if not modeled:
        lines.append("_No modeled PTET states (CA/NY/NJ) supplied._")
    for s in modeled:
        lines += [
            f"## {s.state} — election due {s.election_deadline}",
            "",
            f"- Qualifying K-1 income: {_money(s.qualifying_income)}",
            f"- PTET paid (entity-level, {s.rate_desc}): {_money(s.ptet_paid)}",
            f"- Gross federal benefit: {_money(s.gross_fed_benefit)}",
        ]
        if s.lost_salt_offset:
            lines.append(f"- Less forgone SALT itemized deduction: "
                         f"({_money(s.lost_salt_offset)})")
        lines += [
            f"- **Net federal savings: {_money(s.net_fed_savings)}**",
            f"- ⏰ {s.deadline_note}",
        ]
        for c in s.caveats:
            lines.append(f"  - ⚠ {c}")
        lines.append("")

    unmodeled = [s for s in report.states if not s.modeled]
    for s in unmodeled:
        lines.append(f"## {s.state} — not modeled")
        for c in s.caveats:
            lines.append(f"- ⚠ {c}")
        lines.append("")

    lines += [
        "## Summary",
        "",
        f"- Total PTET paid: {_money(report.total_ptet_paid)}",
        f"- **Total net federal savings: {_money(report.total_net_fed_savings)}**",
        "",
        "### Notes",
    ]
    lines += [f"- {n}" for n in report.notes]
    lines.append("")
    lines.append("_Federal-only estimate. Confirm rates, eligibility, and election "
                 "mechanics with the entity's state authority before electing — the "
                 "election is generally annual and irrevocable._")
    return "\n".join(lines)


def _parse_state_amounts(tokens: "list[str]", *, what: str,
                         combine: str = "sum") -> dict:
    """Parse repeated STATE=VALUE CLI tokens into {STATE: float}.

    combine="sum": repeated states accumulate (e.g. two K-1 entities in the same
        state — `--income CA=100000 --income CA=50000` → CA=150000). This is the
        right behavior for income; a later token silently overwriting the earlier
        one would underreport the PTET base.
    combine="reject": a repeated state is an error. Right for rates, where summing
        is nonsensical and "last wins" would silently pick one ambiguous value.
    """
    out: dict = {}
    for tok in tokens or []:
        if "=" not in tok:
            raise PtetError(f"--{what} expects STATE=VALUE, got {tok!r}")
        key, _, val = tok.partition("=")
        # Validate the state-code SHAPE at the parse boundary so a typo like
        # `California=...` fails fast instead of silently never matching a
        # normalized income key downstream (the --ptet-rate escape hatch would
        # otherwise no-op and quietly price the state at the statutory rate).
        key = _normalize_state(key)
        try:
            num = float(val)
        except ValueError:
            raise PtetError(f"--{what} value for {key} is not a number: {val!r}")
        if key in out:
            if combine == "reject":
                raise PtetError(
                    f"--{what} given more than once for {key}; supply a single value.")
            num += out[key]
        out[key] = num
    return out


def _main(argv: list) -> int:
    p = argparse.ArgumentParser(
        prog="tax-ptet",
        description="Estimate the federal SALT-cap-workaround savings of a "
                    "CA/NY/NJ pass-through-entity tax election.")
    add_workspace_argument(p)
    p.add_argument("--year", default=None,
                   help="Tax year (defaults to result.json meta.tax_year)")
    p.add_argument("--income", action="append", default=[], metavar="STATE=AMOUNT",
                   help="Qualifying K-1 income per entity state (repeatable)")
    p.add_argument("--ptet-rate", action="append", default=[], metavar="STATE=RATE",
                   help="Override the PTET rate for a state (fraction, e.g. 0.093)")
    p.add_argument("--salt-room", type=float, default=None,
                   help="Unused SALT cap headroom directly (default: assume cap "
                        "exhausted unless --other-salt is given)")
    p.add_argument("--other-salt", type=float, default=None,
                   help="Your other state/local taxes already counting against the "
                        "SALT cap (property + state income/wage tax) — headroom is "
                        "derived as cap − this. --salt-room takes precedence.")
    p.add_argument("--marginal-fed", type=float, default=None,
                   help="Federal marginal rate for the forgone-SALT offset "
                        "(auto-estimated from result.json if omitted)")
    p.add_argument("--qbi-eligible", action="append", default=[], metavar="STATE",
                   help="Mark a state's K-1 income as §199A QBI-eligible so the "
                        "PTET's QBI clawback is priced (repeatable)")
    p.add_argument("--standard-deduction", action="store_true",
                   help="You take the standard deduction — suppresses the "
                        "forgone-SALT offset (no SALT itemized deduction to displace)")
    args = p.parse_args(argv)

    try:
        workspace = resolve_workspace(args.workspace).resolve()
        result_path = resolve_under_workspace("result.json", workspace)
        result = _read_json(result_path)
        year = args.year
        if year is None:
            year = (result.get("meta", {}) or {}).get("tax_year")
            if year is None:
                raise PtetError("result.json has no meta.tax_year — pass --year.")
        year = _validate_year_arg(str(year))
        read_tax_year(result, year)

        profile_path = resolve_under_workspace("taxpayer_profile.json", workspace)
        profile = _read_json(profile_path)
        law_path = resolve_under_workspace(f"tax_law_{year}.json", workspace)
        tax_law = _read_json(law_path)

        filing_status = (result.get("meta", {}) or {}).get("filing_status") \
            or profile.get("filing_status") or "Single"
        baseline_inputs = extract_baseline_inputs(result)
        rate_kwargs = _rate_kwargs(profile, tax_law, filing_status)
        # Phantom-income guard bucket (L2): PREFER the dedicated
        # `schedule_e_k1_income` field (the gross positive qualifying K-1
        # distributive income) — the aggregate line 8 also folds in
        # unemployment, taxable refunds, Schedule C, gambling, etc., any of which
        # could mask a K-1 that isn't in the return. Fall back to the aggregate
        # `line_8_schedule1_other` only for a legacy result.json written before
        # the dedicated field existed. The dedicated field, when PRESENT, must be
        # a finite non-negative number — a corrupt `Infinity`/NaN (json.loads
        # accepts them) or a non-numeric value FAILS CLOSED rather than passing
        # every claim or silently degrading to the looser line-8 fallback.
        form_1040 = result.get("form_1040", {}) or {}

        def _guard_bucket(raw, field_name):
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise PtetError(
                    f"result.json form_1040.{field_name} must be a number, "
                    f"got {raw!r}")
            if not math.isfinite(float(raw)):
                raise PtetError(
                    f"result.json form_1040.{field_name} must be finite, "
                    f"got {raw!r}")
            return round(max(0.0, float(raw)), 2)

        # KEY PRESENCE (not value) picks the branch: the dedicated field ships in
        # the current template (defaulting 0), so its KEY is present on every
        # current-schema result. A present key with a null/corrupt value is a
        # malformed CURRENT result and must FAIL CLOSED via _guard_bucket rather
        # than silently degrading to the looser line-8 aggregate — only a truly
        # ABSENT key (a legacy result.json predating the field) takes the fallback.
        passthrough_income = None
        if "schedule_e_k1_income" in form_1040:
            passthrough_income = _guard_bucket(
                form_1040["schedule_e_k1_income"], "schedule_e_k1_income")
        elif form_1040.get("line_8_schedule1_other") is not None:
            passthrough_income = _guard_bucket(
                form_1040["line_8_schedule1_other"], "line_8_schedule1_other")

        state_incomes = _parse_state_amounts(args.income, what="income", combine="sum")
        rate_overrides = _parse_state_amounts(args.ptet_rate, what="ptet-rate",
                                              combine="reject")
        qbi_eligible = {s.strip().upper() for s in (args.qbi_eligible or []) if s.strip()}

        flags = profile.get("flags", {}) or {}
        # When no income is supplied, fall back to flags.k1_states as a
        # deadline-reminder + detection mode — but ONLY when has_k1_income is
        # truthy. has_k1_income is the canonical guard for "does PTET apply"
        # (same as /tax-calendar); a stale k1_states left on a profile whose
        # pass-through income is gone must NOT resurrect PTET advice.
        if not state_incomes and flags.get("has_k1_income"):
            k1_states = flags.get("k1_states") or []
            if isinstance(k1_states, list):
                state_incomes = {str(s).strip().upper(): 0.0
                                 for s in k1_states if isinstance(s, str) and s.strip()}

        # --salt-room (explicit headroom) takes precedence over --other-salt
        # (derive headroom from the cap). Default: neither → assume exhausted.
        salt_room = args.salt_room if args.salt_room is not None else 0.0
        other_salt = args.other_salt if args.salt_room is None else None
        report = analyze(
            baseline_inputs=baseline_inputs, rate_kwargs=rate_kwargs,
            state_incomes=state_incomes, tax_year=year, filing_status=filing_status,
            salt_room=salt_room, other_salt=other_salt, marginal_fed=args.marginal_fed,
            rate_overrides=rate_overrides, qbi_eligible_states=qbi_eligible,
            itemizes=not args.standard_deduction,
            passthrough_income=passthrough_income,
            salt_cap_cfg=(tax_law.get("federal", {}) or {}).get("salt_cap"),
            ptet_cfg=(tax_law.get("ptet") if isinstance(tax_law, dict) else None))
        print(render_markdown(report))
        return 0
    except (WorkspaceError, PtetError, ProjectionError, ScenarioError, RolloverError, ValueError) as e:
        # ProjectionError belongs here because `_read_json` is projection's,
        # and it is what a missing result.json raises — the single most
        # ordinary way to run this skill wrong (before /tax-compute has been
        # run at all). Without it that case exited with a Python traceback
        # rather than the `Error: …` line the skill tells the agent to read.
        print(f"Error: {redact(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
