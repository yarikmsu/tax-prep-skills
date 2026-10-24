#!/usr/bin/env python3
"""Scenario modeling for /tax-scenario.

Forks the computed baseline in result.json, applies one or more signed income
deltas, recomputes the FEDERAL tax effect, and reports the marginal cost and the
movement in AGI / taxable income / effective rate. Scenarios are stackable via
--base scenarios/<other>.json.

Never mutates result.json or taxpayer_profile.json. Writes ONLY under
workspace/scenarios/. Pure compute + markdown renderer + guarded writer + CLI.
Reuses tax_computations.compute_scenario (the keystone), projection helpers,
and rollover.py path/year validation.
"""

import datetime
import json
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.projection import (  # noqa: E402
    ProjectionError,
    _binary_status_key,
    capital_gains_limits,
    _capital_loss_limit,
    _full_status_key,
    _money,
    _num,
    _read_json,
    _validate_tax_law_year,
)
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_subpath,
    _validate_year_arg,
)
from agents.lib.secure_fs import atomic_write, redact, secure_path  # noqa: E402
from agents.lib.tax_computations import (  # noqa: E402
    SCENARIO_INPUT_KEYS,
    _medical_deductible,
    _coerce_numeric,
    compute_scenario,
    compute_taxable_ss,
    net_capital_gains,
    rerun_schedule_a,
)
from agents.lib.tax_computations.federal import salt  # noqa: E402
from agents.lib.tax_computations.state.planning import price_state_points  # noqa: E402
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    resolve_under_workspace as _resolve_under,
    resolve_workspace,
)


class ScenarioError(Exception):
    """Raised when /tax-scenario cannot safely proceed."""


# Delta keys the user may type. roth_conversion is an alias: a Roth conversion is
# taxable ordinary income, modeled as an IRA distribution.
DELTA_ALIASES = {"roth_conversion": "ira_distribution"}
DELTA_KEYS = set(SCENARIO_INPUT_KEYS) | set(DELTA_ALIASES)


@dataclass(frozen=True)
class Outcome:
    gross_income: float
    adjustments: float
    agi: float
    taxable_income: float
    tax_before_credits: float
    ctc: float
    se_tax: float
    additional_medicare: float
    total_federal_tax: float
    used_qdcg: bool
    taxable_ss: float = 0.0


@dataclass(frozen=True)
class ScenarioDiff:
    delta_income: float
    delta_agi: float
    delta_taxable_income: float
    delta_total_tax: float
    marginal_rate: "float | None"  # None when delta_income == 0
    baseline_anchored_total_tax: float  # anchored tax of the IMMEDIATE base (== line_24 when unstacked)
    anchored_scenario_total_tax: float
    baseline_effective_rate: "float | None"
    scenario_effective_rate: "float | None"


@dataclass(frozen=True)
class ScenarioReport:
    schema: str
    name: str
    tax_year: int
    created: str
    base: str
    filing_status: "str | None"
    applied_deltas: "list[dict]"
    anchor: dict  # {"line_24": float, "baseline_recompute_total": float}
    inputs: dict  # resolved post-delta normalized inputs (for stacking)
    base_outcome: Outcome
    scenario_outcome: Outcome
    diff: ScenarioDiff
    salt_cap_may_move: bool = True  # gates salt_phasedown_caveat
    medical_floor_may_move: bool = True  # gates medical_floor_caveat
    # AA23: Schedule A re-priced at each AGI from the return's recorded inputs.
    schedule_a_repriced: bool = False
    # What the re-pricing held rather than priced: the re-run's own notes.
    schedule_a_held: "list[str]" = field(default_factory=list)
    state_effect: dict = field(default_factory=dict)


def parse_deltas(tokens: "list[str]") -> dict:
    """Parse ['KEY=AMOUNT', ...] into a normalized signed-additive delta dict.

    Resolves aliases, validates keys, coerces amounts, and sums repeated keys.
    """
    if not tokens:
        raise ScenarioError(
            "No deltas given. Provide at least one KEY=AMOUNT "
            f"(valid keys: {', '.join(sorted(DELTA_KEYS))})."
        )
    out: dict = {}
    for tok in tokens:
        if "=" not in tok:
            raise ScenarioError(f"Delta {redact(repr(tok))} is not KEY=AMOUNT.")
        key, _, raw = tok.partition("=")
        key = key.strip().lstrip("+")  # allow a leading +KEY for readability
        raw = raw.strip()
        if key not in DELTA_KEYS:
            raise ScenarioError(
                f"Unknown delta key {redact(repr(key))}. "
                f"Valid keys: {', '.join(sorted(DELTA_KEYS))}."
            )
        amount = _coerce_numeric(raw, default=None)
        if amount is None:
            raise ScenarioError(
                f"Delta amount for {redact(repr(key))} is not numeric: "
                f"{redact(repr(raw))}")
        resolved = DELTA_ALIASES.get(key, key)
        out[resolved] = round(out.get(resolved, 0.0) + amount, 2)
    return out


def _get(d: dict, dotted: str, default=0):
    cur = d
    for seg in dotted.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return default
        cur = cur[seg]
    return cur


def read_tax_year(result: dict, year: int) -> None:
    """Validate result.json is for `year` and has a computed line 24."""
    result_year = (result.get("meta", {}) or {}).get("tax_year")
    if result_year != year:
        raise ScenarioError(
            f"result.json is for TY{result_year}, not TY{year}. "
            f"Pass --year {result_year} or recompute for {year}."
        )
    line24 = _get(result.get("form_1040", {}) or {}, "line_24_total_tax", default=None)
    if line24 is None:
        raise ScenarioError(
            "result.json has no form_1040.line_24_total_tax — run /tax-compute first."
        )


# Netting logic lives in tax_computations.net_capital_gains so projection
# (which scenario.py imports FROM — importing scenario there would be circular)
# can reuse the identical character-preservation rules. This private alias is the
# stable name loss_harvest.py and the tests import.
_net_capital_gains = net_capital_gains


def _deducted_se_tips(result: dict, se_net: float) -> float:
    section = result.get("schedule_1a") or {}
    if not isinstance(section, dict):
        raise ScenarioError("schedule_1a must be an object")
    tips = section.get("tips") or {}
    if not isinstance(tips, dict):
        raise ScenarioError("schedule_1a.tips must be an object")
    allowed = _num(tips.get("allowed"), "tips.allowed")
    if allowed < 0:
        raise ScenarioError("tips.allowed must be non-negative")
    raw = section.get("se_tips_deducted")
    if raw is None:
        if se_net > 0 and allowed > 0:
            raise ScenarioError("Record schedule_1a.se_tips_deducted with /tax-compute: "
                                "a tipped return with business income needs its deducted SE-tip split")
        return 0.0
    deducted = _num(raw, "se_tips_deducted")
    if deducted < 0 or deducted > allowed:
        raise ScenarioError("se_tips_deducted must be non-negative and no greater than tips.allowed")
    return round(deducted, 2)


def extract_baseline_inputs(result: dict) -> dict:
    """Map a computed result.json into the normalized income input set."""
    f = result.get("form_1040", {}) or {}
    wages = _num(f.get("line_1z_total_wages") or f.get("line_1a_wages"), "wages")
    # Capital gains, in order of fidelity:
    #  1. Schedule D with short/long detail → net the two, preserving rate character
    #     (LT gain → preferential QDCG; ST gain → ordinary; net loss → capped in keystone).
    #  2. Schedule D with only the combined line 16 (e.g. rollover-shaped results) →
    #     treat combined as net LTCG (can't split character without the detail).
    #  3. No Schedule D → fall back to 1040 line 7 (combined), same simplification.
    ordinary_short = 0.0
    sd = result.get("schedule_d")
    if isinstance(sd, dict) and ("part_i_short_term" in sd or "part_ii_long_term" in sd):
        net_lt = _num(
            _get(sd, "part_ii_long_term.line_15_net_long_term", default=0), "capital_gains"
        )
        net_st = _num(
            _get(sd, "part_i_short_term.line_7_net_short_term", default=0), "short_term_gain"
        )
        net_ltcg, ordinary_short = _net_capital_gains(net_st, net_lt)
    elif isinstance(sd, dict) and sd.get("line_16_combined") is not None:
        net_ltcg = _num(sd.get("line_16_combined"), "capital_gains")
    else:
        net_ltcg = _num(f.get("line_7_capital_gain"), "capital_gains")
    se_net = 0.0
    sc = result.get("schedule_c")
    if isinstance(sc, dict):
        se_net = _num(sc.get("line_31_net"), "self_employment_net")
    inputs: dict = {
        "wages": wages,
        "taxable_interest": _num(f.get("line_2b_taxable_interest"), "taxable_interest"),
        "ordinary_dividends": _num(f.get("line_3b_ordinary_dividends"), "ordinary_dividends"),
        "qualified_dividends": _num(f.get("line_3a_qualified_dividends"), "qualified_dividends"),
        "capital_gains": net_ltcg,
        "ira_distribution": round(
            _num(f.get("line_4b_taxable_ira"), "line_4b_taxable_ira")
            + _num(f.get("line_5b_taxable_pensions"), "line_5b_taxable_pensions"), 2),
        "self_employment_net": se_net,
        # other_income is the ordinary catch-all: Schedule 1 + net short-term gain +
        # taxable Social Security (line 6b). The keystone replaces its baseline
        # taxable portion with the repriced amount from the recorded worksheet.
        "other_income": round(
            _num(f.get("line_8_schedule1_other"), "other_income") + ordinary_short
            + _num(f.get("line_6b_taxable_ss"), "taxable_ss"), 2),
        # OBBBA below-AGI deductions the keystone subtracts before QBI so
        # marginal deltas are priced from the correct taxable-income position
        # (not delta-able income fields): Schedule 1-A total (line 13b) PLUS the
        # §170(p) non-itemizer charitable deduction (line 13c). Both sit in the
        # line-13 region below AGI and reduce the QBI cap base identically, so
        # they combine into the single fixed `schedule_1a_deduction` input.
        "schedule_1a_deduction": round(
            _num(f.get("line_13b_schedule_1a_deductions"),
                 "schedule_1a_deduction")
            + _num(f.get("line_13c_charitable_non_itemizer"),
                   "charitable_non_itemizer"), 2),
        "schedule_1a_se_tips": _deducted_se_tips(result, se_net),
    }
    benefits = _num(f.get("line_6a_social_security"), "Social Security benefits")
    taxable = _num(f.get("line_6b_taxable_ss"), "taxable Social Security")
    if benefits != 0 or taxable != 0:
        evidence = result.get("social_security_worksheet")
        if not isinstance(evidence, dict) or evidence.get("regular_worksheet_confirmed") is not True:
            raise ScenarioError("Social Security benefits require recorded regular-worksheet inputs; "
                                "never infer gross benefits from line 6b")
        worksheet = evidence.get("inputs")
        required = {"ss_benefits", "other_income", "tax_exempt_interest", "adjustments",
                    "filing_status", "lived_apart_from_spouse_all_year",
                    "lump_sum_prior_year_benefits", "excluded_income_addbacks"}
        if not isinstance(worksheet, dict) or set(worksheet) != required:
            raise ScenarioError("Social Security requires every original worksheet input")
        expected_other = sum(_num(f.get(k), k) for k in
                             ("line_1z_total_wages", "line_2b_taxable_interest",
                              "line_3b_ordinary_dividends", "line_4b_taxable_ira",
                              "line_5b_taxable_pensions", "line_7_capital_gain", "line_8_schedule1_other"))
        try:
            computed = compute_taxable_ss(**worksheet)
            if (benefits <= 0 or worksheet["lump_sum_prior_year_benefits"] != 0
                    or isinstance(evidence.get("tax_year"), bool)
                    or not isinstance(evidence.get("tax_year"), int)
                    or worksheet["ss_benefits"] != benefits
                    or worksheet["other_income"] != round(expected_other, 2)
                    or worksheet["tax_exempt_interest"] != _num(f.get("line_2a_tax_exempt_interest"), "line 2a")
                    or worksheet["adjustments"] != _num(f.get("line_10_adjustments"), "line 10")
                    or computed["taxable_ss"] != taxable
                    or evidence.get("tax_year") != (result.get("meta") or {}).get("tax_year")
                    or _full_status_key(worksheet["filing_status"]) != _full_status_key(
                        (result.get("meta") or {}).get("filing_status"))
                    or (worksheet["filing_status"].strip().upper() == "MFJ") != (
                        ((result.get("meta") or {}).get("filing_status") or "").strip().upper() == "MFJ")):
                raise ValueError("inconsistent worksheet")
        except (ValueError, TypeError, ProjectionError, AttributeError):
            raise ScenarioError("Social Security worksheet does not reproduce the return or uses "
                                "a lump-sum/repayment method; obtain reviewed evidence") from None
        inputs["social_security"] = {"regular_worksheet_confirmed": True,
                                     "worksheet": dict(worksheet),
                                     "origin": {k: inputs[k] for k in SCENARIO_INPUT_KEYS}}
    return inputs


def _num_ctc_children(profile: dict) -> int:
    deps = profile.get("dependents") or []
    return sum(1 for d in deps if isinstance(d, dict) and d.get("ctc_eligible"))


def return_deduction(result: dict) -> float:
    """The return's line 12, standard or itemized — the deduction every
    planning tool holds across the baseline and the scenario.

    It was the cache's standard deduction for every filer, so an itemizer was
    priced from a taxable-income position too high by the difference, and a
    delta could land a bracket too high: a harvest or PTET saving over-stated,
    the direction AGENTS.md §4 makes a hard error (AA20). The age-65/blind
    add-on rides along, since line 12 carries it.

    Missing or corrupt, it refuses rather than falling back to the cache: that
    fallback is the over-statement this replaces.
    """
    value = (result.get("form_1040", {}) or {}).get("line_12_standard_deduction")
    if value is None:
        raise ScenarioError(
            "result.json has no form_1040.line_12_standard_deduction: the "
            "planning tools price from the deduction the return took. Run "
            "/tax-compute first.")
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ScenarioError(
            "result.json form_1040.line_12_standard_deduction must be a finite, "
            f"non-negative number, got {type(value).__name__}")
    # Zero is the template's placeholder. The only return that deducts
    # nothing is one §63(c)(6)(A) forced to itemize, an MFS filer whose spouse
    # itemizes, with nothing to itemize; anything else priced on $0 over-states
    # more than the cache fallback this replaced.
    sched = result.get("schedule_a")
    forced_to_nothing = (isinstance(sched, dict)
                         and sched.get("use_itemized") is True
                         and _known_amount(sched.get("itemized_total")) == 0)
    if value == 0 and not forced_to_nothing:
        raise ScenarioError(
            "result.json form_1040.line_12_standard_deduction is 0, the "
            "template's placeholder: only a return forced to itemize with "
            "nothing to itemize deducts nothing. Run /tax-compute first.")
    return round(float(value), 2)


def _rate_kwargs(profile: dict, tax_law: dict, filing_status: "str | None",
                 result: dict) -> dict:
    """The compute_scenario kwargs: the cache's rates for this filing status,
    and the return's own deduction (`return_deduction`), read last so a
    missing status or rate refuses first."""
    full_key = _full_status_key(filing_status)
    ss = result.get("social_security_worksheet")
    if isinstance(ss, dict) and ss.get("regular_worksheet_confirmed") is True:
        worksheet_year = ss.get("tax_year")
        if isinstance(worksheet_year, bool) or not isinstance(worksheet_year, int):
            raise ScenarioError("Social Security worksheet needs its covered tax year")
        _validate_tax_law_year(tax_law, worksheet_year)
        saved = ss.get("inputs")
        saved_status = saved.get("filing_status") if isinstance(saved, dict) else None
        if (_full_status_key(saved_status, "Social Security worksheet") != full_key
                or (str(saved_status).strip().upper() == "MFJ") != (str(filing_status).strip().upper() == "MFJ")):
            raise ScenarioError("Social Security worksheet filing status differs from the planner")
    bin_key = _binary_status_key(filing_status)
    fed = tax_law.get("federal", {}) or {}
    fstatus = (fed.get("filing_statuses", {}) or {}).get(full_key, {}) or {}
    brackets = fstatus.get("brackets") or []
    if not brackets:
        raise ScenarioError(
            f"No federal brackets for status '{full_key}' in tax_law. Run /tax-setup to fetch."
        )
    cg_zero, cg_fifteen = capital_gains_limits(tax_law, filing_status, "scenario")
    ctc_cfg = fed.get("child_tax_credit", {}) or {}
    addl_cfg = fed.get("additional_medicare_tax", {}) or {}
    threshold_map = addl_cfg.get("threshold") or {}
    return {
        "brackets": brackets,
        "cg_zero_limit": cg_zero,
        "cg_fifteen_limit": cg_fifteen,
        "ss_wage_base": (fed.get("social_security", {}) or {}).get("wage_base"),
        "qbi_rate": fed.get("qbi_deduction_rate", 0.20),
        "num_ctc_children": _num_ctc_children(profile),
        "ctc_per_child": ctc_cfg.get("amount_per_child"),
        "ctc_phase_out_start": (ctc_cfg.get("phase_out_start") or {}).get(bin_key),
        "addl_medicare_threshold": threshold_map.get(full_key) or threshold_map.get(bin_key),
        "addl_medicare_rate": addl_cfg.get("rate", 0.009),
        "capital_loss_limit": _capital_loss_limit(filing_status, "scenario"),
        "deduction": return_deduction(result),
    }


def _outcome(d: dict) -> Outcome:
    return Outcome(
        gross_income=d["gross_income"], adjustments=d["adjustments"], agi=d["agi"],
        taxable_income=d["taxable_income"], tax_before_credits=d["tax_before_credits"],
        ctc=d["ctc"], se_tax=d["se_tax"], additional_medicare=d["additional_medicare"],
        total_federal_tax=d["total_federal_tax"], used_qdcg=d["used_qdcg"],
        taxable_ss=d.get("taxable_ss", 0.0),
    )


def _eff_rate(tax: float, agi: float) -> "float | None":
    # Undefined for non-positive AGI; also suppress a negative rate, which can
    # arise only in the degenerate case where modeled savings exceed the real tax.
    return round(tax / agi, 4) if (agi > 0 and tax >= 0) else None


def _require_the_same_deduction(anchor: dict, deduction: float) -> None:
    """A stacked base carries the anchor it was priced with, and
    `build_report` re-prices its inputs on the CURRENT deduction. Two
    deductions in one `line_24 + (base - anchor)` moved a saved baseline from
    $27,000 to $15,800 with no warning. A base saved before AA20 was priced on
    the cache's standard deduction and records none."""
    saved = anchor.get("deduction")
    if saved is None:
        raise ScenarioError(
            "--base scenario was saved before the planning tools priced from "
            "the return's line 12, so its figures rest on another deduction. "
            "Re-run it from result.json, then stack on the new file.")
    if (isinstance(saved, bool) or not isinstance(saved, (int, float))
            or not math.isfinite(saved)):
        raise ScenarioError(
            f"--base scenario anchor.deduction is not numeric: {redact(repr(saved))}")
    if round(float(saved), 2) != round(deduction, 2):
        raise ScenarioError(
            f"--base scenario was priced on a ${saved:,.2f} deduction, and the "
            f"return's line 12 is now ${deduction:,.2f}. Re-run it from "
            "result.json, then stack on the new file.")


def resolve_base(result: dict, base_scenario: "dict | None", year: int,
                 filing_status: "str | None") -> dict:
    """Return {inputs, anchor, applied_deltas, base_label} for the chosen base.

    base=result.json → inputs from result, anchor recomputed against result line 24.
    base=a prior scenario file → carry its resolved inputs, anchor, and deltas.
    """
    if base_scenario is None:
        return {
            "inputs": extract_baseline_inputs(result),
            "anchor": None,  # filled by build_report after the zero-delta recompute
            "applied_deltas": [],
            "base_label": "result.json",
        }
    if base_scenario.get("schema") != "tax-scenario/v1":
        raise ScenarioError("--base file is not a tax-scenario/v1 scenario.")
    # A base from a different tax year carries that year's baseline numbers, which
    # would be recomputed with the CURRENT year's tax law — silently wrong. Refuse.
    base_year = base_scenario.get("tax_year")
    if base_year != year:
        raise ScenarioError(
            f"--base scenario is for TY{base_year}, not TY{year}. Cannot stack across tax years."
        )
    # Likewise refuse a base built under a different filing status: its anchor was
    # computed with that status's brackets, but build_report recomputes the carried
    # inputs with the CURRENT status — mixing the two is silently wrong. Compare by
    # effective bracket key so MFJ/QSS (same brackets) are treated as compatible.
    base_fs = base_scenario.get("filing_status")
    try:
        base_key = _full_status_key(base_fs, "--base scenario")
    except ProjectionError:
        # `_canonical_status`'s remedy names result.json and
        # taxpayer_profile.json, which is right for every OTHER caller and
        # wrong here: this status was read out of the stored scenario passed
        # as --base, and sending the reader to two files that are both fine is
        # worse than the silent "single" this replaced. The value is not
        # echoed — a hand-edited scenario can carry misplaced data.
        raise ScenarioError(
            "--base scenario file has no usable `filing_status`: it must be "
            "one of Single/MFJ/MFS/HoH/QSS. Re-save the base scenario with "
            "/tax-scenario, or correct that field in the scenario file by "
            "hand — result.json and taxpayer_profile.json are not at fault."
        ) from None
    if base_key != _full_status_key(filing_status, "scenario"):
        raise ScenarioError(
            f"--base scenario filing status ({base_fs}) differs from current "
            f"({filing_status}). Cannot stack across filing statuses."
        )
    inputs = base_scenario.get("inputs")
    anchor = base_scenario.get("anchor")
    if not isinstance(inputs, dict) or not isinstance(anchor, dict):
        raise ScenarioError("--base scenario is missing 'inputs' or 'anchor'.")
    # The anchored-tax arithmetic does raw subtraction on these, so a hand-edited
    # string/None must fail with a controlled error, not a TypeError traceback.
    for key in ("line_24", "baseline_recompute_total"):
        val = anchor.get(key)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ScenarioError(
                f"--base scenario anchor.{key} is not numeric: "
                f"{redact(repr(val))}")
    return {
        "inputs": dict(inputs),
        "anchor": anchor,
        "applied_deltas": list(base_scenario.get("applied_deltas") or []),
        "base_label": f"scenarios/{base_scenario.get('name', 'unknown')}.json",
    }


def build_report(result: dict, profile: dict, tax_law: dict, year: int, name: str,
                 deltas: dict, base_scenario: "dict | None",
                 today: datetime.date) -> ScenarioReport:
    from agents.lib.tax_computations.federal.rentals import rental_planning_block
    if rental_planning_block(profile, result):
        raise ScenarioError("Residential rental passive-loss limits and depreciation are not repriced by scenarios; recompute the return with revised inputs instead")
    filing_status = ((result.get("meta", {}) or {}).get("filing_status")
                     or profile.get("filing_status"))
    rate_kwargs = _rate_kwargs(profile, tax_law, filing_status, result)
    base = resolve_base(result, base_scenario, year, filing_status)
    if base["anchor"] is not None:
        _require_the_same_deduction(base["anchor"], rate_kwargs["deduction"])
    base_inputs = base["inputs"]
    if base_inputs.get("social_security") is not None:
        read_tax_year(result, year)
    if base_scenario is not None and base_inputs.get("social_security") != extract_baseline_inputs(result).get("social_security"):
        raise ScenarioError("The base scenario used different or missing Social Security evidence; "
                            "re-run it from result.json before stacking")
    if (base_scenario is not None
            and base_inputs.get("schedule_1a_se_tips", 0)
            != extract_baseline_inputs(result)["schedule_1a_se_tips"]):
        raise ScenarioError("The base scenario used a different deducted SE-tip split; "
                            "re-run it from result.json before stacking")

    # Deltas are applied verbatim (no derived adjustments), so every delta is exactly
    # reversible when stacking. `qualified_dividends` is the qualified SPLIT of ordinary
    # dividends — it never changes gross on its own; the keystone clamps it to <=
    # ordinary. To add dividend income, raise `ordinary_dividends` (with an optional
    # `qualified_dividends` portion), mirroring 1040 lines 3b/3a.

    # Outcome of the immediate base (zero further delta) and of the new scenario.
    base_out = compute_scenario(base_inputs, {}, **rate_kwargs)
    scen_out = compute_scenario(base_inputs, deltas, **rate_kwargs)
    # The keystone's AGI for the return itself: the frame every held-deduction
    # check is measured in, which a stacked base has already moved away from.
    return_agi = compute_scenario(
        extract_baseline_inputs(result), {}, **rate_kwargs)["agi"]
    priced = [base_out["agi"], scen_out["agi"]]
    # AA23: Schedule A re-priced at each AGI where the return recorded what a
    # re-run needs. AGI does not depend on the deduction, so each side is
    # priced again at the AGI it already has; anywhere the re-run refuses,
    # both stay held and the disclosures below apply as before.
    (base_out, scen_out), repriced, held_notes = reprice_outcomes(
        result, rate_kwargs, return_agi,
        [(base_inputs, {}), (base_inputs, deltas)], [base_out, scen_out])
    # Binding caps can be re-priced only with confirmed donation classes.
    refuse_where_the_charity_cap_moves(result, return_agi, priced, repriced=repriced)

    # Anchor is constant across a stack: original result.json line 24 + the zero-delta
    # recompute of the ORIGINAL inputs. For base=result.json we establish it here.
    if base["anchor"] is None:
        line24 = _num(_get(result.get("form_1040", {}) or {}, "line_24_total_tax"), "line_24")
        anchor = {"line_24": round(line24, 2),
                  "baseline_recompute_total": base_out["total_federal_tax"],
                  "deduction": rate_kwargs["deduction"],
                  "schedule_a_repriced": repriced}
    else:
        anchor = base["anchor"]
        # A stack priced one way and extended the other mixes a re-priced
        # deduction with a held one in a single `line_24 + (base - anchor)`.
        if bool(anchor.get("schedule_a_repriced", False)) != repriced:
            raise ScenarioError(
                "The base scenario was priced with Schedule A "
                f"{'re-priced' if anchor.get('schedule_a_repriced') else 'held'}"
                f", and this change {'re-prices' if repriced else 'holds'} it: "
                "a stack must price its deduction one way throughout. Re-run "
                "the stack from result.json.")

    # Resolved post-delta inputs (for stacking) — fold the delta into the set verbatim.
    resolved_inputs = dict(base_inputs)
    for k, v in deltas.items():
        resolved_inputs[k] = round(_coerce_numeric(resolved_inputs.get(k, 0), default=0) + v, 2)

    # Anchor BOTH the immediate base and the scenario to the original line 24 so the
    # rendered table is self-consistent for stacked runs (baseline + Δ == scenario).
    # For an unstacked run base_out == the zero-delta recompute, so base_anchored == line_24.
    base_anchored = round(anchor["line_24"]
                          + (base_out["total_federal_tax"] - anchor["baseline_recompute_total"]), 2)
    anchored = round(anchor["line_24"]
                     + (scen_out["total_federal_tax"] - anchor["baseline_recompute_total"]), 2)
    delta_income = round(scen_out["gross_income"] - base_out["gross_income"]
                         - (scen_out["taxable_ss"] - base_out["taxable_ss"]), 2)
    delta_tax = round(scen_out["total_federal_tax"] - base_out["total_federal_tax"], 2)
    diff = ScenarioDiff(
        delta_income=delta_income,
        delta_agi=round(scen_out["agi"] - base_out["agi"], 2),
        delta_taxable_income=round(scen_out["taxable_income"] - base_out["taxable_income"], 2),
        delta_total_tax=delta_tax,
        marginal_rate=(round(delta_tax / delta_income, 4) if delta_income else None),
        baseline_anchored_total_tax=base_anchored,
        anchored_scenario_total_tax=anchored,
        baseline_effective_rate=_eff_rate(base_anchored, base_out["agi"]),
        scenario_effective_rate=_eff_rate(anchored, scen_out["agi"]),
    )

    applied = list(base["applied_deltas"]) + [
        {"key": k, "amount": v} for k, v in deltas.items()
    ]
    original_inputs = extract_baseline_inputs(result)
    original_out = compute_scenario(original_inputs, {}, **rate_kwargs)
    state_effect = price_state_points(
        result, tax_law, year, filing_status, original_inputs, original_out,
        [(base_inputs, base_out), (resolved_inputs, scen_out)], profile=profile)
    if base_scenario is not None:
        prior = base_scenario.get("state_effect") or {}
        if (prior.get("modeled", False) != state_effect["modeled"]
                or prior.get("fingerprint") != state_effect["fingerprint"]):
            raise ScenarioError("State evidence/law changed or was absent in the saved scenario; re-run the stack from result.json")
    if state_effect["modeled"]:
        state_delta = round(state_effect["points"][1]["anchored_total_tax"]
                            - state_effect["points"][0]["anchored_total_tax"], 2)
        state_effect["delta_total_tax"] = state_delta
        state_effect["combined_delta_tax"] = round(delta_tax + state_delta, 2)
        state_effect["combined_marginal_rate"] = (
            round((delta_tax + state_delta) / delta_income, 4) if delta_income else None)
    return ScenarioReport(
        schema="tax-scenario/v1", name=name, tax_year=year,
        created=today.isoformat(), base=base["base_label"], filing_status=filing_status,
        applied_deltas=applied, anchor=anchor, inputs=resolved_inputs,
        base_outcome=_outcome(base_out), scenario_outcome=_outcome(scen_out), diff=diff,
        salt_cap_may_move=(not repriced and salt_cap_may_move(
            result, tax_law, year, filing_status, return_agi, priced)),
        medical_floor_may_move=(not repriced and medical_floor_may_move(
            result, return_agi, priced)),
        schedule_a_repriced=repriced,
        schedule_a_held=held_notes,
        state_effect=state_effect,
    )


def _report_to_dict(r: ScenarioReport) -> dict:
    return {
        "schema": r.schema, "name": r.name, "tax_year": r.tax_year, "created": r.created,
        "base": r.base, "filing_status": r.filing_status, "applied_deltas": r.applied_deltas,
        "anchor": r.anchor, "inputs": r.inputs,
        "base_outcome": vars(r.base_outcome), "scenario_outcome": vars(r.scenario_outcome),
        "diff": vars(r.diff), "salt_cap_may_move": r.salt_cap_may_move,
        "medical_floor_may_move": r.medical_floor_may_move,
        "schedule_a_repriced": r.schedule_a_repriced,
        "schedule_a_held": r.schedule_a_held,
        "state_effect": r.state_effect,
    }


def _pct(v) -> str:
    return "—" if v is None else f"{v:.1%}"


def _known_amount(value) -> "float | None":
    """A finite number, or None — for a gate where unknown means say it."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _known_non_negative(value) -> "float | None":
    """`_known_amount`, and not below zero: a negative tax or expense total is
    corrupt, and read as known it silenced the caveat it gates."""
    amount = _known_amount(value)
    return amount if amount is not None and amount >= 0 else None


def line_13c_claimed(result: dict) -> bool:
    """Whether the return claims §170(p)'s line-13c deduction, which only a
    standard-deduction filer can. It is the election the return actually
    made. Since AA23 `compute_schedule_a` weighs §170(p) in its own election,
    but a result.json written before that came from a by-hand tilt that could
    move a return to standard after `schedule_a.use_itemized` was recorded."""
    amount = _known_amount((result.get("form_1040", {}) or {}).get(
        "line_13c_charitable_non_itemizer"))
    return amount is not None and amount > 0


def _return_frame(result: dict, keystone_agi: float,
                  priced_agis: "list[float]") -> "tuple[float, list[float]]":
    """The return's own AGI, and each priced keystone AGI carried to it.

    The keystone models half the SE tax as its only adjustment, so its AGI
    sits off line 11 by every other line-10 adjustment — an IRA deduction, an
    HSA, student-loan interest. Its DIFFERENCES are right and its levels are
    not, so a priced AGI is line 11 plus how far the keystone moved from its
    recompute of the return itself, `keystone_agi`. Comparing levels dropped
    the caveat where it applied and showed it where it did not.
    """
    line_11 = _known_amount((result.get("form_1040", {}) or {}).get("line_11_agi"))
    anchor = keystone_agi if line_11 is None else line_11
    return anchor, [anchor + (a - keystone_agi) for a in priced_agis]


def _held_deduction_may_move(result: dict, keystone_agi: float,
                             priced_agis: "list[float]",
                             deduction_at: "Callable[[float], float] | None",
                             ) -> bool:
    """Whether a deduction the keystone holds at the return's can move anywhere
    a report prices: the shape both gates below share, each passing only
    `deduction_at`, the deduction at an AGI in the return's frame, or None
    where the return does not record enough to price it. Anything not known
    is True:

    - no `schedule_a`: the standard deduction, since the template includes
      the section only for an itemizer or a margin within $500, with nothing
      to price, so only a lower AGI can matter, by growing a deduction until
      itemizing wins;
    - a return with the section that took the standard deduction, or claims
      line 13c: the same, priced — only where the deduction would rise;
    - an itemizer: wherever the deduction differs from the return's.
    """
    return_agi, in_play = _return_frame(result, keystone_agi, priced_agis)
    lower = any(a < return_agi for a in in_play)
    sched = result.get("schedule_a")
    if not isinstance(sched, dict):
        return lower
    standard = sched.get("use_itemized") is False or line_13c_claimed(result)
    if deduction_at is None:
        return lower if standard else any(a != return_agi for a in in_play)
    at_return = deduction_at(return_agi)
    elsewhere = [deduction_at(a) for a in in_play]
    if standard:
        return any(d > at_return for d in elsewhere)
    return any(d != at_return for d in elsewhere)


def salt_cap_may_move(result: dict, tax_law: dict, tax_year: int,
                      filing_status: "str | None", keystone_agi: float,
                      priced_agis: "list[float]") -> bool:
    """Whether §164(b)(7)(B) can move the return's SALT deduction anywhere a
    report prices — the gate on `salt_phasedown_caveat`.

    It asks whether min(tax, cap) changes, not whether MAGI crosses the
    threshold: a cap at the floor throughout, or above the tax throughout,
    moves nothing however far MAGI goes. MAGI is an AGI in the return's frame
    plus its own add-back, `modified_agi` less line 11; the amount is the
    cache's, as the return used, or the statute's where the cache has none.
    Everything else is `_held_deduction_may_move`.
    """
    mfs = _full_status_key(filing_status, "SALT caveat") == "mfs"
    if not salt.phasedown_applies(tax_year):
        return False
    sched = result.get("schedule_a")
    sched = sched if isinstance(sched, dict) else {}
    salt_total = _known_non_negative(sched.get("salt_before_cap"))
    magi = _known_amount(sched.get("modified_agi"))
    line_11 = _known_amount((result.get("form_1040", {}) or {}).get("line_11_agi"))
    cfg = ((tax_law.get("federal", {}) or {}).get("salt_cap") or {})
    base = (_known_non_negative(cfg.get("mfs_amount" if mfs else "amount"))
            if isinstance(cfg, dict) else None)
    deduction_at: "Callable[[float], float] | None" = None
    if salt_total is not None and magi is not None and line_11 is not None:
        tax, added_back = salt_total, magi - line_11

        def salt_deduction(agi: float) -> float:
            return min(tax, salt.salt_cap(tax_year, mfs, base, agi + added_back))
        deduction_at = salt_deduction
    return _held_deduction_may_move(result, keystone_agi, priced_agis, deduction_at)


def medical_floor_may_move(result: dict, keystone_agi: float,
                           priced_agis: "list[float]") -> bool:
    """Whether the medical-expense floor, a share of AGI, can move the
    return's deduction anywhere a report prices — the gate on
    `medical_floor_caveat`.

    The keystone holds the return's line 12, and the part of Schedule A that
    moves with AGI in EVERY year is the medical floor: a delta that raises AGI
    shrinks the deduction and one that lowers it grows it. Priced on the gross
    `compute_schedule_a` records, by the engine's own rule. Everything else is
    `_held_deduction_may_move`.
    """
    sched = result.get("schedule_a")
    expenses = (_known_non_negative(sched.get("medical_expenses"))
                if isinstance(sched, dict) else None)
    deduction_at: "Callable[[float], float] | None" = None
    if expenses is not None:
        gross = expenses

        def medical_deduction(agi: float) -> float:
            return _medical_deductible(gross, agi)
        deduction_at = medical_deduction
    return _held_deduction_may_move(result, keystone_agi, priced_agis, deduction_at)


def reprice_outcomes(result: dict, rate_kwargs: dict, keystone_agi: float,
                     runs: "list[tuple[dict, dict]]", held: "list[dict]",
                     ) -> "tuple[list[dict], bool, list[str]]":
    """Price complete Schedule A inputs at every run's AGI, or hold all runs.

    Provisional QBI is computed before the §68 haircut, then final QBI is
    recomputed after it. An election change uses the recorded standard side
    rather than carrying an itemizer's offset into it. Legacy offsets whose
    regime cannot be identified still refuse a flip. Binding charitable caps
    require explicit confirmation of the engine's donation classifications.
    """
    sched = result.get("schedule_a")
    if not isinstance(sched, dict):
        return held, False, []
    inputs = sched.get("inputs")
    if not isinstance(inputs, dict):
        return held, False, []
    meta = result.get("meta", {}) or {}
    if (inputs.get("tax_year") != meta.get("tax_year")
            or _full_status_key(inputs.get("filing_status"), "Schedule A")
            != _full_status_key(meta.get("filing_status"), "return")):
        raise ScenarioError("Schedule A inputs must match the return's year and filing status")
    if (sched.get("charitable_carryforward", 0)
            and sched.get("charitable_limits_confirmed") is not True):
        return held, False, []
    brackets = rate_kwargs["brackets"]
    top = max(brackets, key=lambda b: b.get("rate", 0), default=None)
    top_min = _known_amount(top.get("min")) if isinstance(top, dict) else None
    if top_min is None:
        return held, False, []
    f = result.get("form_1040", {}) or {}
    line_12 = return_deduction(result)
    line_13c = _known_amount(f.get("line_13c_charitable_non_itemizer")) or 0.0
    return_agi, _ = _return_frame(result, keystone_agi, [])
    charity = inputs.get("non_itemizer_charitable")
    if charity is None and line_13c > 0:
        charity = line_13c

    def deductions(out: dict) -> tuple[float, float]:
        if (not out["use_itemized"] and inputs.get("charitable_cash")
                and out["non_itemizer_charitable_weighed"] is None):
            raise ScenarioError("A standard-deduction outcome needs the provisional §170(p) "
                                "amount; re-run /tax-compute to record it in Schedule A inputs.")
        return (out["itemized_total"] if out["use_itemized"] else out["standard_deduction"],
                0.0 if out["use_itemized"] else
                (out["non_itemizer_charitable_weighed"] or 0.0))

    def at(income: dict, delta: dict, raw: dict, offset: float = 0.0,
           regime: "bool | None" = None) -> tuple[dict, dict, float]:
        agi = return_agi + raw["agi"] - keystone_agi
        unreduced = rerun_schedule_a(inputs, agi, top_bracket_income=0.0,
                                    non_itemizer_charitable=charity)
        if (unreduced["charitable_carryforward"]
                and sched.get("charitable_limits_confirmed") is not True):
            raise ScenarioError("The projected charitable cap binds; confirm public-charity "
                                "cash and appreciated-property noncash gifts with /tax-compute.")
        d, c = deductions(unreduced)
        # The normalized input bundles lines 13b and 13c. Replace only 13c;
        # retain explicit below-AGI deltas once, in the caller's delta dict.
        below = _coerce_numeric(income.get("schedule_1a_deduction"), default=0) - line_13c
        provisional = compute_scenario(
            {**income, "schedule_1a_deduction": below + c}, delta,
            **{**rate_kwargs, "deduction": d + (offset if regime == unreduced["use_itemized"] else 0)})
        base = max(0.0, agi - provisional["qbi_deduction"] - below
                   - _coerce_numeric(delta.get("schedule_1a_deduction"), default=0) - top_min)
        out = rerun_schedule_a(inputs, agi, top_bracket_income=base,
                              non_itemizer_charitable=charity)
        d, c = deductions(out)
        if regime is not None and out["use_itemized"] != regime and offset != 0:
            raise ValueError("an unclassified line-12 offset cannot cross the election")
        applied = d + (offset if out["use_itemized"] == regime else 0.0)
        computed = compute_scenario(
            {**income, "schedule_1a_deduction": below + c}, delta,
            **{**rate_kwargs, "deduction": applied})
        return computed, out, d

    try:
        baseline_inputs = extract_baseline_inputs(result)
        raw_base = compute_scenario(baseline_inputs, {}, **rate_kwargs)
        _, original, original_d = at(baseline_inputs, {}, raw_base)
        offset = round(line_12 - original_d, 2)
        regime = original["use_itemized"]
        priced = [at(i, d, raw, offset, regime)
                  for (i, d), raw in zip(runs, held)]
    except ValueError:
        return held, False, []
    notes = list(dict.fromkeys(n for _, out, _ in priced for n in out["rerun_notes"]))
    return [out for out, _, _ in priced], True, notes


def schedule_a_caveat(repriced: bool, held_notes: "list[str]") -> "list[str]":
    """The deduction bullet the three re-pricing reports (/tax-scenario,
    /tax-roth-convert, /tax-harvest) print: re-priced
    from Schedule A's recorded inputs, with what that still held, or the
    return's line 12 held throughout."""
    if not repriced:
        return ["- The deduction is the return's own line 12, standard or "
                "itemized, held at the baseline."]
    return (["- The deduction is the return's own line 12, re-priced at each "
             "AGI from the inputs its Schedule A recorded: the medical floor, "
             "the SALT phasedown and confirmed charitable AGI caps "
             "(as gifts to public charities, as the return computed them) "
             "move with it, including the itemize-or-standard election and the "
             "top-bracket haircut. Legacy inputs that cannot price a change stay held."]
            + [f"- {note}" for note in held_notes])


def refuse_where_the_charity_cap_moves(result: dict, keystone_agi: float,
                                       priced_agis: "list[float]", *, repriced: bool = False) -> None:
    """Refuse wherever a charitable deduction capped at a share of AGI moves.

    §170(b) caps a gift at a share of AGI, and a carryforward above zero is the
    cap binding. Any priced AGI other than the return's moves that cap, and the
    keystone holds the deduction, so the figure reads high whichever way AGI
    goes: a lower AGI allows less of the gift, so a saving is smaller than
    shown, and a higher one allows more, so a cost is. Over-stating is
    AGENTS.md §4's hard error, which no caveat may stand in for, and pricing it
    needs the split between cash and appreciated gifts that the return does
    not record. So it stops.
    """
    sched = result.get("schedule_a")
    if not isinstance(sched, dict) or sched.get("charitable_carryforward") is None:
        return
    carry = sched["charitable_carryforward"]
    if (isinstance(carry, bool) or not isinstance(carry, (int, float))
            or not math.isfinite(carry) or carry < 0):
        raise ScenarioError(
            "result.json schedule_a.charitable_carryforward must be a finite, "
            f"non-negative number, got {type(carry).__name__}")
    if carry == 0 or (repriced and sched.get("charitable_limits_confirmed") is True):
        return
    return_agi, in_play = _return_frame(result, keystone_agi, priced_agis)
    if any(a != return_agi for a in in_play):
        raise ScenarioError(
            "The return's charitable deduction is capped at a share of AGI "
            "(schedule_a.charitable_carryforward is above zero), and this change "
            "moves AGI and with it the cap. The planning tools hold the "
            "deduction, so the figure would read high. Price the change on the "
            "return itself with /tax-compute.")


def medical_floor_sentence(effect: str) -> str:
    """The one wording of the medical-floor caveat: the three reports print it
    as a bullet, and /tax-ptet as a note. /tax-ptet's own sentence dropped the
    "If you itemize" and read as a statement of fact on a standard return."""
    return ("If you itemize, your medical-expense deduction is held at the "
            "baseline: its floor is a share of AGI, and that is not re-computed "
            f"here, so {effect} The filed /tax-compute return applies it exactly.")


def medical_floor_caveat(may_move: bool, effect: str) -> "list[str]":
    """The caveat a `compute_scenario` report owes wherever the medical floor
    can move the deduction; `medical_floor_may_move` decides where. Held
    without a word until AA20."""
    return [f"- {medical_floor_sentence(effect)}"] if may_move else []


def salt_phasedown_caveat(may_move: bool, effect: str) -> "list[str]":
    """The caveat a `compute_scenario` report owes wherever §164(b)(7)(B) can
    move the SALT cap; `salt_cap_may_move` decides where.

    The keystone holds deductions at the baseline, and since AA16 the return's
    cap moves with modified AGI, so a delta that raises it can cost more than
    the keystone says and one that lowers it can save more. Both under-state,
    which AGENTS.md §4 lets degrade with a banner and not in silence. Where
    the cap cannot move it would describe nothing, and a caveat about nothing
    teaches a reader to skip the ones that are real.
    """
    if not may_move:
        return []
    return ["- If you itemize, your SALT cap is held at the baseline: "
            "§164(b)(7)(B) lowers it as modified AGI rises past the year's "
            f"threshold, and that is not re-computed here, so {effect} The "
            "filed /tax-compute return applies it exactly."]


def render_markdown(r: ScenarioReport) -> str:
    d = r.diff
    deltas_str = ", ".join(f"{x['key']} {'+' if x['amount'] >= 0 else ''}{x['amount']:,.0f}"
                           for x in r.applied_deltas)
    lines = [f"# Tax Scenario \"{r.name}\" — TY{r.tax_year}", ""]

    lines += ["## What this models", "",
              f"- Base: `{r.base}`",
              f"- Applied delta(s): **{deltas_str}**",
              f"- Filing status: {r.filing_status or 'unknown'}",
              "- The federal recompute is a **simplified** model; the absolute scenario "
              "tax is anchored to your real `result.json` line 24 and only the *marginal "
              "effect* of the delta is modeled.", ""]

    lines += ["## Bottom line", "",
              "| Item | Baseline | Scenario | Δ |", "|---|---:|---:|---:|",
              f"| Total federal tax | {_money(d.baseline_anchored_total_tax)} | "
              f"{_money(d.anchored_scenario_total_tax)} | {_money(d.delta_total_tax)} |",
              f"| AGI | {_money(r.base_outcome.agi)} | {_money(r.scenario_outcome.agi)} | "
              f"{_money(d.delta_agi)} |",
              f"| Taxable income | {_money(r.base_outcome.taxable_income)} | "
              f"{_money(r.scenario_outcome.taxable_income)} | {_money(d.delta_taxable_income)} |",
              f"| Effective rate | {_pct(d.baseline_effective_rate)} | "
              f"{_pct(d.scenario_effective_rate)} | |", ""]

    lines += ["## Marginal cost", "",
              f"- Δ income modeled: **{_money(d.delta_income)}**",
              f"- Δ federal tax: **{_money(d.delta_total_tax)}**",
              f"- Marginal rate on this change: **{_pct(d.marginal_rate)}**", ""]

    if r.state_effect.get("modeled"):
        before, after = r.state_effect["points"]
        lines += ["## State and local effects", "",
                  "State liability is repriced independently from the confirmed return inputs. "
                  "Refundable credits, payments, use tax and penalties are excluded.", "",
                  "| Item | Baseline | Scenario | Δ |", "|---|---:|---:|---:|",
                  f"| {r.state_effect['state']} state/local liability | {_money(before['anchored_total_tax'])} | "
                  f"{_money(after['anchored_total_tax'])} | {_money(r.state_effect['delta_total_tax'])} |",
                  f"| Federal + state/local tax change | | | {_money(r.state_effect['combined_delta_tax'])} |", "",
                  f"Combined marginal rate: **{_pct(r.state_effect['combined_marginal_rate'])}**", ""]
    lines += ["## Caveats"]
    lines += [f"- {note}" for note in r.state_effect.get("limitations", [])]
    lines += [
              "- `capital_gains` deltas are treated as net long-term (preferential) gains; "
              "model a short-term gain with `other_income` instead. A net capital LOSS "
              "offsets ordinary income only up to $3,000/yr (excess carryforward not modeled).",
              "- Taxable Social Security is recomputed from confirmed regular-worksheet inputs. "
              "Gross benefits, tax-exempt interest, exclusion add-backs and non-SE adjustments "
              "stay fixed; the SE-tax deduction moves with modeled earnings. Lump-sum and "
              "combined IRA worksheets require separate review. The marginal-rate denominator "
              "excludes the additional benefits made taxable by the change.",
              "- OBBBA Schedule 1-A (line 13b) deductions are held at the baseline amount; a "
              "MAGI-increasing delta that would phase OUT more of the tips/overtime/car-loan/"
              "senior deduction near a threshold is not re-computed, so the marginal cost "
              "there may read slightly low.",
              "- Deducted self-employed §224 tips are removed from QBI using the recorded "
              "source split, held with Schedule 1-A; changes to its eligibility are not re-priced. The "
              "§70105 minimum QBI deduction (TY2026+ small active trade) is not applied; "
              "marginal pricing near a "
              "threshold may read slightly off. The filed /tax-compute return handles "
              "them exactly.",
              "- NIIT, IRMAA, and AMT thresholds are not flagged in v1 (see backlog #6)."]
    lines += schedule_a_caveat(r.schedule_a_repriced, r.schedule_a_held)
    lines += salt_phasedown_caveat(
        r.salt_cap_may_move, "a delta that raises modified AGI above the threshold can "
        "cost more than shown, and one that lowers it there can save more.")
    lines += medical_floor_caveat(
        r.medical_floor_may_move, "a delta that raises AGI can cost more than "
        "shown, and one that lowers it can save more.")
    lines += ["- This is a planning estimate, not a filed return.", ""]
    return "\n".join(lines) + "\n"


def _validate_name(name: str) -> str:
    """A scenario name must be a single safe path segment (no separators / traversal)."""
    if not name or name in (".", "..") or "/" in name or "\\" in name or name.startswith("."):
        raise ScenarioError(
            f"Invalid scenario name {redact(repr(name))}: must be a single path "
            "segment "
            "(letters, digits, '-', '_'), no separators or leading dot."
        )
    return name


def save_scenario(workspace: Path, name: str, data: dict) -> Path:
    """Write scenarios/<name>.json under workspace, chmod 600. Refuses any other target."""
    name = _validate_name(name)
    workspace = Path(workspace).resolve()
    scen_dir = (workspace / "scenarios").resolve()
    out_path = (scen_dir / f"{name}.json").resolve()
    # Force the write target under workspace/scenarios/ and never over a source-of-truth file.
    _validate_subpath(out_path, workspace)
    if out_path.parent != scen_dir:
        raise ScenarioError(
            f"Refusing to write outside scenarios/: {redact(out_path)}")
    if out_path.name in ("result.json", "taxpayer_profile.json"):
        raise ScenarioError("Refusing to overwrite a source-of-truth file.")
    scen_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # 600 on the file leaves the directory listing readable, and a scenario NAME
    # discloses on its own. The chmod is not redundant with `mode`: mkdir ignores
    # it for a directory that already exists, which is the usual case here. A
    # failure here (e.g. an ACL/NFS filesystem where chmod doesn't hold) fails
    # the write rather than continuing into a directory that could not be
    # secured — archive.py and tie_out.py hold the same line for the runtime
    # paths they create (AGENTS.md SECURITY §1).
    try:
        secure_path(scen_dir)
    except OSError as e:
        raise ScenarioError(
            f"Could not secure {redact(scen_dir)} (chmod 700): "
            f"{redact(e)}") from None
    # `mode` on os.open is a create-only argument — POSIX ignores it when
    # out_path already exists (a re-save over a scenario name that predates
    # this hardening, or one restored from a backup), so opening alone does
    # not guarantee 600 the way the comment used to claim. The explicit
    # secure_fd right after open, before any content is written, closes that
    # window instead of leaving new financial data written at the file's old
    # mode until the trailing secure_path below runs. secure_fd prefers
    # fchmod on the open descriptor (immune to the path being replaced in
    # between) where the platform has it; os.fchmod does not exist on
    # Windows, a supported platform (docs/contributing/agents-compat.md), so
    # it falls back there to the same PowerShell-ACL-based enforcement
    # secure_path uses everywhere else on that platform.
    content = json.dumps(data, indent=2, default=str)
    # ONE writer for every runtime artifact (V10, `secure_fs.atomic_write`).
    # This wrote IN PLACE: `O_TRUNC` on the real target, so an interrupted write
    # left a truncated scenario file under a name the next run reads as good.
    # The helper creates the temp owner-only before the first byte — the file
    # holds a projected return — renames it on a clean exit and removes it on
    # any failure.
    try:
        with atomic_write(out_path) as f:
            f.write(content)
    except OSError as e:
        raise ScenarioError(
            f"Could not write {redact(out_path)}: {redact(e)}") from e
    return out_path


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.scenario")
    parser.add_argument("--result", required=True, help="path to result.json")
    add_workspace_argument(parser)
    parser.add_argument("--year", required=True, help="current tax year")
    parser.add_argument("--name", default="scenario", help="scenario name (single path segment)")
    parser.add_argument("--base", default=None,
                        help="path to a prior scenarios/<name>.json to stack on (default: result.json)")
    parser.add_argument("--today", default=None,
                        help="override today's date as YYYY-MM-DD (testing only)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    parser.add_argument("--no-save", action="store_true", help="do not write scenarios/<name>.json")
    parser.add_argument("deltas", nargs="*", help="one or more KEY=AMOUNT deltas")
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

        base_scenario = None
        if args.base:
            base_path = _resolve_under(args.base, workspace)
            base_scenario = _read_json(base_path)

        deltas = parse_deltas(args.deltas)
        name = _validate_name(args.name)
        # fromisoformat raises ValueError on a malformed --today — surface it cleanly.
        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
    except (WorkspaceError, ScenarioError, RolloverError, ProjectionError,
            ValueError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    try:
        report = build_report(result, profile, tax_law, year, name, deltas,
                              base_scenario, today)
    except (ScenarioError, ProjectionError, ValueError) as e:
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
