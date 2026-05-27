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
from dataclasses import dataclass
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
    _resolve_under,
    _validate_name,
    extract_baseline_inputs,
    read_tax_year,
    save_scenario,
)
from agents.lib.tax_computations import (  # noqa: E402
    _coerce_numeric,
    compute_niit,
    compute_scenario,
)

SCHEMA = "tax-roth-ladder/v1"
MAX_RUNGS = 200  # bound output / compute for pathological --max/--step combinations


class RothLadderError(Exception):
    """Raised when /tax-roth-convert cannot safely proceed."""


@dataclass(frozen=True)
class LadderRung:
    amount: float           # conversion modeled at this rung
    agi: float              # scenario AGI (== MAGI proxy)
    taxable_income: float
    fed_tax_anchored: float  # line 24 + keystone marginal effect (ex-NIIT)
    niit_addon: float        # incremental NIIT vs the $0 rung
    total_tax: float         # fed_tax_anchored + niit_addon
    cumulative_cost: float   # total_tax - baseline (== line 24)
    marginal_rate: "float | None"  # Δtotal / step vs the previous rung; None at $0
    flags: "list[str]"       # threshold labels first crossed at this rung


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
    baseline_nii: float
    niit_threshold: "float | None"
    niit_modeled: bool       # False → no NIIT threshold in tax_law; surtax EXCLUDED
    irmaa_modeled: bool      # False → no IRMAA tiers in tax_law; tiers NOT checked
    rungs: "list[LadderRung]"
    crossings: "list[ThresholdCrossing]"


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


def build_ladder(result: dict, profile: dict, tax_law: dict, year: int, *,
                 max_amount: float, step: float, name: str,
                 today: datetime.date) -> LadderReport:
    filing_status = ((result.get("meta", {}) or {}).get("filing_status")
                     or profile.get("filing_status"))
    rate_kwargs = _rate_kwargs(profile, tax_law, filing_status)
    base_inputs = extract_baseline_inputs(result)

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

    for amt in amounts:
        scen = compute_scenario(base_inputs, {"ira_distribution": amt}, **rate_kwargs)
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

        rungs.append(LadderRung(
            amount=amt, agi=scen["agi"], taxable_income=scen["taxable_income"],
            fed_tax_anchored=fed_anchored, niit_addon=niit_addon, total_tax=total,
            cumulative_cost=round(total - line24, 2), marginal_rate=marginal, flags=flags))
        prev_total = total
        prev_amount = amt

    return LadderReport(
        schema=SCHEMA, name=name, tax_year=year, created=today.isoformat(),
        filing_status=filing_status, max_amount=round(float(amounts[-1]), 2),
        step=round(float(step), 2), anchor=anchor, baseline_nii=nii,
        niit_threshold=niit_threshold, niit_modeled=(niit_threshold is not None),
        irmaa_modeled=bool(irmaa_tiers), rungs=rungs, crossings=crossings)


def _report_to_dict(r: LadderReport) -> dict:
    return {
        "schema": r.schema, "name": r.name, "tax_year": r.tax_year, "created": r.created,
        "filing_status": r.filing_status, "max_amount": r.max_amount, "step": r.step,
        "anchor": r.anchor, "baseline_nii": r.baseline_nii, "niit_threshold": r.niit_threshold,
        "niit_modeled": r.niit_modeled, "irmaa_modeled": r.irmaa_modeled,
        "rungs": [vars(x) for x in r.rungs],
        "crossings": [vars(x) for x in r.crossings],
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

    lines += ["## Ladder", "",
              "| Convert | Taxable income | Fed tax | NIIT add | Total tax | Cumulative cost | Marginal rate | Flags |",
              "|---:|---:|---:|---:|---:|---:|---:|---|"]
    for rung in r.rungs:
        lines.append(
            f"| {_money(rung.amount)} | {_money(rung.taxable_income)} | "
            f"{_money(rung.fed_tax_anchored)} | {_money(rung.niit_addon)} | "
            f"{_money(rung.total_tax)} | {_money(rung.cumulative_cost)} | "
            f"{_pct(rung.marginal_rate)} | {', '.join(rung.flags) or '—'} |")
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
              "- Federal only — state tax on the conversion is not modeled in v1.",
              "- Full conversion is treated as taxable; non-deductible IRA **basis** "
              "(Form 8606 pro-rata) is not applied (backlog #7).",
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
              "- MFS/HOH use single-status capital-gains and IRMAA schedules (toolkit-wide "
              "`tax_law` simplification).",
              "- This is a planning estimate, not a filed return.", ""]
    return "\n".join(lines) + "\n"


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.roth_ladder")
    parser.add_argument("--result", required=True, help="path to result.json")
    parser.add_argument("--workspace", required=True, help="current workspace directory")
    parser.add_argument("--year", required=True, help="current tax year")
    parser.add_argument("--name", default="roth-ladder", help="scenario name (single path segment)")
    parser.add_argument("--max", default=100000, help="maximum conversion to sweep (default 100000)")
    parser.add_argument("--step", default=5000, help="sweep increment (default 5000)")
    parser.add_argument("--today", default=None, help="override today's date YYYY-MM-DD (testing)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    parser.add_argument("--no-save", action="store_true",
                        help="do not write scenarios/<name>.json")
    args = parser.parse_args(argv)

    try:
        year = _validate_year_arg(args.year)
        workspace = Path(args.workspace).resolve()
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
    except (RothLadderError, ScenarioError, RolloverError, ProjectionError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    try:
        report = build_ladder(result, profile, tax_law, year, max_amount=amounts[-1],
                              step=_coerce_numeric(args.step, default=5000), name=name,
                              today=today)
    except (RothLadderError, ScenarioError, ProjectionError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    data = _report_to_dict(report)
    if not args.no_save:
        try:
            out = save_scenario(workspace, name, data)
            print(f"# Saved {out}", file=sys.stderr)
        except (ScenarioError, RolloverError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
