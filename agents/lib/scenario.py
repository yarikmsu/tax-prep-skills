#!/usr/bin/env python3
"""Scenario modeling for /tax-scenario.

Forks the computed baseline in result.json, applies one or more signed income
deltas, recomputes the FEDERAL tax effect, and reports the marginal cost and the
movement in AGI / taxable income / effective rate. Scenarios are stackable via
--base scenarios/<other>.json.

Never mutates result.json or taxpayer_profile.json. Writes ONLY under
workspace/scenarios/. Pure compute + markdown renderer + guarded writer + CLI.
Reuses tax_computations.compute_scenario (the keystone), projection.py helpers,
and rollover.py path/year validation.
"""

import datetime
import json
import sys
from dataclasses import dataclass
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
)
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_subpath,
    _validate_year_arg,
)
from agents.lib.secure_fs import atomic_write, redact, secure_path  # noqa: E402
from agents.lib.tax_computations import (  # noqa: E402
    SCENARIO_INPUT_KEYS,
    _coerce_numeric,
    compute_scenario,
    net_capital_gains,
)
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


# Netting logic lives in tax_computations.net_capital_gains so projection.py
# (which scenario.py imports FROM — importing scenario there would be circular)
# can reuse the identical character-preservation rules. This private alias is the
# stable name loss_harvest.py and the tests import.
_net_capital_gains = net_capital_gains


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
    return {
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
        # taxable Social Security (line 6b). Including taxable SS keeps a delta stacked
        # at the right marginal bracket for retirees. (The second-order effect — a delta
        # pushing MORE SS into the taxable range — is not modeled; see caveats.)
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
    }


def _num_ctc_children(profile: dict) -> int:
    deps = profile.get("dependents") or []
    return sum(1 for d in deps if isinstance(d, dict) and d.get("ctc_eligible"))


def _rate_kwargs(profile: dict, tax_law: dict, filing_status: "str | None") -> dict:
    """Build the compute_scenario rate kwargs from tax_law for this filing status."""
    full_key = _full_status_key(filing_status)
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
        "standard_deduction": _num(fstatus.get("standard_deduction"), "standard_deduction"),
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
    }


def _outcome(d: dict) -> Outcome:
    return Outcome(
        gross_income=d["gross_income"], adjustments=d["adjustments"], agi=d["agi"],
        taxable_income=d["taxable_income"], tax_before_credits=d["tax_before_credits"],
        ctc=d["ctc"], se_tax=d["se_tax"], additional_medicare=d["additional_medicare"],
        total_federal_tax=d["total_federal_tax"], used_qdcg=d["used_qdcg"],
    )


def _eff_rate(tax: float, agi: float) -> "float | None":
    # Undefined for non-positive AGI; also suppress a negative rate, which can
    # arise only in the degenerate case where modeled savings exceed the real tax.
    return round(tax / agi, 4) if (agi > 0 and tax >= 0) else None


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
    filing_status = ((result.get("meta", {}) or {}).get("filing_status")
                     or profile.get("filing_status"))
    rate_kwargs = _rate_kwargs(profile, tax_law, filing_status)
    base = resolve_base(result, base_scenario, year, filing_status)
    base_inputs = base["inputs"]

    # Deltas are applied verbatim (no derived adjustments), so every delta is exactly
    # reversible when stacking. `qualified_dividends` is the qualified SPLIT of ordinary
    # dividends — it never changes gross on its own; the keystone clamps it to <=
    # ordinary. To add dividend income, raise `ordinary_dividends` (with an optional
    # `qualified_dividends` portion), mirroring 1040 lines 3b/3a.

    # Outcome of the immediate base (zero further delta) and of the new scenario.
    base_out = compute_scenario(base_inputs, {}, **rate_kwargs)
    scen_out = compute_scenario(base_inputs, deltas, **rate_kwargs)

    # Anchor is constant across a stack: original result.json line 24 + the zero-delta
    # recompute of the ORIGINAL inputs. For base=result.json we establish it here.
    if base["anchor"] is None:
        line24 = _num(_get(result.get("form_1040", {}) or {}, "line_24_total_tax"), "line_24")
        anchor = {"line_24": round(line24, 2),
                  "baseline_recompute_total": base_out["total_federal_tax"]}
    else:
        anchor = base["anchor"]

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
    delta_income = round(scen_out["gross_income"] - base_out["gross_income"], 2)
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
    return ScenarioReport(
        schema="tax-scenario/v1", name=name, tax_year=year,
        created=today.isoformat(), base=base["base_label"], filing_status=filing_status,
        applied_deltas=applied, anchor=anchor, inputs=resolved_inputs,
        base_outcome=_outcome(base_out), scenario_outcome=_outcome(scen_out), diff=diff,
    )


def _report_to_dict(r: ScenarioReport) -> dict:
    return {
        "schema": r.schema, "name": r.name, "tax_year": r.tax_year, "created": r.created,
        "base": r.base, "filing_status": r.filing_status, "applied_deltas": r.applied_deltas,
        "anchor": r.anchor, "inputs": r.inputs,
        "base_outcome": vars(r.base_outcome), "scenario_outcome": vars(r.scenario_outcome),
        "diff": vars(r.diff),
    }


def _pct(v) -> str:
    return "—" if v is None else f"{v:.1%}"


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

    lines += ["## Caveats",
              "- Federal only — state tax effect is not modeled in v1.",
              "- `capital_gains` deltas are treated as net long-term (preferential) gains; "
              "model a short-term gain with `other_income` instead. A net capital LOSS "
              "offsets ordinary income only up to $3,000/yr (excess carryforward not modeled).",
              "- Taxable Social Security is taken from the baseline as fixed; a delta that "
              "would push more SS into the taxable range is not re-computed.",
              "- OBBBA Schedule 1-A (line 13b) deductions are held at the baseline amount; a "
              "MAGI-increasing delta that would phase OUT more of the tips/overtime/car-loan/"
              "senior deduction near a threshold is not re-computed, so the marginal cost "
              "there may read slightly low.",
              "- Self-employed §224 tips are not removed from the QBI base, the §63(f) "
              "age-65/blind standard-deduction add-on is not threaded, and the §70105 "
              "minimum QBI deduction (TY2026+ small active trade) is not applied (all held "
              "from the baseline result.json); marginal pricing near a threshold may read "
              "slightly off. The filed /tax-compute return handles them exactly.",
              "- MFS and HOH use the single-status capital-gains brackets (a toolkit-wide "
              "tax_law simplification); MFS gains near the 15%/20% boundary may read low.",
              "- NIIT, IRMAA, and AMT thresholds are not flagged in v1 (see backlog #6).",
              "- This is a planning estimate, not a filed return.", ""]
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
