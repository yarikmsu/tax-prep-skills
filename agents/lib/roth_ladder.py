#!/usr/bin/env python3
"""Roth conversion ladder modeler for /tax-roth-convert (backlog #6).

Sweeps Roth conversion amounts $0 .. --max in --step increments, recomputing the
FEDERAL tax effect of each rung via the compute_scenario keystone (a conversion is
taxable ordinary income, modeled as an extra IRA distribution), and reports the
marginal cost per dollar plus a flagged list of exactly which rung trips each
planning threshold: LTCG 0%/15%/20% bracket bumping, NIIT onset, and IRMAA tiers.

The absolute tax at each rung is ANCHORED to the real result.json line 24 (same
contract as /tax-scenario) — only the marginal effect of the conversion is modeled.
NIIT is layered on OUTSIDE the keystone (which omits it); IRMAA is a flagged side
cost, not income tax, so it is never folded into the tax total.

Never mutates result.json or taxpayer_profile.json. Writes ONLY one
scenarios/<name>.json via the reused scenario.save_scenario guard. Pure compute +
markdown renderer + guarded writer + CLI.
"""

import datetime
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.projection import (  # noqa: E402
    ProjectionError,
    _binary_status_key,
    _full_status_key,
    _money,
    _num,
    _read_json,
)
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_year_arg,
)
from agents.lib.scenario import (  # noqa: E402
    ScenarioError,
    _rate_kwargs,
    _validate_name,
    extract_baseline_inputs,
    medical_floor_caveat,
    reprice_outcomes,
    schedule_a_caveat,
    medical_floor_may_move,
    read_tax_year,
    refuse_where_the_charity_cap_moves,
    salt_cap_may_move,
    salt_phasedown_caveat,
    save_scenario,
)
from agents.lib.tax_computations import (  # noqa: E402
    _coerce_numeric,
    compute_form_8606,
    compute_niit,
    compute_scenario,
)
from agents.lib.secure_fs import redact  # noqa: E402
from agents.lib.tax_computations.state.planning import price_state_points  # noqa: E402
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    resolve_under_workspace as _resolve_under,
    resolve_workspace,
)

SCHEMA = "tax-roth-ladder/v1"
MAX_RUNGS = 200  # bound output / compute for pathological --max/--step combinations


class RothLadderError(Exception):
    """Raised when /tax-roth-convert cannot safely proceed."""


@dataclass(frozen=True)
class LadderRung:
    amount: float           # gross conversion modeled at this rung
    taxable_conversion: float  # taxable portion after Form 8606 pro-rata (== amount when no basis)
    agi: float              # scenario AGI (== MAGI proxy)
    taxable_income: float
    fed_tax_anchored: float  # line 24 + keystone marginal effect (ex-NIIT)
    niit_addon: float        # incremental NIIT vs the $0 rung
    total_tax: float         # fed_tax_anchored + niit_addon
    cumulative_cost: float   # total_tax - baseline (== line 24)
    marginal_rate: "float | None"  # Δtotal / step vs the previous rung; None at $0
    flags: "list[str]"       # threshold labels first crossed at this rung
    state_local_tax: "float | None" = None
    state_local_cost: "float | None" = None
    combined_cost: "float | None" = None
    combined_marginal_rate: "float | None" = None


@dataclass(frozen=True)
class ThresholdCrossing:
    kind: 'Literal["ltcg_0_15", "ltcg_15_20", "niit", "irmaa"]'
    label: str
    at_amount: float         # conversion amount at which it first trips
    magi: float
    est_annual_cost: "float | None"  # incremental annual $ (NIIT/IRMAA); None for LTCG


@dataclass(frozen=True)
class LadderReport:
    schema: str
    name: str
    tax_year: int
    created: str
    filing_status: "str | None"
    max_amount: float
    step: float
    anchor: dict             # {"line_24", "baseline_recompute_total"}
    basis_modeled: bool      # True → Form 8606 pro-rata diluted each rung's taxable conversion
    ira_basis: "float | None"  # after-tax basis used; None when not modeled
    ira_value: "float | None"  # total beginning-of-year traditional IRA balance B; None when not modeled
    baseline_nii: float
    niit_threshold: "float | None"
    niit_modeled: bool       # False → no NIIT threshold in tax_law; surtax EXCLUDED
    irmaa_modeled: bool      # False → no IRMAA tiers in tax_law; tiers NOT checked
    rungs: "list[LadderRung]"
    crossings: "list[ThresholdCrossing]"
    salt_cap_may_move: bool = True  # gates salt_phasedown_caveat
    medical_floor_may_move: bool = True  # gates medical_floor_caveat
    # AA23: Schedule A re-priced at each rung's AGI, and what that still held.
    schedule_a_repriced: bool = False
    schedule_a_held: "list[str]" = field(default_factory=list)
    state_effect: dict = field(default_factory=dict)
    ira_owner: "str | None" = None


def parse_sweep(max_amount, step) -> "list[float]":
    """Validate --max/--step and return the rung amounts [0, step, 2*step, ..., max].

    A final partial rung at exactly --max is appended if max is not a step multiple.
    """
    try:
        mx = _coerce_numeric(max_amount, default=None)
        st = _coerce_numeric(step, default=None)
    except TypeError:
        raise RothLadderError("--max and --step must be numeric, got bool.")
    if mx is None or st is None:
        raise RothLadderError(f"--max ({max_amount!r}) and --step ({step!r}) must be numeric.")
    if st <= 0:
        raise RothLadderError(f"--step must be positive, got {st}.")
    if mx < 0:
        raise RothLadderError(f"--max must be non-negative, got {mx}.")
    if mx == 0:
        return [0.0]
    if st > mx:
        raise RothLadderError(f"--step ({st}) cannot exceed --max ({mx}).")
    n = int(mx // st)
    if n + 1 > MAX_RUNGS:
        raise RothLadderError(
            f"--max/--step would produce {n + 1} rungs (limit {MAX_RUNGS}). "
            "Use a larger --step or smaller --max."
        )
    rungs = [round(i * st, 2) for i in range(n + 1)]
    if rungs[-1] < mx:
        rungs.append(round(mx, 2))
    return rungs


def _net_investment_income(inputs: dict) -> float:
    """Baseline NII for NIIT: interest + ordinary dividends + net positive cap gains.

    A Roth conversion / IRA distribution is NOT investment income and is excluded.
    Constant across the sweep, so NIIT rises only via MAGI clearing the threshold.
    """
    interest = _num(inputs.get("taxable_interest"), "taxable_interest")
    dividends = _num(inputs.get("ordinary_dividends"), "ordinary_dividends")
    cap_gain = max(0.0, _num(inputs.get("capital_gains"), "capital_gains"))
    return round(interest + dividends + cap_gain, 2)


def _niit_params(tax_law: dict, filing_status: "str | None") -> "tuple[float | None, float]":
    """(threshold, rate) for NIIT from tax_law; threshold None → NIIT not modeled."""
    niit = (tax_law.get("federal", {}) or {}).get("niit", {}) or {}
    tmap = niit.get("threshold") or {}
    threshold = tmap.get(_full_status_key(filing_status)) or tmap.get(_binary_status_key(filing_status))
    rate = niit.get("rate", 0.038)
    return threshold, rate


def _irmaa_tiers(tax_law: dict, filing_status: "str | None") -> "list[dict]":
    """Sorted IRMAA tiers for the (binary) filing status; [] when not fetched.

    An EMPTY tier list is a legitimate "not applicable / not fetched" state (the
    schema ships empty) and degrades to "IRMAA not modeled". But a PRESENT-yet-malformed
    tier (non-object, or non-numeric threshold/surcharge) is a data error and raises —
    silently dropping it would make a real Medicare-premium crossing vanish or render a
    misleading $0 surcharge. Mirrors the hard-error-on-bad-data philosophy of scenario.py.

    IRMAA brackets split single vs. married-filing-jointly; MFS has a special rule
    not modeled in v1 (collapses to the single schedule — flagged in caveats).
    """
    irmaa = (tax_law.get("federal", {}) or {}).get("irmaa", {}) or {}
    tiers = (irmaa.get("tiers") or {}).get(_binary_status_key(filing_status)) or []
    cleaned = []
    for i, t in enumerate(tiers):
        if not isinstance(t, dict):
            raise RothLadderError(f"IRMAA tier #{i} in tax_law is not an object: {t!r}")
        thr = t.get("magi_threshold")
        if not isinstance(thr, (int, float)) or isinstance(thr, bool):
            raise RothLadderError(
                f"IRMAA tier #{i} has a non-numeric magi_threshold: {thr!r}")
        sur = t.get("annual_surcharge_per_person")
        if not isinstance(sur, (int, float)) or isinstance(sur, bool):
            raise RothLadderError(
                f"IRMAA tier #{i} has a non-numeric annual_surcharge_per_person: {sur!r}")
        cleaned.append({"magi_threshold": float(thr), "annual_surcharge_per_person": float(sur)})
    return sorted(cleaned, key=lambda t: t["magi_threshold"])


def _irmaa_surcharge(magi: float, tiers: "list[dict]") -> float:
    """Per-beneficiary annual surcharge for the highest tier whose threshold <= magi."""
    surcharge = 0.0
    for t in tiers:
        if magi >= t["magi_threshold"]:
            surcharge = t["annual_surcharge_per_person"]
    return surcharge


def _resolve_basis(profile: dict, ira_basis, ira_value, ira_owner=None) -> "tuple[bool, float | None, float | None]":
    """Resolve (basis_modeled, basis, value) for the pro-rata sweep.

    `ira_basis` defaults to the selected individual opening profile balance when not passed
    explicitly. Pro-rata is modeled only when a positive total IRA value B is known
    (the ratio denominator); without it the ladder degrades to full-conversion-taxable.
    A negative basis/value is rejected as a data error. basis > value is NOT rejected:
    after investment losses a taxpayer can legitimately have Form 8606 basis exceeding the
    current IRA value, and the keystone caps the line-10 ratio at 1.0 (the conversion is
    then fully nontaxable up to the balance, with leftover basis carried forward).
    """
    from agents.lib.tax_computations.federal.ira_basis import opening_basis
    if ira_owner is not None and ira_owner not in {"primary", "spouse"}:
        raise RothLadderError("--ira-owner must be primary/spouse")
    if ira_owner == "spouse" and profile.get("filing_status") != "MFJ":
        raise RothLadderError("A spouse's IRA belongs on their own separate return")
    if ira_value is not None and profile.get("filing_status") == "MFJ" and ira_owner is None:
        raise RothLadderError("Joint IRA pro-rata planning requires --ira-owner; never pool spouses' balances")
    try:
        individual_basis = opening_basis(profile)
    except ValueError as exc:
        raise RothLadderError(str(exc)) from exc
    if ira_basis is None:
        ira_basis = individual_basis[ira_owner or "primary"]
    try:
        basis = _coerce_numeric(ira_basis, default=None) if ira_basis is not None else None
        value = _coerce_numeric(ira_value, default=None) if ira_value is not None else None
    except TypeError:  # bool is never a valid amount (mirrors the keystones)
        raise RothLadderError("--ira-basis/--ira-value must be numeric, got bool.")
    if ira_basis is not None and basis is None:
        raise RothLadderError(f"--ira-basis is not numeric: {ira_basis!r}")
    if ira_value is not None and value is None:
        raise RothLadderError(f"--ira-value is not numeric: {ira_value!r}")
    if basis is not None and basis < 0:
        raise RothLadderError(f"--ira-basis cannot be negative (got {basis}).")
    if value is not None and value < 0:
        raise RothLadderError(f"--ira-value cannot be negative (got {value}).")
    if value is None or value <= 0:
        return False, None, None  # no denominator → degrade to full-taxable v1
    basis = basis or 0.0
    # basis > value is allowed (post-loss IRA): the keystone caps the ratio at 1.0, so the
    # conversion is fully nontaxable up to the balance and leftover basis carries forward.
    return True, round(basis, 2), round(value, 2)


def build_ladder(result: dict, profile: dict, tax_law: dict, year: int, *,
                 max_amount: float, step: float, name: str,
                 today: datetime.date,
                 ira_basis=None, ira_value=None, ira_owner=None) -> LadderReport:
    filing_status = ((result.get("meta", {}) or {}).get("filing_status")
                     or profile.get("filing_status"))
    rate_kwargs = _rate_kwargs(profile, tax_law, filing_status, result)
    base_inputs = extract_baseline_inputs(result)
    if base_inputs.get("social_security") is not None:
        read_tax_year(result, year)
    basis_modeled, basis, value = _resolve_basis(profile, ira_basis, ira_value, ira_owner)

    line24 = round(_num(((result.get("form_1040", {}) or {})
                         .get("line_24_total_tax")), "line_24"), 2)
    base_out = compute_scenario(base_inputs, {}, **rate_kwargs)
    baseline_recompute = base_out["total_federal_tax"]
    anchor = {"line_24": line24, "baseline_recompute_total": baseline_recompute}

    nii = _net_investment_income(base_inputs)
    niit_threshold, niit_rate = _niit_params(tax_law, filing_status)
    irmaa_tiers = _irmaa_tiers(tax_law, filing_status)
    cg_zero = rate_kwargs.get("cg_zero_limit")
    cg_fifteen = rate_kwargs.get("cg_fifteen_limit")
    preferential = (max(0.0, base_inputs.get("capital_gains", 0.0))
                    + min(max(0.0, base_inputs.get("qualified_dividends", 0.0)),
                          max(0.0, base_inputs.get("ordinary_dividends", 0.0))))

    base_niit = compute_niit(base_out["agi"], nii, niit_threshold, rate=niit_rate)
    base_irmaa_surcharge = _irmaa_surcharge(base_out["agi"], irmaa_tiers)
    # IRMAA tiers already in effect at $0 are not "crossings"; only flag NEW tiers.
    base_irmaa_idx = sum(1 for t in irmaa_tiers if base_out["agi"] >= t["magi_threshold"])

    amounts = parse_sweep(max_amount, step)
    # When pro-rata is modeled, truncate the sweep at the IRA balance so every rung is an
    # ACHIEVABLE conversion. Without this, rungs past B would carry a gross `amount`
    # exceeding the real maximum convertible (wrong threshold-crossing labels) and a
    # marginal-rate denominator using the full step instead of the real B − prev_amount
    # (understated rate). Both bugs vanish by construction once amounts <= B.
    truncated_at_balance = False
    if basis_modeled and value is not None and value < amounts[-1]:
        amounts = [a for a in amounts if a < value] + [round(value, 2)]
        truncated_at_balance = True
    rungs: "list[LadderRung]" = []
    crossings: "list[ThresholdCrossing]" = []
    _ltcg_zero_ceiling = cg_zero if cg_zero is not None else float("inf")
    _ltcg_fifteen_ceiling = cg_fifteen if cg_fifteen is not None else float("inf")
    seen_ltcg_0 = base_out["taxable_income"] > _ltcg_zero_ceiling
    seen_ltcg_15 = base_out["taxable_income"] > _ltcg_fifteen_ceiling
    seen_niit = (niit_threshold is not None and base_out["agi"] >= niit_threshold)
    irmaa_crossed = base_irmaa_idx  # number of tiers already crossed at baseline
    # Surcharge at the last-crossed tier (baseline tier at $0), so each crossing reports
    # its INCREMENT over the previous tier, not over the $0 baseline (which would
    # over-report every tier above the first when the crossings are read additively).
    last_irmaa_surcharge = base_irmaa_surcharge
    prev_total = None
    prev_amount = None

    # Form 8606 pro-rata: only the taxable fraction of the conversion is ordinary
    # income. A conversion of `amt` out of beginning balance B leaves year-end FMV
    # B - amt, so the keystone's line 9 recovers B and the taxable portion is
    # amt * (1 - basis/B). Amounts are clamped to <= B by the truncation above, so
    # we never need to cap converted here. Without basis/B we feed the full amount.
    convs = [compute_form_8606(0.0, basis, max(0.0, value - amt), 0.0,
                               amt)["taxable_conversion"]
             if basis_modeled else amt for amt in amounts]
    runs = [(base_inputs, {"ira_distribution": c}) for c in convs]
    # AA23: every rung priced on Schedule A at its own AGI where the return
    # recorded what a re-run needs, or all held.
    scens, repriced, held_notes = reprice_outcomes(
        result, rate_kwargs, base_out["agi"], runs,
        [compute_scenario(i, d, **rate_kwargs) for i, d in runs])

    state_effect = price_state_points(
        result, tax_law, year, filing_status, base_inputs, base_out,
        [({**base_inputs, "ira_distribution": base_inputs["ira_distribution"] + c}, s)
         for c, s in zip(convs, scens)], profile=profile,
        retirement_owner=ira_owner or ("primary" if profile.get("filing_status") != "MFJ" else "unconfirmed"))
    prev_combined = None
    for index, (amt, taxable_conv, scen) in enumerate(zip(amounts, convs, scens)):
        fed_anchored = round(line24 + (scen["total_federal_tax"] - baseline_recompute), 2)
        niit_here = compute_niit(scen["agi"], nii, niit_threshold, rate=niit_rate)
        niit_addon = round(niit_here - base_niit, 2)
        total = round(fed_anchored + niit_addon, 2)
        # Divide by the ACTUAL spend since the previous rung — not `step` — so the
        # appended partial final rung (when --max isn't a step multiple) reports a
        # correct marginal rate rather than diluting it across a full step.
        rung_delta = (amt - prev_amount) if prev_amount is not None else 0
        marginal = (round((total - prev_total) / rung_delta, 4)
                    if prev_total is not None and rung_delta else None)
        flags: "list[str]" = []

        # LTCG bracket bumping — taxable income crossing the preferential breakpoints,
        # only meaningful when there is preferential income to be bumped.
        if preferential > 0 and cg_zero is not None and not seen_ltcg_0 \
                and scen["taxable_income"] > cg_zero:
            seen_ltcg_0 = True
            flags.append("LTCG 0%→15%")
            crossings.append(ThresholdCrossing(
                kind="ltcg_0_15", label="Long-term gains start being taxed at 15%",
                at_amount=amt, magi=scen["agi"], est_annual_cost=None))
        if preferential > 0 and cg_fifteen is not None and not seen_ltcg_15 \
                and scen["taxable_income"] > cg_fifteen:
            seen_ltcg_15 = True
            flags.append("LTCG 15%→20%")
            crossings.append(ThresholdCrossing(
                kind="ltcg_15_20", label="Long-term gains start being taxed at 20%",
                at_amount=amt, magi=scen["agi"], est_annual_cost=None))

        # NIIT onset — MAGI clearing the threshold with investment income present.
        # The onset rung itself usually lands at zero excess (cost ramps in on later
        # rungs), so report the FULLY-PHASED-IN annual NIIT (3.8% × baseline NII) — the
        # planning magnitude now in play — rather than the ~$0 add-on at the boundary.
        if niit_threshold is not None and nii > 0 and not seen_niit \
                and scen["agi"] >= niit_threshold:
            seen_niit = True
            flags.append("NIIT onset (3.8%)")
            crossings.append(ThresholdCrossing(
                kind="niit",
                label="MAGI crosses NIIT threshold — 3.8% on investment income "
                      "(cost shown is the fully-phased-in annual NIIT)",
                at_amount=amt, magi=scen["agi"], est_annual_cost=round(niit_rate * nii, 2)))

        # IRMAA tiers — each new tier crossed by the conversion. est_annual_cost is the
        # INCREMENT over the previously-crossed tier so the crossings list sums correctly.
        tiers_now = sum(1 for t in irmaa_tiers if scen["agi"] >= t["magi_threshold"])
        while tiers_now > irmaa_crossed:
            tier = irmaa_tiers[irmaa_crossed]
            irmaa_crossed += 1
            incremental = round(tier["annual_surcharge_per_person"] - last_irmaa_surcharge, 2)
            last_irmaa_surcharge = tier["annual_surcharge_per_person"]
            flags.append(f"IRMAA tier @ MAGI {_money(tier['magi_threshold'])}")
            crossings.append(ThresholdCrossing(
                kind="irmaa",
                label=f"Medicare IRMAA surcharge tier (MAGI ≥ {_money(tier['magi_threshold'])})",
                at_amount=amt, magi=scen["agi"], est_annual_cost=incremental))

        # When the sweep was truncated at the IRA balance (--max ran past B), flag the
        # final rung so the user sees the sweep stopped at their balance, not at --max.
        if truncated_at_balance and amt == amounts[-1]:
            flags.append(f"Full IRA balance reached (max convertible {_money(value)})")

        state_tax = state_cost = combined_cost = combined_marginal = None
        if state_effect["modeled"]:
            state_tax = state_effect["points"][index]["anchored_total_tax"]
            state_cost = round(state_tax - state_effect["recorded_liability"], 2)
            combined_cost = round(total - line24 + state_cost, 2)
            combined_marginal = (round((combined_cost - prev_combined) / rung_delta, 4)
                                if prev_combined is not None and rung_delta else None)
            prev_combined = combined_cost
        rungs.append(LadderRung(
            amount=amt, taxable_conversion=round(taxable_conv, 2),
            agi=scen["agi"], taxable_income=scen["taxable_income"],
            fed_tax_anchored=fed_anchored, niit_addon=niit_addon, total_tax=total,
            cumulative_cost=round(total - line24, 2), marginal_rate=marginal, flags=flags,
            state_local_tax=state_tax, state_local_cost=state_cost,
            combined_cost=combined_cost, combined_marginal_rate=combined_marginal))
        prev_total = total
        prev_amount = amt

    priced = [x.agi for x in rungs]
    refuse_where_the_charity_cap_moves(result, base_out["agi"], priced, repriced=repriced)
    return LadderReport(
        schema=SCHEMA, name=name, tax_year=year, created=today.isoformat(),
        filing_status=filing_status, max_amount=round(float(amounts[-1]), 2),
        step=round(float(step), 2), anchor=anchor,
        basis_modeled=basis_modeled, ira_basis=basis, ira_value=value,
        baseline_nii=nii,
        niit_threshold=niit_threshold, niit_modeled=(niit_threshold is not None),
        irmaa_modeled=bool(irmaa_tiers), rungs=rungs, crossings=crossings,
        salt_cap_may_move=(not repriced and salt_cap_may_move(
            result, tax_law, year, filing_status, base_out["agi"], priced)),
        medical_floor_may_move=(not repriced and medical_floor_may_move(
            result, base_out["agi"], priced)),
        schedule_a_repriced=repriced, schedule_a_held=held_notes, state_effect=state_effect,
        ira_owner=ira_owner or ("primary" if basis_modeled else None))


def _report_to_dict(r: LadderReport) -> dict:
    return {
        "schema": r.schema, "name": r.name, "tax_year": r.tax_year, "created": r.created,
        "filing_status": r.filing_status, "max_amount": r.max_amount, "step": r.step,
        "anchor": r.anchor, "basis_modeled": r.basis_modeled, "ira_basis": r.ira_basis,
        "ira_value": r.ira_value, "ira_owner": r.ira_owner, "baseline_nii": r.baseline_nii,
        "niit_threshold": r.niit_threshold,
        "niit_modeled": r.niit_modeled, "irmaa_modeled": r.irmaa_modeled,
        "rungs": [vars(x) for x in r.rungs],
        "crossings": [vars(x) for x in r.crossings],
        "salt_cap_may_move": r.salt_cap_may_move,
        "medical_floor_may_move": r.medical_floor_may_move,
        "schedule_a_repriced": r.schedule_a_repriced,
        "schedule_a_held": r.schedule_a_held,
        "state_effect": r.state_effect,
    }


def _pct(v) -> str:
    return "—" if v is None else f"{v:.1%}"


def render_markdown(r: LadderReport) -> str:
    lines = [f"# Roth Conversion Ladder \"{r.name}\" — TY{r.tax_year}", ""]

    lines += ["## What this models", "",
              f"- Filing status: {r.filing_status or 'unknown'}",
              f"- Sweep: $0 → {_money(r.max_amount)} in {_money(r.step)} steps",
              "- A Roth conversion is taxable **ordinary income**, modeled as an extra "
              "traditional-IRA distribution.",
              "- Absolute tax at each rung is **anchored** to your real `result.json` "
              "line 24; only the marginal effect of the conversion is modeled.",
              f"- Baseline net investment income (for NIIT): **{_money(r.baseline_nii)}**"
              + ("" if not r.niit_modeled
                 else f"; NIIT threshold **{_money(r.niit_threshold)}**"), ""]

    if r.basis_modeled:
        # Floor at 0: when basis >= value (post-loss IRA) the taxable fraction is 0%,
        # never negative — the conversion is fully nontaxable up to the balance.
        taxable_frac = max(0.0, 1 - (r.ira_basis / r.ira_value)) if r.ira_value else 1.0
        lines += ["- **Form 8606 pro-rata applied:** after-tax basis "
                  f"**{_money(r.ira_basis)}** of a **{_money(r.ira_value)}** total traditional "
                  f"IRA balance, so only **{taxable_frac:.1%}** of each conversion is taxable "
                  "(the rest is return of basis). The *Taxable convert* column shows the taxed "
                  "portion.", ""]
    else:
        lines += ["- **Basis not modeled** — the full conversion is treated as taxable. Pass "
                  "`--ira-basis` and `--ira-value` (or set individual opening basis and provide "
                  "`--ira-value`) to apply the Form 8606 pro-rata rule.", ""]

    # Loud, unmissable banners when a threshold concern could not be checked — a $0
    # NIIT column or "none tripped" must never be read as "no impact" when the truth
    # is "no data". An empty IRMAA list is a legitimate not-on-Medicare state, so its
    # note is informational; a missing NIIT threshold indicates incomplete tax_law.
    if not r.niit_modeled:
        lines += [f"> **⚠ NIIT NOT MODELED** — no `federal.niit.threshold` in "
                  f"`tax_law_{r.tax_year}.json`. The 3.8% surtax is **excluded from every "
                  "rung** below; a conversion may incur NIIT this ladder does not show. "
                  "Run `/tax-setup` to fetch it.", ""]
    if not r.irmaa_modeled:
        lines += ["> **IRMAA not modeled** — no `federal.irmaa.tiers` populated in "
                  f"`tax_law_{r.tax_year}.json`, so Medicare premium-surcharge tiers were "
                  "**not checked**. Run `/tax-setup` to fetch them, or ignore if not on "
                  "Medicare.", ""]

    tx_hdr = " Taxable convert |" if r.basis_modeled else ""
    tx_sep = "---:|" if r.basis_modeled else ""
    lines += ["## Ladder", "",
              f"| Convert |{tx_hdr} Taxable income | Fed tax | NIIT add | Federal total | Federal cost | Federal marginal rate | Flags |",
              f"|---:|{tx_sep}---:|---:|---:|---:|---:|---:|---|"]
    for rung in r.rungs:
        tx_cell = f" {_money(rung.taxable_conversion)} |" if r.basis_modeled else ""
        lines.append(
            f"| {_money(rung.amount)} |{tx_cell} {_money(rung.taxable_income)} | "
            f"{_money(rung.fed_tax_anchored)} | {_money(rung.niit_addon)} | "
            f"{_money(rung.total_tax)} | {_money(rung.cumulative_cost)} | "
            f"{_pct(rung.marginal_rate)} | {', '.join(rung.flags) or '—'} |")
    lines.append("")

    if r.state_effect.get("modeled"):
        lines += ["## State and local effects", "",
                  "State liability is repriced independently. Refundable credits, payments, "
                  "use tax and penalties are excluded.", "",
                  f"| Convert | {r.state_effect['state']} state/local liability | State/local cost | Combined cost | Combined marginal rate |",
                  "|---:|---:|---:|---:|---:|"]
        for rung in r.rungs:
            lines.append(f"| {_money(rung.amount)} | {_money(rung.state_local_tax)} | "
                         f"{_money(rung.state_local_cost)} | {_money(rung.combined_cost)} | "
                         f"{_pct(rung.combined_marginal_rate)} |")
        lines.append("")
    else:
        lines += [f"> **State/local tax not modeled:** {note}" for note in r.state_effect.get("limitations", [])]
        lines.append("")

    lines += ["## Threshold crossings", ""]
    if not r.crossings:
        unchecked = [n for n, on in (("NIIT", r.niit_modeled), ("IRMAA", r.irmaa_modeled))
                     if not on]
        note = (f" (note: {' and '.join(unchecked)} not modeled — see banner above)"
                if unchecked else "")
        lines.append(f"- None of the modeled thresholds tripped within the swept range{note}.")
    else:
        for c in r.crossings:
            cost = "" if c.est_annual_cost is None else f" — est. +{_money(c.est_annual_cost)}/yr"
            lines.append(f"- **At {_money(c.at_amount)} converted** (MAGI {_money(c.magi)}): "
                         f"{c.label}{cost}.")
    lines.append("")

    lines += ["## Caveats",
              "- Non-deductible IRA **basis** (Form 8606 pro-rata) is applied when "
              "`--ira-basis`/`--ira-value` are supplied; otherwise the full conversion is "
              "treated as taxable. Basis is aggregated across all traditional/SEP/SIMPLE "
              "IRAs (per IRS rules) and is per-individual — a couple each converting needs "
              "separate runs.",
              "- MAGI is approximated as AGI; tax-exempt-interest / foreign-EI add-backs "
              "are not modeled.",
              "- NIIT add-on uses baseline net investment income held fixed (a conversion "
              "is not itself investment income). NII = interest + ordinary dividends + net "
              "long-term gains; net **short-term** gains (routed to ordinary income by the "
              "toolkit) are not added back, so baseline NII may be understated if you have "
              "large short-term gains.",
              "- IRMAA is a Medicare **premium** surcharge (2-year lookback), not income "
              "tax — shown as a flagged side cost, never added to the tax total. Surcharge "
              "is per beneficiary; double it if both spouses are on Medicare.",
              "- Marginal-rate column already reflects LTCG bracket bumping (the QDCG "
              "worksheet stacks the conversion under preferential income).",
              "- MFS/HOH use the single-status IRMAA schedule (`federal.irmaa.tiers` "
              "is keyed `single`/`mfj` only).",
              "- OBBBA Schedule 1-A (line 13b) deductions, the senior deduction "
              "among them, are held at the baseline amount: a rung that carries "
              "MAGI through a phase-out removes deduction this table does not, "
              "so its cost may read low.",
              "- Taxable Social Security is recomputed at each rung from confirmed "
              "regular-worksheet inputs. Gross benefits, tax-exempt interest, exclusion "
              "add-backs and non-SE adjustments stay fixed. Lump-sum and combined IRA "
              "worksheets require separate review."]
    lines += schedule_a_caveat(r.schedule_a_repriced, r.schedule_a_held)
    lines += salt_phasedown_caveat(
        r.salt_cap_may_move, "rungs that carry modified AGI above the threshold can cost "
        "more than shown.")
    lines += medical_floor_caveat(
        r.medical_floor_may_move, "each rung can cost more than shown.")
    if r.ira_owner:
        lines += [f"- IRA owner: **{r.ira_owner}**; only this individual\'s traditional/SEP/SIMPLE IRAs are pooled."]
    lines += ["- This is a planning estimate, not a filed return.", ""]
    return "\n".join(lines) + "\n"


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.roth_ladder")
    parser.add_argument("--result", required=True, help="path to result.json")
    add_workspace_argument(parser)
    parser.add_argument("--year", required=True, help="current tax year")
    parser.add_argument("--name", default="roth-ladder", help="scenario name (single path segment)")
    parser.add_argument("--max", default=100000, help="maximum conversion to sweep (default 100000)")
    parser.add_argument("--step", default=5000, help="sweep increment (default 5000)")
    parser.add_argument("--today", default=None, help="override today's date YYYY-MM-DD (testing)")
    parser.add_argument("--ira-basis", default=None,
                        help="after-tax basis for --ira-owner; default: individual opening profile balance")
    parser.add_argument("--ira-owner", choices=["primary", "spouse"],
                        help="individual whose IRA balance/basis is modeled; required for joint pro-rata")
    parser.add_argument("--ira-value", default=None,
                        help="total beginning-of-year traditional/SEP/SIMPLE IRA balance; "
                             "required to apply Form 8606 pro-rata")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    parser.add_argument("--no-save", action="store_true",
                        help="do not write scenarios/<name>.json")
    args = parser.parse_args(argv)

    try:
        year = _validate_year_arg(args.year)
        workspace = resolve_workspace(args.workspace).resolve()
        result_path = _resolve_under(args.result, workspace)
        result = _read_json(result_path)
        read_tax_year(result, year)

        profile = _read_json(workspace / "taxpayer_profile.json")
        tax_law_path = workspace / f"tax_law_{year}.json"
        tax_law = _read_json(tax_law_path) if tax_law_path.exists() else {}

        name = _validate_name(args.name)
        amounts = parse_sweep(args.max, args.step)  # validate early
        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
    except (WorkspaceError, RothLadderError, ScenarioError, RolloverError,
            ProjectionError, ValueError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    try:
        report = build_ladder(result, profile, tax_law, year, max_amount=amounts[-1],
                              step=_coerce_numeric(args.step, default=5000), name=name,
                              today=today, ira_basis=args.ira_basis, ira_value=args.ira_value, ira_owner=args.ira_owner)
    except (RothLadderError, ScenarioError, ProjectionError, ValueError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    data = _report_to_dict(report)
    if not args.no_save:
        try:
            out = save_scenario(workspace, name, data)
            print(f"# Saved {redact(out)}", file=sys.stderr)
        except (ScenarioError, RolloverError) as e:
            print(f"ERROR: {redact(e)}", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
