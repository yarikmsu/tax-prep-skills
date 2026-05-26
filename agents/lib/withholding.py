#!/usr/bin/env python3
"""Federal withholding true-up for /tax-withholding.

Read-only: never writes result.json or taxpayer_profile.json. Reads the ACTUAL
computed liability from result.json (form_1040.line_24_total_tax), compares it to
YTD federal withholding + estimates (from projection_inputs.json) projected at the
current pace, and recommends a W-4 4(c) and/or 1040-ES schedule to close the gap.

Reuses projection.py for the safe-harbor / 4(c) / voucher math and rollover.py for
the prior-year baseline + path/year validation.
"""

import datetime
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.projection import (  # noqa: E402
    ProjectionError,
    Voucher,
    W4Adjustment,
    SafeHarbor,
    _money,
    _num,
    _prior_tax_agi,
    _read_json,
    compute_estimated_vouchers,
    compute_safe_harbor,
    compute_w4_4c,
    elapsed_fraction,
    load_prior_baseline,
    paychecks_remaining,
    parse_as_of,
    validate_pay_frequency,
)
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_subpath,
    _validate_year_arg,
)


class WithholdingError(Exception):
    """Raised when /tax-withholding cannot safely proceed."""


@dataclass(frozen=True)
class WithholdingInputs:
    ytd_withholding: float
    estimates_paid: float
    elapsed_fraction: float
    paychecks_remaining: int
    projected_full_year_withholding: float
    projected_paid: float
    pay_frequency: str


@dataclass(frozen=True)
class TrueUpTarget:
    name: str  # "safe_harbor" | "zero_balance"
    target_amount: float
    projected_paid: float
    shortfall: float
    covered: bool
    per_paycheck: float


@dataclass(frozen=True)
class TrueUpReport:
    year: int
    as_of: str
    pay_frequency: str
    liability: float
    baseline_source: "str | None"
    degraded: bool
    inputs: WithholdingInputs
    safe_harbor: SafeHarbor
    penalty_target: TrueUpTarget
    zero_target: TrueUpTarget
    vouchers_penalty: "list[Voucher]"
    vouchers_zero: "list[Voucher]"
    w4_penalty: W4Adjustment
    w4_zero: W4Adjustment


def build_inputs(proj_inputs: dict, as_of: datetime.date,
                 pay_frequency: str) -> WithholdingInputs:
    frac = elapsed_fraction(as_of)
    if frac <= 0:
        raise WithholdingError("elapsed fraction must be > 0")
    ytd = proj_inputs.get("ytd", {}) or {}
    ytd_wh = _num(ytd.get("federal_withholding"), "ytd.federal_withholding")
    est = proj_inputs.get("estimated_payments_made", {}) or {}
    estimates_paid = _num(est.get("federal"), "estimated_payments_made.federal")
    proj_wh = round(ytd_wh / frac, 2)
    projected_paid = round(proj_wh + estimates_paid, 2)
    return WithholdingInputs(
        ytd_withholding=round(ytd_wh, 2),
        estimates_paid=round(estimates_paid, 2),
        elapsed_fraction=round(frac, 4),
        paychecks_remaining=paychecks_remaining(pay_frequency, frac),
        projected_full_year_withholding=proj_wh,
        projected_paid=projected_paid,
        pay_frequency=pay_frequency,
    )


def read_liability(result: dict, year: int) -> float:
    """Return form_1040.line_24_total_tax from result.json, validating tax year."""
    meta = result.get("meta", {}) or {}
    result_year = meta.get("tax_year")
    if result_year != year:
        raise WithholdingError(
            f"result.json is for TY{result_year}, not TY{year}. "
            f"Pass --year {result_year} or recompute for {year}."
        )
    f1040 = result.get("form_1040", {}) or {}
    raw = f1040.get("line_24_total_tax")
    if raw is None:
        raise WithholdingError(
            "result.json has no form_1040.line_24_total_tax — run /tax-compute first."
        )
    liability = _num(raw, "line_24_total_tax")
    if liability < 0:
        raise WithholdingError(f"line_24_total_tax must not be negative, got {liability}.")
    return round(liability, 2)


def build_report(result: dict, proj_inputs: dict, prior, year: int,
                 as_of: datetime.date) -> TrueUpReport:
    pay_frequency = validate_pay_frequency(proj_inputs.get("pay_frequency", "biweekly"))
    liability = read_liability(result, year)
    inputs = build_inputs(proj_inputs, as_of, pay_frequency)

    filing_status = (result.get("meta", {}) or {}).get("filing_status")
    prior_tax, prior_agi = _prior_tax_agi(prior)
    safe_harbor = compute_safe_harbor(
        projected_tax=liability, prior_tax=prior_tax, prior_agi=prior_agi,
        filing_status=filing_status,
        withholding=inputs.projected_full_year_withholding,
        estimates_paid=inputs.estimates_paid,
    )

    w4_penalty = compute_w4_4c(safe_harbor.shortfall, inputs.paychecks_remaining)
    zero_shortfall = round(max(0.0, liability - inputs.projected_paid), 2)
    w4_zero = compute_w4_4c(zero_shortfall, inputs.paychecks_remaining)

    penalty_target = TrueUpTarget(
        name="safe_harbor", target_amount=safe_harbor.required_annual,
        projected_paid=inputs.projected_paid, shortfall=safe_harbor.shortfall,
        covered=safe_harbor.covered, per_paycheck=w4_penalty.per_paycheck,
    )
    zero_target = TrueUpTarget(
        name="zero_balance", target_amount=liability,
        projected_paid=inputs.projected_paid, shortfall=zero_shortfall,
        covered=zero_shortfall <= 0, per_paycheck=w4_zero.per_paycheck,
    )

    # Two voucher schedules, symmetric with the two W-4 4(c) figures: one to clear
    # the safe-harbor (penalty-avoidance) target, one to reach a zero balance. The
    # zero-balance schedule is the only actionable lever once paychecks run out.
    vouchers_penalty = compute_estimated_vouchers(
        safe_harbor.required_annual, inputs.projected_full_year_withholding,
        inputs.estimates_paid, as_of,
    )
    vouchers_zero = compute_estimated_vouchers(
        liability, inputs.projected_full_year_withholding,
        inputs.estimates_paid, as_of,
    )

    return TrueUpReport(
        year=year, as_of=as_of.strftime("%m-%d"), pay_frequency=pay_frequency,
        liability=liability,
        baseline_source=(f"archive/{year - 1}" if prior is not None else None),
        degraded=(prior is None), inputs=inputs, safe_harbor=safe_harbor,
        penalty_target=penalty_target, zero_target=zero_target,
        vouchers_penalty=vouchers_penalty, vouchers_zero=vouchers_zero,
        w4_penalty=w4_penalty, w4_zero=w4_zero,
    )


def render_markdown(r: TrueUpReport) -> str:
    i = r.inputs
    lines = [f"# Withholding True-Up — TY{r.year} (as of {r.as_of})", ""]

    lines += ["## Assumptions", ""]
    lines.append("- Liability target is the **actual computed** tax from result.json "
                 "(form_1040 line 24), not an annualized estimate.")
    lines.append(f"- Elapsed fraction of year: {i.elapsed_fraction:.0%}")
    lines.append(f"- Pay frequency: {r.pay_frequency} "
                 f"({i.paychecks_remaining} paychecks remaining)")
    lines.append("- Projected full-year withholding assumes the rest of the year "
                 "continues at the YTD withholding rate.")
    lines.append(f"- Prior-year baseline: {r.baseline_source or 'none — DEGRADED MODE'}")
    if r.degraded:
        lines.append("- **Degraded mode:** no prior-year return found. Safe harbor "
                     "uses the 90%-of-current-year test only.")
    if i.ytd_withholding == 0:
        lines.append("- **Note:** YTD federal withholding is $0 — verify it was entered.")
    lines.append("")

    lines += ["## Liability & payments", "",
              "| Item | Amount |", "|---|---:|",
              f"| Total tax (target) | {_money(r.liability)} |",
              f"| YTD federal withholding | {_money(i.ytd_withholding)} |",
              f"| Projected full-year withholding (current pace) | "
              f"{_money(i.projected_full_year_withholding)} |",
              f"| Estimates paid | {_money(i.estimates_paid)} |",
              f"| **Projected total paid** | {_money(i.projected_paid)} |", ""]

    lines += ["## Gap to targets", "",
              "| Target | Required | Projected paid | Shortfall | Status |",
              "|---|---:|---:|---:|---|"]
    for t, label in ((r.penalty_target, "Safe harbor (avoid penalty)"),
                     (r.zero_target, "Zero balance (full liability)")):
        lines.append(
            f"| {label} | {_money(t.target_amount)} | {_money(t.projected_paid)} | "
            f"{_money(t.shortfall)} | {'covered' if t.covered else 'underpaid'} |")
    lines.append("")

    lines += ["## Recommended actions", "",
              "Withholding is treated as paid evenly across the year, so a W-4 line "
              "4(c) bump on remaining paychecks can cure an underpayment retroactively "
              "— prefer it over uneven estimates when paychecks remain.", "",
              "### W-4 line 4(c) per-paycheck extra withholding",
              f"- To avoid penalty (safe harbor): **{_money(r.w4_penalty.per_paycheck)}"
              f"/paycheck** over {r.w4_penalty.paychecks_remaining} remaining paychecks",
              f"- To reach zero balance: **{_money(r.w4_zero.per_paycheck)}/paycheck**",
              "",
              "### 1040-ES voucher schedule (fallback)", "",
              "Per-quarter payments to reach each target. Use the zero-balance column "
              "when no paychecks remain and vouchers are the only lever.", "",
              "| Quarter | Due | To avoid penalty | To zero balance |",
              "|---|---|---:|---:|"]
    for vp, vz in zip(r.vouchers_penalty, r.vouchers_zero):
        lines.append(f"| Q{vp.quarter} | {vp.due_date} | {_money(vp.amount)} | "
                     f"{_money(vz.amount)} |")
    lines += ["",
              "## Caveats",
              "- This is a planning projection, not a filed return. Re-run after any "
              "material income or withholding change.",
              "- Federal only; state withholding true-up is not yet covered.", ""]
    return "\n".join(lines) + "\n"


def _report_to_dict(r: TrueUpReport) -> dict:
    return {
        "year": r.year, "as_of": r.as_of, "pay_frequency": r.pay_frequency,
        "liability": r.liability, "baseline_source": r.baseline_source,
        "degraded": r.degraded, "inputs": vars(r.inputs),
        "safe_harbor": vars(r.safe_harbor),
        "penalty_target": vars(r.penalty_target),
        "zero_target": vars(r.zero_target),
        "vouchers_penalty": [vars(v) for v in r.vouchers_penalty],
        "vouchers_zero": [vars(v) for v in r.vouchers_zero],
        "w4_penalty": vars(r.w4_penalty), "w4_zero": vars(r.w4_zero),
    }


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.withholding")
    parser.add_argument("--result", required=True, help="path to result.json")
    parser.add_argument("--inputs", required=True, help="path to projection_inputs.json")
    parser.add_argument("--workspace", required=True, help="current workspace directory")
    parser.add_argument("--year", required=True, help="current tax year")
    parser.add_argument("--as-of", dest="as_of", default=None, help="MM-DD (default: today)")
    parser.add_argument("--today", default=None,
                        help="override today's date as YYYY-MM-DD (testing only)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args(argv)

    try:
        year = _validate_year_arg(args.year)
        workspace = Path(args.workspace).resolve()
        result_path = _validate_subpath(Path(args.result).resolve(), workspace)
        inputs_path = _validate_subpath(Path(args.inputs).resolve(), workspace)
        result = _read_json(result_path)
        proj_inputs = _read_json(inputs_path)

        inputs_year = proj_inputs.get("tax_year")
        if inputs_year is not None and inputs_year != year:
            raise WithholdingError(
                f"projection_inputs.json is for TY{inputs_year}, not TY{year}."
            )

        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
        as_of_str = args.as_of or proj_inputs.get("as_of_date") or today.strftime("%m-%d")
        as_of = parse_as_of(as_of_str, year)
        if as_of > today:
            raise WithholdingError(
                f"as_of {as_of.isoformat()} is in the future (today {today.isoformat()})"
            )

        prior = load_prior_baseline(workspace / "archive" / str(year - 1), year - 1)
    except (WithholdingError, ProjectionError, RolloverError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    try:
        report = build_report(result, proj_inputs, prior, year, as_of)
    except (WithholdingError, ProjectionError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
