#!/usr/bin/env python3
"""Mid-year tax projection for /tax-project.

Read-only: never writes result.json or taxpayer_profile.json. Pure compute
functions + a markdown renderer + a CLI. Reuses tax_computations.py for tax
math and rollover.py for the prior-year baseline + path/year validation.
"""

import calendar
import datetime
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_subpath,
    _validate_year_arg,
    load_prior,
)
from agents.lib.tax_computations import (  # noqa: E402
    CAPITAL_LOSS_LIMIT,
    CAPITAL_LOSS_LIMIT_MFS,
    _coerce_numeric,
    compute_additional_medicare,
    compute_bracket_tax,
    compute_child_tax_credit,
    compute_niit,
    compute_qbi_deduction,
    compute_qdcg_tax,
    compute_se_tax,
    net_capital_gains,
)


class ProjectionError(Exception):
    """Raised when /tax-project cannot safely proceed."""


PAY_PERIODS = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}
_MFJ_LIKE = {"MFJ", "QSS"}
ES_DUE_DATES = [(1, (4, 15)), (2, (6, 15)), (3, (9, 15)), (4, (1, 15))]  # Q4 due Jan 15 next year


def _full_status_key(filing_status: str | None) -> str:
    fs = (filing_status or "").upper()
    return {"MFJ": "mfj", "QSS": "mfj", "MFS": "mfs", "HOH": "hoh", "SINGLE": "single"}.get(fs, "single")


def _binary_status_key(filing_status: str | None) -> str:
    return "mfj" if (filing_status or "").upper() in _MFJ_LIKE else "single"


def _num(value, field: str = "") -> float:
    """Coerce to float; None->0.0; reject bool and unparseable strings."""
    if value is None:
        return 0.0
    try:
        n = _coerce_numeric(value, default=None)
    except TypeError:
        raise ProjectionError(f"{field or 'value'} must be numeric, got bool")
    if n is None:
        raise ProjectionError(f"{field or 'value'} is not numeric: {value!r}")
    return n


@dataclass(frozen=True)
class ProjectedInputs:
    wages: float
    federal_withholding: float
    state_withholding: float
    medicare_wages: float
    medicare_withheld: float
    ss_wages: float
    capital_gains: float  # legacy single figure — treated as LONG-term
    qualified_dividends: float
    ordinary_dividends: float
    taxable_interest: float
    self_employment_net: float
    other_income: float
    estimated_fed_paid: float
    estimated_state_paid: float
    elapsed_fraction: float
    paychecks_remaining: int
    # ST/LT split (preferred over the legacy combined field): short-term gains
    # are ORDINARY-rate income — routing them through the preferential QDCG
    # buckets understates the projection and the safe-harbor target.
    capital_gains_short: float = 0.0
    capital_gains_long: float = 0.0


@dataclass(frozen=True)
class FederalProjection:
    gross_income: float
    adjustments: float
    agi: float
    taxable_income: float
    tax_before_credits: float
    ctc: float
    se_tax: float
    additional_medicare: float
    total_tax: float
    withholding: float
    estimates_paid: float
    balance: float  # >0 owed, <0 refund
    used_qdcg: bool
    niit: float = 0.0
    qbi_over_threshold: bool = False


@dataclass(frozen=True)
class StateProjection:
    code: str
    taxable_income: float
    tax: float
    withholding: float
    estimates_paid: float
    balance: float


@dataclass(frozen=True)
class SafeHarbor:
    pct90_current: float
    prior_tax: float | None
    prior_factor: float | None
    prior_based: float | None
    required_annual: float
    withholding: float
    estimates_paid: float
    shortfall: float
    covered: bool
    degraded: bool


@dataclass(frozen=True)
class W4Adjustment:
    shortfall: float
    paychecks_remaining: int
    per_paycheck: float


@dataclass(frozen=True)
class Voucher:
    quarter: int
    due_date: str
    amount: float


@dataclass(frozen=True)
class ProjectionReport:
    year: int
    as_of: str
    pay_frequency: str
    baseline_source: str | None
    degraded: bool
    proj: ProjectedInputs
    federal: FederalProjection
    state: "StateProjection | None"
    safe_harbor: SafeHarbor
    vouchers: "list[Voucher]"
    w4_penalty: W4Adjustment
    w4_zero: W4Adjustment


def validate_pay_frequency(pay_frequency: str) -> str:
    if pay_frequency not in PAY_PERIODS:
        raise ProjectionError(
            f"pay_frequency must be one of {sorted(PAY_PERIODS)}, got {pay_frequency!r}"
        )
    return pay_frequency


def parse_as_of(as_of_str: str, year: int) -> datetime.date:
    """Parse 'MM-DD' into a date in `year`. Rejects Jan 1 and impossible dates."""
    if not isinstance(as_of_str, str):
        raise ProjectionError(f"as_of must be a 'MM-DD' string, got {as_of_str!r}")
    parts = as_of_str.split("-")
    if len(parts) != 2:
        raise ProjectionError(f"as_of must be 'MM-DD', got {as_of_str!r}")
    try:
        month, day = int(parts[0]), int(parts[1])
        d = datetime.date(year, month, day)
    except ValueError:
        raise ProjectionError(f"as_of is not a real calendar date: {as_of_str!r}")
    if month == 1 and day == 1:
        raise ProjectionError("as_of must be after Jan 1 — no YTD data to annualize")
    return d


def _days_in_year(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def elapsed_fraction(as_of: datetime.date) -> float:
    return as_of.timetuple().tm_yday / _days_in_year(as_of.year)


def paychecks_remaining(pay_frequency: str, fraction: float) -> int:
    total = PAY_PERIODS[validate_pay_frequency(pay_frequency)]
    return max(0, round(total * (1 - fraction)))


def annualize(ytd: dict, as_of: datetime.date, pay_frequency: str,
              overrides: dict, recurring: dict, estimates: dict) -> ProjectedInputs:
    frac = elapsed_fraction(as_of)
    if frac <= 0:
        raise ProjectionError("elapsed fraction must be > 0")
    ytd = ytd or {}
    overrides = overrides or {}
    recurring = recurring or {}
    estimates = estimates or {}

    def ann(key):
        return round(_num(ytd.get(key), key) / frac, 2)

    base_wages = ann("wages")
    wages = round(base_wages + _num(overrides.get("year_end_bonus"), "year_end_bonus")
                  + _num(overrides.get("rsu_vesting"), "rsu_vesting"), 2)
    ytd_medicare = _num(ytd.get("medicare_wages"), "medicare_wages")
    medicare_wages = round(ytd_medicare / frac, 2) if ytd_medicare else wages

    cg_legacy = _num(overrides.get("capital_gains"), "capital_gains")
    cg_short = _num(overrides.get("capital_gains_short"), "capital_gains_short")
    cg_long = _num(overrides.get("capital_gains_long"), "capital_gains_long")
    if cg_legacy and (cg_short or cg_long):
        raise ProjectionError(
            "forecast_overrides has both the legacy 'capital_gains' and the "
            "'capital_gains_short'/'capital_gains_long' split — use one or the "
            "other (the legacy field is treated as long-term)."
        )

    return ProjectedInputs(
        wages=wages,
        federal_withholding=ann("federal_withholding"),
        state_withholding=ann("state_withholding"),
        medicare_wages=medicare_wages,
        medicare_withheld=ann("medicare_withheld"),
        ss_wages=ann("ss_wages"),
        capital_gains=cg_legacy,
        capital_gains_short=cg_short,
        capital_gains_long=cg_long,
        qualified_dividends=_num(recurring.get("qualified_dividends"), "qualified_dividends"),
        ordinary_dividends=_num(recurring.get("ordinary_dividends"), "ordinary_dividends"),
        taxable_interest=_num(recurring.get("taxable_interest"), "taxable_interest"),
        self_employment_net=_num(overrides.get("self_employment_net"), "self_employment_net"),
        other_income=_num(overrides.get("other_income"), "other_income"),
        estimated_fed_paid=_num(estimates.get("federal"), "estimated.federal"),
        estimated_state_paid=_num(estimates.get("state"), "estimated.state"),
        elapsed_fraction=round(frac, 4),
        paychecks_remaining=paychecks_remaining(pay_frequency, frac),
    )


def project_federal_tax(proj: ProjectedInputs, profile: dict, tax_law: dict) -> FederalProjection:
    fs = profile.get("filing_status")
    full_key = _full_status_key(fs)
    bin_key = _binary_status_key(fs)
    fed = tax_law.get("federal", {}) or {}
    fstatus = (fed.get("filing_statuses", {}) or {}).get(full_key, {}) or {}
    brackets = fstatus.get("brackets") or []
    if not brackets:
        raise ProjectionError(
            f"No federal brackets for status '{full_key}' in tax_law. Run /tax-setup to fetch."
        )
    std = _num(fstatus.get("standard_deduction"), "standard_deduction")

    # ST/LT netting with character preservation (shared net_capital_gains). The
    # legacy combined `capital_gains` field is treated as LONG-term; short-term
    # gains are ordinary-rate income and must not enter the preferential bucket.
    net_st = proj.capital_gains_short
    net_lt = proj.capital_gains_long + proj.capital_gains
    cap_input, ordinary_short = net_capital_gains(net_st, net_lt)
    # A net capital LOSS offsets ordinary income only up to the annual limit
    # ($3,000; $1,500 MFS); the excess carries forward (not modeled here).
    loss_limit = CAPITAL_LOSS_LIMIT_MFS if full_key == "mfs" else CAPITAL_LOSS_LIMIT
    cap_gain_in_gross = cap_input if cap_input >= 0 else max(cap_input, -loss_limit)

    if proj.self_employment_net > 0:
        se = compute_se_tax(
            proj.self_employment_net,
            ss_wage_base=(fed.get("social_security", {}) or {}).get("wage_base"),
            w2_ss_wages=proj.ss_wages,
        )
    else:
        se = {"se_tax": 0.0, "deductible_half": 0.0, "se_earnings": 0.0}

    gross = (proj.wages + cap_gain_in_gross + ordinary_short + proj.ordinary_dividends
             + proj.taxable_interest + proj.other_income + proj.self_employment_net)
    adjustments = se["deductible_half"]
    agi = max(0.0, gross - adjustments)

    net_ltcg = max(0.0, cap_input)
    # QBI needs taxable income BEFORE QBI and net capital gain (qualified
    # dividends + net LTCG) for the Form 8995 overall cap — so it is computed
    # after AGI, mirroring compute_scenario. The fetched §199A threshold is
    # passed so the over-threshold warning (8995-A wage/UBIA/SSTB limits not
    # modeled → deduction may be overstated) surfaces in the report instead of
    # silently understating projected tax for SSTB/wage-limited taxpayers.
    ti_before_qbi = max(0.0, agi - std)
    qbi_over_threshold = False
    if proj.self_employment_net > 0:
        qbi_res = compute_qbi_deduction(
            proj.self_employment_net, 0.0, rate=fed.get("qbi_deduction_rate", 0.20),
            taxable_income_before_qbi=ti_before_qbi,
            net_capital_gain=proj.qualified_dividends + net_ltcg,
            threshold=(fed.get("qbi_threshold") or {}).get(bin_key),
        )
        qbi = qbi_res["total_deduction"]
        qbi_over_threshold = bool(qbi_res.get("over_threshold"))
    else:
        qbi = 0.0
    taxable_income = max(0.0, ti_before_qbi - qbi)

    cg = fed.get("capital_gains_rates", {}) or {}
    cg_zero = (cg.get("zero_pct_limit") or {}).get(bin_key)
    cg_fifteen = (cg.get("fifteen_pct_limit") or {}).get(bin_key)
    if (proj.qualified_dividends or net_ltcg) and cg_zero is not None and cg_fifteen is not None:
        qd = compute_qdcg_tax(taxable_income, proj.qualified_dividends, net_ltcg,
                              brackets, cg_zero, cg_fifteen)
        tax_before_credits = qd["final_tax"]
        used_qdcg = qd["used_qdcg_worksheet"]
    else:
        tax_before_credits, _ = compute_bracket_tax(taxable_income, brackets)
        used_qdcg = False

    deps = profile.get("dependents") or []
    n_ctc = sum(1 for d in deps if isinstance(d, dict) and d.get("ctc_eligible"))
    ctc_cfg = fed.get("child_tax_credit", {}) or {}
    if n_ctc and ctc_cfg.get("amount_per_child"):
        ctc = compute_child_tax_credit(
            n_ctc, ctc_cfg["amount_per_child"], agi,
            (ctc_cfg.get("phase_out_start") or {}).get(bin_key) or 0,
        )["credit_allowed"]
    else:
        ctc = 0.0

    addl_cfg = fed.get("additional_medicare_tax", {}) or {}
    threshold_map = addl_cfg.get("threshold") or {}
    addl_threshold = threshold_map.get(full_key) or threshold_map.get(bin_key)
    medicare_wages = proj.medicare_wages or proj.wages
    if addl_threshold is not None:
        addl = compute_additional_medicare(
            medicare_wages, se["se_earnings"], addl_threshold,
            proj.medicare_withheld, rate=addl_cfg.get("rate", 0.009),
        )["additional_tax"]
    else:
        addl = 0.0

    # NIIT (Form 8960): 3.8% of the lesser of net investment income and MAGI
    # (≈AGI) over the threshold. Omitting it understated the projected total —
    # and therefore the safe-harbor target — for high earners with investment
    # income. Degrades to $0 when tax_law has no niit block (threshold None).
    niit_cfg = fed.get("niit", {}) or {}
    niit_thr_map = niit_cfg.get("threshold") or {}
    niit_threshold = niit_thr_map.get(full_key) or niit_thr_map.get(bin_key)
    # Form 8960 line 5a is the net gain/loss AS INCLUDED IN AGI — a net capital
    # loss reduces NII by the capped allowed amount (-$3K/-$1.5K-MFS), it is not
    # floored at zero. cap_gain_in_gross + ordinary_short is exactly that figure
    # (full combined gain when positive, capped loss when negative);
    # compute_niit itself returns 0 when total NII goes non-positive.
    nii = (proj.taxable_interest + proj.ordinary_dividends
           + cap_gain_in_gross + ordinary_short)
    niit = compute_niit(agi, nii, niit_threshold, rate=niit_cfg.get("rate", 0.038) or 0.038)

    total_tax = max(0.0, tax_before_credits - ctc) + se["se_tax"] + addl + niit
    balance = round(total_tax - proj.federal_withholding - proj.estimated_fed_paid, 2)

    return FederalProjection(
        gross_income=round(gross, 2), adjustments=round(adjustments, 2), agi=round(agi, 2),
        taxable_income=round(taxable_income, 2), tax_before_credits=round(tax_before_credits, 2),
        ctc=round(ctc, 2), se_tax=round(se["se_tax"], 2), additional_medicare=round(addl, 2),
        total_tax=round(total_tax, 2), withholding=round(proj.federal_withholding, 2),
        estimates_paid=round(proj.estimated_fed_paid, 2), balance=balance, used_qdcg=used_qdcg,
        niit=round(niit, 2), qbi_over_threshold=qbi_over_threshold,
    )


def compute_safe_harbor(projected_tax: float, prior_tax: float | None, prior_agi: float | None,
                        filing_status: str | None, withholding: float,
                        estimates_paid: float) -> SafeHarbor:
    pct90 = round(0.90 * projected_tax, 2)
    degraded = prior_tax is None
    if degraded:
        required = pct90
        prior_factor = None
        prior_based = None
    else:
        threshold = 75000 if (filing_status or "").upper() == "MFS" else 150000
        prior_factor = 1.10 if (prior_agi or 0) > threshold else 1.00
        prior_based = round(prior_factor * prior_tax, 2)
        required = round(min(pct90, prior_based), 2)
    paid = round(withholding + estimates_paid, 2)
    shortfall = round(max(0.0, required - paid), 2)
    return SafeHarbor(
        pct90_current=pct90, prior_tax=prior_tax, prior_factor=prior_factor,
        prior_based=prior_based, required_annual=required, withholding=round(withholding, 2),
        estimates_paid=round(estimates_paid, 2), shortfall=shortfall,
        covered=shortfall <= 0, degraded=degraded,
    )


def compute_w4_4c(shortfall: float, paychecks_remaining: int) -> W4Adjustment:
    if paychecks_remaining <= 0:
        per = 0.0
    else:
        per = round(max(0.0, shortfall) / paychecks_remaining, 2)
    return W4Adjustment(shortfall=round(shortfall, 2),
                        paychecks_remaining=paychecks_remaining, per_paycheck=per)


def compute_estimated_vouchers(required_annual: float, withholding: float, paid: float,
                               as_of: datetime.date) -> list:
    shortfall = max(0.0, required_annual - withholding - paid)
    open_q = []
    for q, (m, d) in ES_DUE_DATES:
        if q == 4:
            open_q.append((4, f"{as_of.year + 1}-01-15"))
            continue
        due = datetime.date(as_of.year, m, d)
        if due >= as_of:
            open_q.append((q, due.isoformat()))
    if not open_q:
        open_q = [(4, f"{as_of.year + 1}-01-15")]
    if shortfall <= 0:
        return [Voucher(q, dd, 0.0) for q, dd in open_q]
    per = round(shortfall / len(open_q), 2)
    return [Voucher(q, dd, per) for q, dd in open_q]


def _prior_tax_agi(prior):
    """Extract (prior_total_tax, prior_agi) from a PriorReturn-like object, or (None, None)."""
    if prior is None:
        return None, None
    f1040 = (getattr(prior, "result", {}) or {}).get("form_1040", {}) or {}
    tax = f1040.get("line_24_total_tax")
    agi = f1040.get("line_11_agi")
    return (float(tax) if isinstance(tax, (int, float)) else None,
            float(agi) if isinstance(agi, (int, float)) else None)


def build_report(inputs: dict, profile: dict, tax_law: dict, prior,
                 year: int, as_of: datetime.date) -> ProjectionReport:
    pay_frequency = validate_pay_frequency(inputs.get("pay_frequency", "biweekly"))
    proj = annualize(inputs.get("ytd", {}), as_of, pay_frequency,
                     inputs.get("forecast_overrides", {}),
                     inputs.get("recurring_from_prior", {}),
                     inputs.get("estimated_payments_made", {}))
    fs = profile.get("filing_status")
    federal = project_federal_tax(proj, profile, tax_law)
    state = project_state_tax(proj, prior, tax_law, fs)

    prior_tax, prior_agi = _prior_tax_agi(prior)
    safe_harbor = compute_safe_harbor(
        federal.total_tax, prior_tax, prior_agi, fs,
        withholding=federal.withholding, estimates_paid=federal.estimates_paid)

    vouchers = compute_estimated_vouchers(
        safe_harbor.required_annual, federal.withholding, federal.estimates_paid, as_of)

    full_shortfall = max(0.0, federal.total_tax - federal.withholding - federal.estimates_paid)
    w4_penalty = compute_w4_4c(safe_harbor.shortfall, proj.paychecks_remaining)
    w4_zero = compute_w4_4c(full_shortfall, proj.paychecks_remaining)

    return ProjectionReport(
        year=year, as_of=as_of.strftime("%m-%d"), pay_frequency=pay_frequency,
        baseline_source=(f"archive/{year - 1}" if prior is not None else None),
        degraded=(prior is None), proj=proj, federal=federal, state=state,
        safe_harbor=safe_harbor, vouchers=vouchers, w4_penalty=w4_penalty, w4_zero=w4_zero)


def _money(v) -> str:
    if v is None:
        return "—"
    body = f"${abs(v):,.0f}"
    return f"({body})" if v < 0 else body


def render_markdown(r: ProjectionReport) -> str:
    f = r.federal
    sh = r.safe_harbor
    lines = [f"# Mid-Year Tax Projection — TY{r.year} (as of {r.as_of})", ""]

    lines += ["## Assumptions", ""]
    lines.append(f"- Elapsed fraction of year: {r.proj.elapsed_fraction:.0%}")
    lines.append(f"- Pay frequency: {r.pay_frequency} ({r.proj.paychecks_remaining} paychecks remaining)")
    lines.append("- Steady wages and withholding are annualized from YTD (assumes the rest of the "
                 "year continues at the YTD rate); bonuses, vesting, and capital gains are taken "
                 "as the full-year amounts you entered.")
    lines.append(f"- Prior-year baseline: {r.baseline_source or 'none — DEGRADED MODE'}")
    if r.degraded:
        lines.append("- **Degraded mode:** no prior-year return found. Safe harbor uses the "
                     "90%-of-current-year test only; recurring income defaulted to 0.")
    if r.proj.federal_withholding == 0:
        lines.append("- **Note:** projected federal withholding is $0 — verify YTD withholding was entered.")
    lines.append("")

    lines += ["## Projected full-year federal", "",
              "| Item | Amount |", "|---|---:|",
              f"| Gross income | {_money(f.gross_income)} |",
              f"| Adjustments | {_money(f.adjustments)} |",
              f"| AGI | {_money(f.agi)} |",
              f"| Taxable income | {_money(f.taxable_income)} |",
              f"| Tax before credits{' (QDCG worksheet)' if f.used_qdcg else ''} | {_money(f.tax_before_credits)} |",
              f"| Child Tax Credit | {_money(-f.ctc)} |",
              f"| SE tax | {_money(f.se_tax)} |",
              f"| Additional Medicare | {_money(f.additional_medicare)} |",
              f"| NIIT (Form 8960) | {_money(f.niit)} |",
              f"| **Total tax** | {_money(f.total_tax)} |",
              f"| Withholding | {_money(-f.withholding)} |",
              f"| Estimates paid | {_money(-f.estimates_paid)} |",
              f"| **Projected balance** | {_money(f.balance)} {'(refund)' if f.balance < 0 else '(owed)'} |",
              ""]

    lines += ["## Safe harbor analysis", "",
              "| Test | Amount |", "|---|---:|",
              f"| 90% of projected current-year tax | {_money(sh.pct90_current)} |"]
    if not sh.degraded:
        lines.append(f"| {sh.prior_factor:.0%} of prior-year tax | {_money(sh.prior_based)} |")
    lines += [f"| **Required annual payment** | {_money(sh.required_annual)} |",
              f"| Withholding + estimates paid | {_money(sh.withholding + sh.estimates_paid)} |",
              f"| **Shortfall** | {_money(sh.shortfall)} |",
              f"| Status | {'covered' if sh.covered else 'underpaid'} |", ""]

    lines += ["## Recommended actions", "",
              "### 1040-ES voucher schedule", "",
              "| Quarter | Due | Amount |", "|---|---|---:|"]
    for v in r.vouchers:
        lines.append(f"| Q{v.quarter} | {v.due_date} | {_money(v.amount)} |")
    lines += ["",
              "### W-4 line 4(c) per-paycheck extra withholding",
              f"- To avoid penalty (safe harbor): **{_money(r.w4_penalty.per_paycheck)}/paycheck** "
              f"over {r.w4_penalty.paychecks_remaining} remaining paychecks",
              f"- To reach zero balance: **{_money(r.w4_zero.per_paycheck)}/paycheck**", ""]

    lines += ["## State projection", ""]
    if r.state is None:
        lines.append("_Skipped — no prior-year state baseline or no state brackets in tax_law._")
    else:
        s = r.state
        lines += ["| Item | Amount |", "|---|---:|",
                  f"| State ({s.code}) taxable income | {_money(s.taxable_income)} |",
                  f"| State tax | {_money(s.tax)} |",
                  f"| State withholding | {_money(-s.withholding)} |",
                  f"| **State balance** | {_money(s.balance)} {'(refund)' if s.balance < 0 else '(owed)'} |"]
    lines += ["",
              "## Caveats",
              "- This is a projection, not a filed return. Re-run after any material income change.",
              "- State estimated-payment scheduling is informational; state rules vary."]
    if f.qbi_over_threshold:
        lines.append(
            "- **QBI over the §199A threshold:** the Form 8995-A W-2-wage/UBIA limits "
            "and SSTB phase-out are NOT modeled — the projected QBI deduction may be "
            "overstated (an SSTB phases to $0), understating projected tax and the "
            "safe-harbor target. Verify against Form 8995-A.")
    lines.append("")
    return "\n".join(lines) + "\n"


def _report_to_dict(r: ProjectionReport) -> dict:
    return {
        "year": r.year, "as_of": r.as_of, "pay_frequency": r.pay_frequency,
        "baseline_source": r.baseline_source, "degraded": r.degraded,
        "proj": vars(r.proj), "federal": vars(r.federal),
        "state": (vars(r.state) if r.state is not None else None),
        "safe_harbor": vars(r.safe_harbor),
        "vouchers": [vars(v) for v in r.vouchers],
        "w4_penalty": vars(r.w4_penalty), "w4_zero": vars(r.w4_zero),
    }


def load_prior_baseline(archive_dir, prior_year: int):
    """Return PriorReturn for archive_dir, or None if the directory is absent
    (degraded mode). Re-raises a year mismatch as ProjectionError."""
    p = Path(archive_dir)
    if not p.exists():
        return None
    try:
        return load_prior(p, prior_year)
    except RolloverError as e:
        raise ProjectionError(str(e)) from e


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise ProjectionError(f"{path} does not exist")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ProjectionError(f"{path.name} is malformed: {e}")


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.projection")
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
        # --workspace is trusted-by-contract (the taxpayer's own dir); --inputs is
        # constrained to live under it so a stray/relative path can't escape the workspace.
        workspace = Path(args.workspace).resolve()
        inputs_path = _validate_subpath(Path(args.inputs).resolve(), workspace)
        inputs = _read_json(inputs_path)

        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
        as_of_str = args.as_of or inputs.get("as_of_date") or today.strftime("%m-%d")
        as_of = parse_as_of(as_of_str, year)
        if as_of > today:
            raise ProjectionError(f"as_of {as_of.isoformat()} is in the future (today {today.isoformat()})")

        tax_law_path = workspace / f"tax_law_{year}.json"
        tax_law = _read_json(tax_law_path) if tax_law_path.exists() else {}
        profile = _read_json(workspace / "taxpayer_profile.json")
        prior = load_prior_baseline(workspace / "archive" / str(year - 1), year - 1)
    except ProjectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except RolloverError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    try:
        report = build_report(inputs, profile, tax_law, prior, year, as_of)
    except (ProjectionError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0


# State result sections known to the toolkit; extend as more states are added.
STATE_SECTION_KEYS = ["california_540"]


def _detect_prior_state(prior_result: dict) -> str | None:
    for key in STATE_SECTION_KEYS:
        if key in (prior_result or {}):
            return key
    return None


def project_state_tax(proj: ProjectedInputs, prior, tax_law: dict,
                      filing_status: str | None):
    """Project state tax only when a prior-year state section exists AND
    tax_law carries state brackets. Returns StateProjection or None."""
    if prior is None:
        return None
    if _detect_prior_state(getattr(prior, "result", {})) is None:
        return None
    state_cfg = tax_law.get("state") or {}
    brackets = state_cfg.get("brackets") or []
    if not brackets:
        return None
    bin_key = _binary_status_key(filing_status)
    std_map = state_cfg.get("standard_deduction") or {}
    std = _num(std_map.get(bin_key), "state.standard_deduction")
    # Same capital-gains netting + loss cap as the federal side (v1 assumption:
    # the state taxes capital gains as ordinary income and conforms to the
    # federal $3K/$1.5K-MFS loss limit, as CA does) — the split
    # capital_gains_short/_long fields must reach state income too, not just
    # the legacy combined field.
    net_st = proj.capital_gains_short
    net_lt = proj.capital_gains_long + proj.capital_gains
    cap_input, ordinary_short = net_capital_gains(net_st, net_lt)
    loss_limit = (CAPITAL_LOSS_LIMIT_MFS
                  if (filing_status or "").upper() == "MFS" else CAPITAL_LOSS_LIMIT)
    cap_in_income = (cap_input if cap_input >= 0
                     else max(cap_input, -loss_limit)) + ordinary_short
    state_ti = max(0.0, (proj.wages + cap_in_income + proj.ordinary_dividends
                         + proj.taxable_interest + proj.other_income
                         + proj.self_employment_net) - std)
    tax, _ = compute_bracket_tax(state_ti, brackets)
    balance = round(tax - proj.state_withholding - proj.estimated_state_paid, 2)
    return StateProjection(
        code=state_cfg.get("code", "STATE"), taxable_income=round(state_ti, 2),
        tax=round(tax, 2), withholding=round(proj.state_withholding, 2),
        estimates_paid=round(proj.estimated_state_paid, 2), balance=balance,
    )


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
