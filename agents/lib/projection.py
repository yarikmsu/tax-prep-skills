#!/usr/bin/env python3
"""Mid-year tax projection for /tax-project.

Read-only: never writes result.json or taxpayer_profile.json. Pure compute
functions + a markdown renderer + a CLI. Reuses tax_computations.py for tax
math and rollover.py for the prior-year baseline + path/year validation.
"""

import calendar
import datetime
import json
import math
import sys
from dataclasses import dataclass, field
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
    _SCH1A_EFFECTIVE_FROM,
    _SCH1A_EFFECTIVE_THROUGH,
)
from agents.lib.tax_law_fetcher import (  # noqa: E402
    _TN_HALL_REPEAL_YEAR,
    _none_type_allowed,
)
from agents.lib.secure_fs import redact  # noqa: E402


class ProjectionError(Exception):
    """Raised when /tax-project cannot safely proceed."""


PAY_PERIODS = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}
_MFJ_LIKE = {"MFJ", "QSS"}
ES_DUE_DATES = [(1, (4, 15)), (2, (6, 15)), (3, (9, 15)), (4, (1, 15))]  # Q4 due Jan 15 next year

# CA 540-ES installments are NOT four equal quarters: 30% / 40% / 0% / 30%
# per R&TC §19136.1 (FTB Form 540-ES instructions) — there is NO September
# (Q3) installment. Q4 is due Jan 15 of the following year, like federal.
# Structural statutory constants (weights are set in statute, not indexed).
CA_ES_SCHEDULE = [(1, (4, 15), 0.30), (2, (6, 15), 0.40),
                  (3, (9, 15), 0.00), (4, (1, 15), 0.30)]

# R&TC §19136.3: taxpayers with CURRENT-year CA AGI at or above these
# thresholds may not use the prior-year safe harbor at all — they must pay
# 90% of the current-year tax. Not indexed for inflation.
CA_HIGH_INCOME_AGI_LIMIT = 1_000_000
CA_HIGH_INCOME_AGI_LIMIT_MFS = 500_000

# R&TC §19136 (FTB 5805 Part I): NO CA underpayment penalty when current-year
# tax minus withholding is under $500 ($250 MFS). Statutory-fixed, not indexed.
CA_ES_DE_MINIMIS = 500
CA_ES_DE_MINIMIS_MFS = 250

_CANONICAL_STATUSES = {"SINGLE", "MFJ", "MFS", "HOH", "QSS"}


def _full_status_key(filing_status: str | None) -> str:
    fs = (filing_status or "").upper()
    return {"MFJ": "mfj", "QSS": "mfj", "MFS": "mfs", "HOH": "hoh", "SINGLE": "single"}.get(fs, "single")


def _binary_status_key(filing_status: str | None) -> str:
    return "mfj" if (filing_status or "").upper() in _MFJ_LIKE else "single"


# The full federal.schedule_1a schema (tax_law_fetcher.create_empty_tax_law):
# three scalar caps + four single/mfj-keyed maps. A raw projection override can
# only be validated when EVERY one is populated — so the completeness check
# requires the whole set, not just whichever keys happen to be present.
_SCH1A_SCALAR_LIMITS = ("tips_cap", "car_loan_cap", "senior_amount_per_person")
_SCH1A_NESTED_LIMITS = ("overtime_cap", "tips_overtime_phaseout_start",
                        "car_loan_phaseout_start", "senior_phaseout_start")


def _sch1a_limit_bad(v) -> bool:
    """A usable indexed cap/threshold must be a FINITE, NON-NEGATIVE real number
    (bool excluded). The compute path rejects string/NaN/Infinity/negative via
    _sched_a_amount, so the projection completeness check must reject them too —
    otherwise a corrupt cache like {"tips_cap": "25000"} or a negative figure
    would pass and let a raw override apply without the unfetched warning."""
    return (isinstance(v, bool) or not isinstance(v, (int, float))
            or not math.isfinite(v) or v < 0)


def _sch1a_limits_incomplete(block) -> bool:
    """The fetched federal.schedule_1a block is USABLE for validating a raw
    override only when EVERY indexed cap/threshold is populated. create_empty_
    tax_law seeds the whole block all-None, and inspecting only present keys
    would wave through a scaffold OR a partial cache (e.g. {"tips_cap": 25000}
    missing the others) — so validate the FULL required schema explicitly: each
    scalar cap present + non-None, and each nested map carrying BOTH single and
    mfj non-None. Any gap → incomplete (fail closed; a raw override applied
    against a partial cache would be uncapped/unphased). Codex rounds 14-16."""
    if not isinstance(block, dict):
        return True
    for key in _SCH1A_SCALAR_LIMITS:
        if _sch1a_limit_bad(block.get(key)):
            return True
    for key in _SCH1A_NESTED_LIMITS:
        nested = block.get(key)
        if not isinstance(nested, dict) or any(
                _sch1a_limit_bad(nested.get(k)) for k in ("single", "mfj")):
            return True
    return False


def _num(value, field: str = "") -> float:
    """Coerce to float; None->0.0; reject bool, non-finite, and unparseable strings.

    Non-finite rejection is load-bearing: json.loads accepts NaN/Infinity, and
    NaN silently defeats every downstream comparison (all NaN comparisons are
    False — e.g. a NaN ca_agi would disable the R&TC §19136.3 high-income
    check, the understating direction).
    """
    if value is None:
        return 0.0
    try:
        n = _coerce_numeric(value, default=None)
    except TypeError:
        raise ProjectionError(f"{field or 'value'} must be numeric, got bool")
    if n is None:
        raise ProjectionError(f"{field or 'value'} is not numeric: {value!r}")
    if not math.isfinite(n):
        raise ProjectionError(f"{field or 'value'} must be finite, got {n!r}")
    return n


def _nonneg_num(value, field: str = "") -> float:
    """_num, but HARD-ERROR on a negative value instead of letting a downstream
    max(0, …) silently floor it to $0. Used for the Schedule 1-A override — a
    negative deduction is invalid input (the compute/scenario paths reject it),
    and silently coercing to $0 would drop the deduction and overstate the
    projected tax + safe-harbor target with no warning."""
    n = _num(value, field)
    if n < 0:
        raise ProjectionError(f"{field or 'value'} must be non-negative, got {n!r}")
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
    # OBBBA Schedule 1-A total (tips + overtime premium + car-loan + senior),
    # a BELOW-AGI deduction (line 13b). Reduces taxable income and the QBI cap
    # base, NEVER AGI — so taxable SS and NIIT (MAGI-based) are unaffected.
    schedule_1a_total: float = 0.0
    # The portion of schedule_1a_total that is SELF-EMPLOYED §224 qualified
    # tips (from the projected Schedule C business). Form 8995 excludes deducted
    # tip income from QBI, so this is subtracted from the QBI base as well as
    # feeding the line-13b total — without it the same SE tips get both the
    # line-13b deduction and 20% QBI. Must not exceed self_employment_net.
    schedule_1a_se_tips: float = 0.0


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
    schedule_1a_unfetched: bool = False


@dataclass(frozen=True)
class StateProjection:
    code: str
    taxable_income: float
    tax: float
    withholding: float
    estimates_paid: float
    balance: float
    # Projected state AGI (income before the standard deduction; state
    # adjustments are not modeled in v1). Feeds the R&TC §19136.3
    # high-income test, which is on CURRENT-year CA AGI.
    agi: float = 0.0


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
    # Prior tax present but prior AGI unknown → 100% factor assumed with a
    # loud understatement note (mirrors compute_form_2210's handling).
    prior_agi_missing: bool = False


@dataclass(frozen=True)
class CaSafeHarbor:
    """CA estimated-tax safe harbor (FTB Form 5805).

    Mirrors the federal SafeHarbor structure — CA conforms to the
    lesser-of(90% current, 100/110% prior) test including the $150K/$75K-MFS
    prior-AGI threshold for the 110% factor — with one CA-specific rule:
    current-year CA AGI >= $1,000,000 ($500,000 MFS) forbids the prior-year
    exception entirely (R&TC §19136.3). `high_income_90_only` flags that path
    so the renderer can explain why the prior-year row is absent.
    """
    pct90_current: float
    prior_tax: "float | None"
    prior_factor: "float | None"
    prior_based: "float | None"
    required_annual: float
    withholding: float
    estimates_paid: float
    shortfall: float
    covered: bool
    degraded: bool
    high_income_90_only: bool
    # The §19136.3 threshold applied (by filing status) — lets renderers warn
    # when a PROXY current-year AGI sits near the cliff.
    high_income_threshold: float = 0.0
    # Prior tax present but prior AGI unknown → the 110% factor cannot be
    # tested; 100% assumed with a loud understatement note (the Form 2210
    # precedent for the same gap).
    prior_agi_missing: bool = False
    # R&TC §19136: current tax minus withholding under $500/$250-MFS → no CA
    # underpayment penalty can arise regardless of the shortfall shown.
    de_minimis: bool = False


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
    # Present only when the state projection ran AND the state is CA — the
    # only state whose estimated-payment weighting (30/40/0/30) is modeled.
    ca_safe_harbor: "CaSafeHarbor | None" = None
    ca_vouchers: "list[Voucher]" = field(default_factory=list)
    # True only when the fetched state block POSITIVELY declares no income
    # tax (state.type == "none" — FL, TX, ...). Lets the renderer distinguish
    # "this state has no income tax" from "no baseline / brackets not
    # fetched" when `state` is None (mirrors loss_harvest.state_no_income_tax
    # — same bias-direction rule: absence of data must never read as
    # absence of tax, so an unfetched/unknown-type cache stays False).
    state_no_income_tax: bool = False
    # 2-letter state code to name in the no-income-tax skip message (from
    # tax_law.state.code, falling back to the taxpayer's resident state).
    # Only meaningful when state_no_income_tax is True.
    no_income_tax_state_code: "str | None" = None
    # Set when tax_law.state.code (normalized) and the taxpayer's resident
    # state are BOTH known and DIFFERENT — a stale cache for the wrong
    # state (round-5 P1). None when there's nothing to warn about (codes
    # match, or one/both sides are unknown).
    state_cache_mismatch: "str | None" = None
    # Codex round-8 finding F: set whenever profile flags.
    # has_nonresident_source_income is True, REGARDLESS of which state-
    # projection path ran (FL no-tax / CA graduated / mismatch / skipped) —
    # a resident-state projection (or a resident no-tax note) says nothing
    # about a SEPARATE state's nonresident liability on out-of-state W-2/
    # K-1/rental income. This module is read-only/advisory, so unlike the
    # capabilities gate's fail-closed `_REQUIRED_GATE_FLAGS` rule an
    # absent/unset flag reads as False here (never blocks a projection);
    # only an explicit True flag ever turns this warning on.
    nonresident_income_warning: "str | None" = None
    # Codex round-24 P2: set when the state projection was skipped because
    # the cache/resident resolve to pre-repeal TN — the round-23 TN guard
    # in project_state_tax returns None (never bracket-estimate the Hall
    # regime over all income), but a silent None made render_markdown fall
    # through to the generic "no prior-year state baseline or no state
    # brackets" message, CONCEALING the real limitation: a potentially
    # real Hall-tax liability on interest/dividends needs manual handling
    # (Form INC 250). Mirrors the capability gate's Hall-specific note.
    state_unsupported_note: "str | None" = None


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
        schedule_1a_total=_nonneg_num(overrides.get("schedule_1a"), "schedule_1a"),
        schedule_1a_se_tips=_nonneg_num(
            overrides.get("schedule_1a_se_tips"), "schedule_1a_se_tips"),
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


def project_federal_tax(proj: ProjectedInputs, profile: dict, tax_law: dict,
                        requested_year: "int | None" = None) -> FederalProjection:
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
    # OBBBA Schedule 1-A (line 13b) is a below-AGI deduction: it comes off
    # taxable income and the QBI cap base but NOT AGI (AGI is line 366, already
    # fixed — so the taxable-SS and NIIT MAGI computations are unaffected).
    # The projected total is a forecast_overrides figure (the taxpayer/skill
    # estimates the capped, MAGI-phased amount via compute_schedule_1a); the
    # projection does not re-derive the caps. GATE by the effective-year window:
    # compute_schedule_1a returns $0 outside TY2025-2028, so a stale override
    # for TY2029+/pre-2025 must not shave taxable income (which would understate
    # tax and the safe-harbor target) — Codex P2.
    proj_year = tax_law.get("tax_year")
    sch1a = max(0.0, proj.schedule_1a_total)
    # Effective-year gate, FAIL CLOSED: apply the Schedule 1-A override ONLY
    # when the cache's tax_year is a genuine int inside TY2025-2028. A missing,
    # non-int, or out-of-window year clears it — a malformed or legacy cache
    # (or a TY2029+/pre-2025 projection) must not shave taxable income. (The
    # earlier "clear only when it's an int OUTSIDE the window" logic failed
    # open on a missing/string year — Codex round-16 P2.)
    year_ok = (isinstance(proj_year, int) and not isinstance(proj_year, bool)
               and _SCH1A_EFFECTIVE_FROM <= proj_year <= _SCH1A_EFFECTIVE_THROUGH
               # ...and, when the caller supplies the requested projection year,
               # the cache year must MATCH it — a cache built for a different
               # (even in-window) year can't validate this year's override.
               and (requested_year is None or proj_year == requested_year))
    if sch1a and not year_ok:
        sch1a = 0.0
    # MFS gate: ALL FOUR Schedule 1-A provisions are $0 for a married filer who
    # files separately — tips/overtime/senior require a joint return AND the
    # §70203 car-loan deduction is also unavailable to MFS (round 13) — so an
    # MFS override must not shave taxable income here either (Codex round-14 P2).
    if sch1a and full_key == "mfs":
        sch1a = 0.0
    # A raw override is subtracted here without re-deriving the caps/MAGI phase-
    # out (compute_schedule_1a is not called). If the indexed `federal.schedule_
    # 1a` block is missing OR only scaffold (all/any None — as create_empty_tax_
    # law seeds it), the override is applied uncapped/unphased — flag it so the
    # user knows the projected deduction may exceed the real amount rather than
    # silently trusting a possibly-raw figure.
    schedule_1a_unfetched = bool(
        sch1a > 0 and _sch1a_limits_incomplete(fed.get("schedule_1a")))
    ti_before_qbi = max(0.0, agi - std - sch1a)
    # §224 SE tips are EXCLUDED from QBI (Form 8995): when the applied Schedule
    # 1-A deduction includes self-employed qualified tips, those same dollars
    # are removed from the QBI base so they don't get both the line-13b
    # deduction and 20% QBI. Only when the deduction is actually applied (same
    # gate as sch1a) and capped at self_employment_net so the base can't go
    # negative (Codex round-17 P2).
    # Bound by BOTH self_employment_net (can't exclude more SE income than
    # exists) AND sch1a (the APPLIED deduction — SE tips can't be excluded from
    # QBI beyond what was actually deducted on line 13b; an override with
    # se_tips > schedule_1a is inconsistent and must not over-exclude). Codex P2.
    se_tips_qbi_excl = (
        min(proj.schedule_1a_se_tips, proj.self_employment_net, sch1a)
        if sch1a > 0 else 0.0)
    qbi_base = max(0.0, proj.self_employment_net - se_tips_qbi_excl)
    qbi_over_threshold = False
    if qbi_base > 0:
        # §199A(c)(4) / Reg. §1.199A-3(b)(1)(vi): net the QBI base by the
        # deductible half of SE tax (the SE-tips exclusion is already applied in
        # qbi_base above; SE health insurance / retirement are not projection
        # inputs). Omitting it overstates the QBI deduction and understates
        # projected tax for a self-employed filer.
        qbi_res = compute_qbi_deduction(
            qbi_base, 0.0, rate=fed.get("qbi_deduction_rate", 0.20),
            taxable_income_before_qbi=ti_before_qbi,
            net_capital_gain=proj.qualified_dividends + net_ltcg,
            threshold=(fed.get("qbi_threshold") or {}).get(bin_key),
            half_se_tax=se["deductible_half"],
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
        schedule_1a_unfetched=schedule_1a_unfetched,
    )


def compute_safe_harbor(projected_tax: float, prior_tax: float | None, prior_agi: float | None,
                        filing_status: str | None, withholding: float,
                        estimates_paid: float) -> SafeHarbor:
    pct90 = round(0.90 * projected_tax, 2)
    degraded = prior_tax is None
    prior_agi_missing = False
    if degraded:
        required = pct90
        prior_factor = None
        prior_based = None
    else:
        threshold = 75000 if (filing_status or "").upper() == "MFS" else 150000
        # Prior AGI unknown → assume the 100% factor, flagged so renderers
        # print the may-be-UNDERSTATED note (the compute_form_2210 precedent,
        # pinned by the required-annual-payment parity test).
        prior_agi_missing = prior_agi is None
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
        prior_agi_missing=prior_agi_missing,
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


def compute_ca_safe_harbor(projected_tax: float, prior_tax: "float | None",
                           prior_agi: "float | None", current_agi: float,
                           filing_status: "str | None", withholding: float,
                           estimates_paid: float) -> CaSafeHarbor:
    """CA required annual payment per FTB Form 5805.

    Same structure as the federal §6654 test (CA conforms, including the
    110%-of-prior factor when PRIOR-year AGI exceeds $150K/$75K-MFS), plus
    the R&TC §19136.3 limitation: when CURRENT-year CA AGI is at or above
    $1M ($500K MFS), the prior-year exception is unavailable and the
    required payment is 90% of the current-year tax. Note the two AGI tests
    read different years — §19136.3 looks at the year being paid,
    the 110% factor at the preceding year.
    """
    # MFS drives BOTH CA-specific cliffs ($500K §19136.3 limit, $250 de
    # minimis) plus the $75K 110% threshold — a non-canonical spelling
    # ("Married Filing Separately") silently taking the non-MFS windows is
    # the understating direction, so VALUE-validate like retirement_dashboard.
    status = (filing_status or "").strip().upper()
    if status not in _CANONICAL_STATUSES:
        raise ProjectionError(
            f"filing_status must be one of Single/MFJ/MFS/HoH/QSS for the CA "
            f"safe harbor, got {filing_status!r}")
    pct90 = round(0.90 * projected_tax, 2)
    mfs = status == "MFS"
    high_limit = CA_HIGH_INCOME_AGI_LIMIT_MFS if mfs else CA_HIGH_INCOME_AGI_LIMIT
    high = _num(current_agi, "current_agi") >= high_limit
    degraded = (prior_tax is None) and not high
    prior_factor = None
    prior_based = None
    prior_agi_missing = False
    if high or degraded:
        required = pct90
    else:
        threshold = 75000 if mfs else 150000
        prior_agi_missing = prior_agi is None
        prior_factor = 1.10 if (prior_agi or 0) > threshold else 1.00
        prior_based = round(prior_factor * prior_tax, 2)
        required = round(min(pct90, prior_based), 2)
    paid = round(withholding + estimates_paid, 2)
    shortfall = round(max(0.0, required - paid), 2)
    de_minimis = (projected_tax - withholding) < (CA_ES_DE_MINIMIS_MFS if mfs
                                                  else CA_ES_DE_MINIMIS)
    # Under the de minimis no CA penalty can arise, so the PENALTY target is
    # met — zero the shortfall so DE 4 / "to avoid penalty" vouchers don't
    # demand payments that avoid nothing (Codex P2 on PR #35). The
    # zero-balance target is computed by callers from the liability and
    # still shows the tax due.
    if de_minimis:
        shortfall = 0.0
    return CaSafeHarbor(
        pct90_current=pct90, prior_tax=prior_tax, prior_factor=prior_factor,
        prior_based=prior_based, required_annual=required,
        withholding=round(withholding, 2), estimates_paid=round(estimates_paid, 2),
        shortfall=shortfall, covered=shortfall <= 0, degraded=degraded,
        high_income_90_only=high, high_income_threshold=float(high_limit),
        prior_agi_missing=prior_agi_missing, de_minimis=de_minimis,
    )


def compute_ca_estimated_vouchers(required_annual: float, withholding: float,
                                  paid: float, as_of: datetime.date) -> list:
    """CA 540-ES voucher schedule under the 30/40/0/30 weighting.

    The remaining shortfall (withholding treated as paid evenly — the FTB
    5805 default, same as federal — net of estimates already paid) is
    allocated across the still-open installments in proportion to their
    statutory weights, so before Apr 15 the split is exactly 30/40/0/30 and
    after Jun 15 everything lands on the January installment. Q3 is emitted
    at $0 whenever it's still open so the "no September CA payment" fact is
    visible in the schedule rather than silently absent. The rounding
    remainder lands on the last weighted installment so the column sums to
    the shortfall exactly.

    Simplification (same as the federal schedule): FTB 5805 tracks each
    installment cumulatively, so paying after a missed installment date does
    not fully cure that installment's underpayment — this schedule targets
    the ANNUAL requirement from `as_of` forward.
    """
    shortfall = max(0.0, required_annual - withholding - paid)
    open_q = []
    for q, (m, d), weight in CA_ES_SCHEDULE:
        if q == 4:
            open_q.append((4, f"{as_of.year + 1}-01-15", weight))
            continue
        due = datetime.date(as_of.year, m, d)
        if due >= as_of:
            open_q.append((q, due.isoformat(), weight))
    # Q4 is appended unconditionally above, so open_q is never empty and
    # total_weight is never 0 (Q4 carries 0.30).
    total_weight = sum(w for _, _, w in open_q)
    if shortfall <= 0 or total_weight <= 0:
        return [Voucher(q, dd, 0.0) for q, dd, _ in open_q]
    last_weighted = max(i for i, (_, _, w) in enumerate(open_q) if w > 0)
    vouchers = []
    allocated = 0.0
    for i, (q, dd, w) in enumerate(open_q):
        if i == last_weighted:
            amt = round(shortfall - allocated, 2)
        else:
            amt = round(shortfall * w / total_weight, 2)
            allocated = round(allocated + amt, 2)
        vouchers.append(Voucher(q, dd, amt))
    return vouchers


def _prior_numeric(v, *, allow_negative: bool):
    """Prior-year figure sanitizer: bool, non-finite (json.loads accepts
    NaN/Infinity), and — for tax figures — negative values all degrade to
    None (→ 90%-of-current fallback) instead of corrupting the min():
    min(pct90, NaN) propagates NaN and min(pct90, -5000) marks a corrupted
    archive as "covered"."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    f = float(v)
    if not math.isfinite(f):
        return None
    if f < 0 and not allow_negative:
        return None
    return f


# Fields whose non-zero values prove /tax-compute step 10 actually RAN —
# deliberately excludes payment/bookkeeping fields (ca_withholding_box17,
# ca_sdi_box14, estimated payments): those can be populated from W-2 parsing
# before the CA return is ever computed, so their presence proves nothing
# about ca_total_tax/ca_agi being real rather than untouched template zeros.
# A genuinely computed CA return always carries a non-zero
# ca_standard_deduction (statutory minimum ~$5K), even at zero income.
_CA_COMPUTED_EVIDENCE_FIELDS = (
    "ca_total_tax", "ca_agi", "ca_taxable_income",
    "ca_standard_deduction", "ca_tax_from_table",
)


def _ca_section_computed(result: dict) -> bool:
    """True when result's california_540 section shows COMPUTED evidence —
    a non-zero finite number in one of the compute-output fields — not mere
    non-emptiness. yoy_diff._section_present is too weak here: a section
    holding only parsed W-2 Box 17 withholding reads "present" while
    ca_total_tax/ca_agi are still template zeros (Codex P2 on PR #35)."""
    ca = (result or {}).get("california_540")
    if not isinstance(ca, dict):
        return False
    for key in _CA_COMPUTED_EVIDENCE_FIELDS:
        v = ca.get(key)
        if (isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) and v != 0):
            return True
    return False


def _prior_ca_tax_agi(prior):
    """Extract (prior CA total tax, prior CA AGI) from the archived
    california_540 section, or (None, None) when absent, scaffold-only,
    or non-numeric. An all-zero template scaffold (never computed) must
    not become a $0 prior-year tax — that would make ANY current shortfall
    "covered" via min(pct90, 0) — and non-zero payment/bookkeeping fields
    alone don't rescue it (_ca_section_computed). AGI may be negative
    (loss year); tax may not."""
    if prior is None:
        return None, None
    result = getattr(prior, "result", {}) or {}
    if not _ca_section_computed(result):
        return None, None
    ca = result.get("california_540", {}) or {}
    return (_prior_numeric(ca.get("ca_total_tax"), allow_negative=False),
            _prior_numeric(ca.get("ca_agi"), allow_negative=True))


def _prior_tax_agi(prior):
    """Extract (prior_total_tax, prior_agi) from a PriorReturn-like object,
    or (None, None). Same sanitization as the CA reader — bool/NaN/negative
    prior tax degrade to None rather than corrupt the safe-harbor min()."""
    if prior is None:
        return None, None
    f1040 = (getattr(prior, "result", {}) or {}).get("form_1040", {}) or {}
    return (_prior_numeric(f1040.get("line_24_total_tax"), allow_negative=False),
            _prior_numeric(f1040.get("line_11_agi"), allow_negative=True))


def _normalize_state_code(value: "str | None") -> "str | None":
    """Uppercase/strip to a 2-letter state code, else None. Shared by
    project_state_tax's resident-state backfill and the cache-vs-resident
    cross-check below so the two can't drift on what counts as a valid
    code."""
    if isinstance(value, str):
        cleaned = value.strip().upper()
        if len(cleaned) == 2 and cleaned.isalpha():
            return cleaned
    return None


def _cache_state_code_present_but_invalid(state_cfg: dict) -> bool:
    """True iff `state_cfg` has a `"code"` KEY (checked via `in`, not
    `.get() is not None`) that does NOT normalize to a valid 2-letter code
    — this includes an explicit `"code": null` as well as a malformed
    string/number (e.g. "FLA", 123, "").

    Codex round-14 P2 (sharpened by round-15 P2): `_normalize_state_code`
    collapses ALL of "key absent", "key present as null", and "key present
    but malformed" to the same `None` — every consumer treats "cache code
    unknown" as "safe to backfill the resident's state and trust this
    cache's brackets/type," which is the right call ONLY for a genuinely
    ABSENT key (a legacy cache that predates the `state.code` field). The
    original round-14 fix checked `raw is not None`, which correctly caught
    "FLA" but still let an EXPLICIT `"code": null` through as if the key
    had never existed — a CA-bracket cache with `"code": null` beside an FL
    taxpayer would otherwise sail through every code-mismatch check (both
    sides need to be non-None to compare) and have its real CA brackets
    projected and LABELED as Florida tax, exactly like the "FLA" case.
    Checking `"code" in state_cfg` (key presence) instead of "value is not
    None" closes that gap: absent key -> False (legacy leniency preserved),
    key present with ANY non-normalizing value (null, malformed, wrong
    type) -> True (hard-reject, never backfill)."""
    if "code" not in state_cfg:
        return False
    return _normalize_state_code(state_cfg.get("code")) is None


def _resolve_state_no_income_tax(tax_law: dict, resident_code: "str | None",
                                  year: int):
    """Decide the no-income-tax flag, guarding against a STALE cache for a
    different state than the taxpayer's resident state (e.g. a leftover FL
    state.type == "none" cache left beside a CA profile after an address
    correction — Codex round-5 P1) AND against a cache saved for the wrong
    YEAR (Codex round-9 P2). Mirrors the /tax-compute skill-level
    cross-check (tax_law.state.code vs taxpayer_profile.address.state).

    The year check below is now also enforced EARLIER and HARDER by
    `build_report`'s `_validate_tax_law_year` call (Codex round-11 P1),
    which raises before this function — or `project_federal_tax`/
    `project_state_tax` — ever runs, so `build_report` callers never
    actually reach the year-mismatch branch here. It stays as defense in
    depth for any other/future caller of this function directly.

    Returns (no_income_tax, mismatch_note, code):
    - resident state unknown: flag False, no warning — can't verify the
      cache against anything, so this stays the safe "no baseline" skip
      note rather than a confident (and possibly wrong) claim (mirrors
      loss_harvest.state_no_income_tax's bias-direction rule).
    - resident known AND the cache's declared `tax_year` is missing,
      malformed, or does not equal `year`: flag False + a loud mismatch
      note, regardless of what the cache claims — this module reads
      `tax_law_{year}.json` directly in `_main` and never calls
      tax_law_fetcher.check_freshness (which gained an equivalent guard in
      round 8), so a mis-saved/misnamed cache — e.g. a `tax_law_2020.json`
      whose body still declares `"tax_year": 2021` — would otherwise reach
      the `type == "none"` branch below and claim "no state tax" for a
      year whose regime may have been entirely different (TN's Hall tax
      was still live in 2020, repealed only for TY2021+).
    - resident known AND year matches AND cache code known AND they
      differ: flag False + a loud mismatch note, regardless of what the
      mismatched cache claims — a cache for the wrong state can't be
      trusted either way.
    - resident known AND year matches AND (cache code matches OR cache
      code is absent): flag mirrors the cache's declared
      state.type == "none" — UNLESS `code` is known and `type: "none"`
      isn't a regime that code can legitimately carry for `year` (Codex
      round-10 P1): a same-year, code-matching CA cache with a corrupted
      `type: "none"` (hand-edited or a fetch bug) would otherwise pass
      every check above and make this module claim California has no
      individual income tax, silently dropping the CA liability/payment
      projection. `_none_type_allowed` (tax_law_fetcher's single source of
      truth for the code<->regime allowlist, also used by
      check_freshness — which this module never calls, since it reads the
      cache file directly) is the same cross-check /tax-compute's
      skill-level guard applies; a cache lacking a `code` field can't be
      checked against it and keeps degrading to the generic skip note
      (safe direction) rather than a confident no-tax claim.
    - `state.code` is PRESENT but does not normalize to a valid 2-letter
      code (Codex round-14 P2, e.g. "FLA"): flag False + a loud mismatch
      note, checked FIRST (before the resident-unknown branch, since this
      is a property of the cache itself and needs no resident to compare
      against) — `_normalize_state_code` collapses this to the same `None`
      an ABSENT code produces, which every other branch here treats as
      "safe to degrade quietly," but a present-and-corrupted code is
      affirmative evidence the cache itself is untrustworthy.
    """
    state_cfg = tax_law.get("state") or {}

    if _cache_state_code_present_but_invalid(state_cfg):
        mismatch = (f"state law cache has a malformed state.code "
                    f"({state_cfg.get('code')!r} — not a valid 2-letter "
                    f"USPS code); re-run /tax-setup; state projection "
                    f"skipped")
        return False, mismatch, None

    cache_code = _normalize_state_code(state_cfg.get("code"))
    cache_says_none = state_cfg.get("type") == "none"

    if resident_code is None:
        return False, None, None

    cache_tax_year = tax_law.get("tax_year")
    if isinstance(cache_tax_year, bool) or not isinstance(cache_tax_year, int) \
            or cache_tax_year != year:
        mismatch = (f"state law cache does not declare tax_year {year} "
                    f"(found {cache_tax_year!r}) — re-run /tax-setup; "
                    f"state projection skipped")
        return False, mismatch, None

    if cache_code is not None and cache_code != resident_code:
        mismatch = (f"state law cache is for {cache_code} but the taxpayer "
                    f"resides in {resident_code} — re-run /tax-setup; "
                    f"state projection skipped")
        return False, mismatch, None

    if cache_code is not None and cache_says_none \
            and not _none_type_allowed(cache_code, year):
        mismatch = (f"state law cache declares type \"none\" for "
                    f"{cache_code}, but {cache_code} is not a recognized "
                    f"no-income-tax state for tax year {year} (cache is "
                    f"corrupted or was hand-edited) — re-run /tax-setup; "
                    f"state projection skipped")
        return False, mismatch, None

    # INVERSE regime contradiction (Codex round-15 P1's twin here): a code
    # that IS a legitimate no-tax state for `year` but is typed something
    # OTHER than "none" while carrying real leftover brackets (e.g.
    # code: "FL", type: "graduated_income") is just as corrupted as the
    # opposite case above — without this, `no_income_tax` below would
    # silently resolve to False with NO mismatch note (cache_says_none is
    # False, so the affirmative branch never fires either), leaving the
    # renderer to show a generic "no baseline" skip instead of flagging the
    # actual data corruption. Gated on non-empty brackets — a merely
    # untyped-but-bracket-less cache isn't the dangerous case this guards.
    if cache_code is not None and not cache_says_none \
            and _none_type_allowed(cache_code, year) \
            and (state_cfg.get("brackets") or []):
        mismatch = (f"state law cache declares type "
                    f"{state_cfg.get('type')!r} for {cache_code}, but "
                    f"{cache_code} is a recognized no-income-tax state for "
                    f"tax year {year} (cache is corrupted or was "
                    f"hand-edited) — re-run /tax-setup; state projection "
                    f"skipped")
        return False, mismatch, None

    no_income_tax = cache_code is not None and cache_says_none
    code = cache_code if no_income_tax else None
    return no_income_tax, None, code


_NONRESIDENT_INCOME_WARNING = (
    "Another state's nonresident liability is not modeled — that state's "
    "tax and estimates are missing from this projection."
)


def _nonresident_income_flag(profile: dict) -> bool:
    """Advisory read of flags.has_nonresident_source_income: absent, None,
    or a non-dict/non-bool flags block all read as False. Deliberately NOT
    the capabilities gate's fail-closed rule (_REQUIRED_GATE_FLAGS hard-
    errors on an unanswered flag) — /tax-project is read-only/advisory and
    must never block a projection over an unrelated screening question the
    taxpayer hasn't answered yet; only an explicit `true` turns the warning
    on."""
    flags = profile.get("flags")
    if not isinstance(flags, dict):
        return False
    return flags.get("has_nonresident_source_income") is True


def _validate_tax_law_year(tax_law: dict, year: int) -> None:
    """Hard-stop guard (Codex round-11 P1): `tax_law`'s OWN declared
    `tax_year` must match the year this projection is running for, checked
    BEFORE `project_federal_tax`/`project_state_tax` consume the cache's
    brackets/deductions/thresholds.

    The round-9 cross-check lived only inside `_resolve_state_no_income_tax`,
    which runs at the END of `build_report` — by then `project_federal_tax`
    and `project_state_tax` had already used the (possibly wrong-year)
    cache, so a mismatch silently produced a wrong federal liability,
    safe-harbor target, voucher schedule, and W-4 recommendation while only
    a STATE-labeled `state_cache_mismatch` note appeared. This raises
    immediately instead, before either projection runs, mirroring the
    existing "No federal brackets for status... run /tax-setup" hard error
    a few lines into `project_federal_tax` — a wrong-year cache is the same
    class of unusable-cache problem as a missing one, just with data that
    happens to be present but wrong.
    """
    cache_tax_year = tax_law.get("tax_year")
    if isinstance(cache_tax_year, bool) or not isinstance(cache_tax_year, int) \
            or cache_tax_year != year:
        raise ProjectionError(
            f"tax_law_{year}.json does not declare tax_year {year} "
            f"(found {cache_tax_year!r}) — this cache cannot be trusted for "
            f"federal OR state projection (wrong brackets/deductions/"
            f"thresholds). Re-run /tax-setup to fetch a {year} cache."
        )


def build_report(inputs: dict, profile: dict, tax_law: dict, prior,
                 year: int, as_of: datetime.date) -> ProjectionReport:
    _validate_tax_law_year(tax_law, year)
    pay_frequency = validate_pay_frequency(inputs.get("pay_frequency", "biweekly"))
    proj = annualize(inputs.get("ytd", {}), as_of, pay_frequency,
                     inputs.get("forecast_overrides", {}),
                     inputs.get("recurring_from_prior", {}),
                     inputs.get("estimated_payments_made", {}))
    fs = profile.get("filing_status")
    federal = project_federal_tax(proj, profile, tax_law, requested_year=year)
    address = profile.get("address")
    resident_state = address.get("state") if isinstance(address, dict) else None
    state = project_state_tax(proj, prior, tax_law, fs,
                              resident_state=resident_state)

    prior_tax, prior_agi = _prior_tax_agi(prior)
    safe_harbor = compute_safe_harbor(
        federal.total_tax, prior_tax, prior_agi, fs,
        withholding=federal.withholding, estimates_paid=federal.estimates_paid)

    vouchers = compute_estimated_vouchers(
        safe_harbor.required_annual, federal.withholding, federal.estimates_paid, as_of)

    full_shortfall = max(0.0, federal.total_tax - federal.withholding - federal.estimates_paid)
    w4_penalty = compute_w4_4c(safe_harbor.shortfall, proj.paychecks_remaining)
    w4_zero = compute_w4_4c(full_shortfall, proj.paychecks_remaining)

    # CA is the only state whose voucher weighting (30/40/0/30) is modeled;
    # other states fall back to the informational note in the renderer.
    ca_safe_harbor = None
    ca_vouchers: list = []
    if state is not None and state.code == "CA":
        prior_ca_tax, prior_ca_agi = _prior_ca_tax_agi(prior)
        ca_safe_harbor = compute_ca_safe_harbor(
            state.tax, prior_ca_tax, prior_ca_agi, state.agi, fs,
            withholding=state.withholding, estimates_paid=state.estimates_paid)
        ca_vouchers = compute_ca_estimated_vouchers(
            ca_safe_harbor.required_annual, state.withholding,
            state.estimates_paid, as_of)

    resident_code = _normalize_state_code(resident_state)
    no_income_tax, cache_mismatch, no_income_tax_code = _resolve_state_no_income_tax(
        tax_law, resident_code, year)

    nonresident_income_warning = (
        _NONRESIDENT_INCOME_WARNING if _nonresident_income_flag(profile) else None
    )

    # Round-24 P2: name the Hall-tax skip instead of hiding it behind the
    # generic no-baseline message. Fires only when the state projection
    # actually skipped, the resolved state is TN, and no louder channel
    # (cache mismatch / legitimate post-repeal no-tax claim) already
    # explains the outcome — a post-repeal type-none TN cache takes the
    # no_income_tax path, and a TY2021+ graduated TN cache is the round-15
    # inverse contradiction and gets its mismatch note instead.
    state_unsupported_note = None
    _st_cfg = tax_law.get("state") or {}
    _tn_resolved = _normalize_state_code(_st_cfg.get("code")) or resident_code
    # Codex round-27 P2: the Hall label additionally requires the year to
    # actually BE pre-repeal — a TY2021+ TN resident with a legacy
    # code-less cache reaches here (resident backfill resolves TN, the
    # resolver degrades quietly on the absent code), and labeling THAT
    # skip "predates the Hall-tax repeal" asserts a false fact about a
    # post-repeal year. The post-repeal code-less case keeps the generic
    # skip message — its real problem is an unverifiable cache, not the
    # Hall regime.
    if (state is None and _tn_resolved == "TN"
            and year < _TN_HALL_REPEAL_YEAR
            and not no_income_tax and cache_mismatch is None):
        state_unsupported_note = (
            "pre-2021 TN Hall tax (interest & dividends ONLY) is not "
            "modeled — a bracket-based projection would fabricate tax on "
            "wages/SE/capital gains. If taxable interest/dividend income "
            "exceeded the filing threshold, handle the TN Hall return "
            "(Form INC 250) manually.")

    return ProjectionReport(
        year=year, as_of=as_of.strftime("%m-%d"), pay_frequency=pay_frequency,
        baseline_source=(f"archive/{year - 1}" if prior is not None else None),
        degraded=(prior is None), proj=proj, federal=federal, state=state,
        safe_harbor=safe_harbor, vouchers=vouchers, w4_penalty=w4_penalty, w4_zero=w4_zero,
        ca_safe_harbor=ca_safe_harbor, ca_vouchers=ca_vouchers,
        state_no_income_tax=no_income_tax,
        no_income_tax_state_code=no_income_tax_code,
        state_cache_mismatch=cache_mismatch,
        nonresident_income_warning=nonresident_income_warning,
        state_unsupported_note=state_unsupported_note)


def _money(v) -> str:
    if v is None:
        return "—"
    body = f"${abs(v):,.0f}"
    return f"({body})" if v < 0 else body


def _ca_status(csh: CaSafeHarbor) -> str:
    """Status cell for the CA safe-harbor table. Under the de minimis no CA
    penalty can arise regardless of the payment pace (R&TC §19136; FTB 5805
    Part I), so the penalty target reads covered — with the reason, since
    the zero-balance target may still show tax due."""
    if csh.de_minimis:
        return ("covered — CA tax minus withholding is below the "
                "$500/$250-MFS de minimis (R&TC §19136; FTB 5805 Part I): "
                "no CA underpayment penalty applies")
    return "covered" if csh.covered else "underpaid"


def _ca_notes(csh: CaSafeHarbor) -> list:
    """Caveat bullets shared by the /tax-project and /tax-withholding CA
    blocks (single source so the two reports can't drift)."""
    notes = []
    if csh.high_income_90_only:
        notes.append(
            "- **High-income limitation (R&TC §19136.3):** CA AGI is "
            "$1,000,000 or more ($500,000 MFS), so the prior-year safe "
            "harbor is NOT available — CA requires 90% of the current-year tax.")
    if csh.degraded:
        notes.append("- **Degraded:** no prior-year CA baseline — using the "
                     "90%-of-current-year test only.")
    if csh.prior_agi_missing:
        notes.append(
            "- **Prior-year CA AGI unavailable** — assumed at or below the "
            "$150,000/$75,000-MFS threshold (100% factor); if prior CA AGI "
            "exceeded it, the 110% harbor applies and the required payment "
            "shown may be UNDERSTATED.")
    notes.append(
        "- Not modeled: farmers/fishermen (FTB 5805F — 66⅔% harbor, single "
        "Jan 15 installment; this 30/40/0/30 schedule does not apply to "
        "them) and the prior-year-harbor precondition of a filed 12-month "
        "prior-year CA return.")
    return notes


_CA_VOUCHER_CATCHUP_NOTE = (
    "This schedule targets the ANNUAL requirement from the as-of date "
    "forward — FTB 5805 tracks each installment cumulatively, so an "
    "installment already missed keeps accruing penalty until paid. To stop "
    "accrual, pay catch-up amounts NOW rather than at the printed dates.")


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
    lines.append(
        "- **Second-order effects (use /tax-compute for the exact figures):** "
        "the standard deduction is the base amount — no §63(f) age-65/blind "
        "add-on (slightly overstates the safe-harbor target near a threshold). "
        "Self-employed §224 tips are removed from the QBI base ONLY when you "
        "supply `forecast_overrides.schedule_1a_se_tips`; if a Schedule 1-A "
        "override includes SE tips but that split is omitted, those dollars "
        "stay in QBI and the projection understates tax. The §70105 minimum "
        "QBI deduction (TY2026+, ~$400 for a small ACTIVE trade) is NOT applied "
        "in this forecast — so projected tax may be slightly OVERSTATED (safe "
        "direction) for an eligible small business; /tax-compute applies it.")
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
    if sh.prior_agi_missing:
        lines += ["- **Prior-year AGI unavailable** — assumed at or below the "
                  "$150,000/$75,000-MFS threshold (100% factor); if prior AGI "
                  "exceeded it, the 110% harbor applies and the required "
                  "payment shown may be UNDERSTATED.", ""]

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
    if r.nonresident_income_warning:
        lines += [f"> **⚠ Nonresident income exposure:** "
                  f"{r.nonresident_income_warning}", ""]
    if r.state is None:
        if r.state_cache_mismatch:
            lines.append(f"_⚠ **State cache mismatch:** {r.state_cache_mismatch}._")
        elif r.state_no_income_tax:
            code = r.no_income_tax_state_code or "This state"
            lines.append(
                f'_State projection: none — {code} levies no individual '
                f'income tax (state.type = "none")._'
            )
        elif r.state_unsupported_note:
            lines.append(f"_⚠ **State not modeled:** {r.state_unsupported_note}_")
        else:
            lines.append("_Skipped — no prior-year state baseline or no state brackets in tax_law._")
    else:
        s = r.state
        lines += ["| Item | Amount |", "|---|---:|",
                  f"| State ({s.code}) taxable income | {_money(s.taxable_income)} |",
                  f"| State tax | {_money(s.tax)} |",
                  f"| State withholding | {_money(-s.withholding)} |",
                  f"| **State balance** | {_money(s.balance)} {'(refund)' if s.balance < 0 else '(owed)'} |"]
    if r.ca_safe_harbor is not None:
        csh = r.ca_safe_harbor
        lines += ["", "### CA safe harbor (FTB 5805)", "",
                  "| Test | Amount |", "|---|---:|",
                  f"| 90% of projected current-year CA tax | {_money(csh.pct90_current)} |"]
        if csh.prior_based is not None:
            lines.append(f"| {csh.prior_factor:.0%} of prior-year CA tax | {_money(csh.prior_based)} |")
        lines += [f"| **Required annual payment** | {_money(csh.required_annual)} |",
                  f"| CA withholding + estimates paid | {_money(csh.withholding + csh.estimates_paid)} |",
                  f"| **Shortfall** | {_money(csh.shortfall)} |",
                  f"| Status | {_ca_status(csh)} |", ""]
        lines += _ca_notes(csh)
        # The §19136.3 test above ran on a PROXY CA AGI (federal-composition
        # income; no CA adjustments like the HSA add-back) — say so, and get
        # loud near the cliff where the proxy could mask a crossing.
        lines.append(
            "- Projected CA AGI here is a federal-composition proxy (CA "
            "adjustments such as the HSA add-back are not modeled) — verify "
            "against Schedule CA when near the §19136.3 threshold.")
        if (r.state is not None and not csh.high_income_90_only
                and csh.high_income_threshold > 0
                and r.state.agi >= 0.9 * csh.high_income_threshold):
            lines.append(
                f"- **⚠ Projected CA AGI ({_money(r.state.agi)}) is within "
                f"10% of the R&TC §19136.3 threshold "
                f"({_money(csh.high_income_threshold)}).** If true CA AGI "
                f"reaches the threshold, the prior-year safe harbor is "
                f"unavailable and the required payment jumps to 90% of "
                f"current-year tax ({_money(csh.pct90_current)}).")
        lines += ["", "### CA 540-ES voucher schedule (30% / 40% / 0% / 30%)", "",
                  "California installments are NOT four equal quarters — there is no "
                  "September (Q3) payment (R&TC §19136.1; FTB Form 540-ES).", "",
                  "| Installment | Due | Share of remaining | Amount |",
                  "|---|---|---:|---:|"]
        # Share of the REMAINING shortfall, renormalized over the still-open
        # installments — printing the statutory full-year weight next to a
        # renormalized amount (e.g. "30%" beside 100% of the shortfall after
        # Jun 15) misleads.
        ca_weights = {q: w for q, _, w in CA_ES_SCHEDULE}
        open_total = sum(ca_weights[v.quarter] for v in r.ca_vouchers)
        for v in r.ca_vouchers:
            share = (ca_weights[v.quarter] / open_total) if open_total else 0.0
            lines.append(f"| Q{v.quarter} | {v.due_date} | {share:.0%} | "
                         f"{_money(v.amount)} |")
        lines.append("")
        if len(r.ca_vouchers) < len(CA_ES_SCHEDULE):
            lines += ["Earlier installments have closed — the remaining "
                      "shortfall is reallocated across the open "
                      "installments in proportion to their statutory "
                      "weights.", ""]
        lines.append(_CA_VOUCHER_CATCHUP_NOTE)
    lines += ["",
              "## Caveats",
              "- This is a projection, not a filed return. Re-run after any material income change."]
    if r.ca_safe_harbor is not None:
        lines.append("- CA mandatory e-pay (R&TC §19011.5): once any estimated/extension "
                     "payment exceeds $20,000 or total tax liability exceeds $80,000, ALL "
                     "subsequent CA payments must be made electronically (FTB Web Pay) — "
                     "a 1% penalty applies to paper payments.")
    else:
        lines.append("- State estimated-payment scheduling is informational; state rules vary.")
    if f.qbi_over_threshold:
        lines.append(
            "- **QBI over the §199A threshold:** the Form 8995-A W-2-wage/UBIA limits "
            "and SSTB phase-out are NOT modeled — the projected QBI deduction may be "
            "overstated (an SSTB phases to $0), understating projected tax and the "
            "safe-harbor target. Verify against Form 8995-A.")
    if f.schedule_1a_unfetched:
        lines.append(
            "- **Schedule 1-A override applied WITHOUT fetched caps:** a "
            "`forecast_overrides.schedule_1a` amount was subtracted but "
            "`federal.schedule_1a` (the indexed caps/thresholds) is unfetched, so "
            "the raw figure was NOT capped or MAGI-phased — the projected deduction "
            "may exceed the real Schedule 1-A amount, understating projected tax and "
            "the safe-harbor target. Fetch the block via /tax-setup, or enter the "
            "already-capped/phased amount.")
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
        "ca_safe_harbor": (vars(r.ca_safe_harbor)
                           if r.ca_safe_harbor is not None else None),
        "ca_vouchers": [vars(v) for v in r.ca_vouchers],
        "state_no_income_tax": r.state_no_income_tax,
        "no_income_tax_state_code": r.no_income_tax_state_code,
        "state_cache_mismatch": r.state_cache_mismatch,
        "nonresident_income_warning": r.nonresident_income_warning,
        "state_unsupported_note": r.state_unsupported_note,
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
        raise ProjectionError(f"{redact(path)} does not exist")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ProjectionError(f"{redact(path.name)} is malformed: {redact(e)}")


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
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1
    except RolloverError as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    try:
        report = build_report(inputs, profile, tax_law, prior, year, as_of)
    except (ProjectionError, ValueError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0


# State result sections known to the toolkit; extend as more states are added.
STATE_SECTION_KEYS = ["california_540", "florida_state"]

# Which state CODE each section key represents — keep in sync with
# STATE_SECTION_KEYS above. Used by _detect_prior_state_code to tell WHICH
# state a prior section is evidence for, since the two sections are not
# interchangeable (Codex round-15 P2).
_STATE_SECTION_CODES: dict = {"california_540": "CA", "florida_state": "FL"}


def _detect_prior_state(prior_result: dict) -> str | None:
    for key in STATE_SECTION_KEYS:
        if key in (prior_result or {}):
            return key
    return None


def _detect_prior_state_code(prior_result: dict) -> "str | None":
    """The STATE CODE implied by whichever known section is present in
    `prior_result` (via `_STATE_SECTION_CODES`), or None if no known
    section is present or the section isn't in the mapping.

    Distinct from `_detect_prior_state` (which returns the SECTION KEY,
    e.g. "florida_state") — Codex round-15 P2: `project_state_tax`'s
    has-prior-state gate used to treat ANY known section as
    interchangeable proof of "this taxpayer files a state return." Adding
    `florida_state` to `STATE_SECTION_KEYS` made that collapse dangerous: a
    prior `florida_state` section is evidence the taxpayer was an FL
    resident, NOT evidence for a completely unrelated current-year CA (or
    other) cache. With an unusable `resident_state`, the old boolean gate
    would let a prior FL section bypass the residency check for any
    graduated-income cache."""
    section_key = _detect_prior_state(prior_result)
    if section_key is None:
        return None
    return _STATE_SECTION_CODES.get(section_key)


def project_state_tax(proj: ProjectedInputs, prior, tax_law: dict,
                      filing_status: str | None,
                      resident_state: "str | None" = None):
    """Project state tax only when a prior-year state section exists AND
    tax_law carries state brackets. Returns StateProjection or None.

    `resident_state` (profile address.state) backfills the state code when
    the tax_law cache predates the `state.code` field — without it the CA
    540-ES block would silently never fire on a schema-conformant cache
    (reviewer P1). It ALSO substitutes for the prior-state-section gate:
    that gate was only ever a "files this state" proxy, and a first-year /
    missing-archive CA resident still needs the state projection and the
    CA 540-ES schedule (Codex P2 on PR #35) — so the projection runs
    whenever the prior return carries a state section OR the resident
    state matches the configured state law. A KNOWN cache-vs-resident
    mismatch (both sides known, both different) always returns None
    regardless of the prior-state-section gate (round-6 P1).

    `state.type == "none"` ALWAYS wins over brackets content (Codex
    round-12 P1): round-11 taught tax_law_fetcher.check_freshness to flag a
    type-none cache carrying leftover real brackets as a contradiction, but
    this function reads the cache directly and never calls check_freshness
    — so a `code: "FL", type: "none"` cache with leftover CA brackets
    (copied/partially replaced, never stripped) would sail through the
    `if not brackets: return None` guard below and have this function
    invent a Florida tax liability from CA's brackets. The type
    discriminator is checked FIRST and unconditionally short-circuits to
    None; `build_report`'s `_resolve_state_no_income_tax` is the one place
    that decides what to say about a no-tax claim (including flagging a
    corrupted regime-vs-code combination) — this function's only job is to
    never compute a bracket-based liability for a state that says it has
    none.

    A PRESENT-but-INVALID `state.code` (e.g. "FLA") is rejected outright,
    never backfilled (Codex round-14 P2): `_normalize_state_code` collapses
    it to the same `None` a genuinely ABSENT code produces, and `None`
    normally means "safe to backfill the resident's state and trust this
    cache." That's correct for a legacy code-less cache, but a CA-bracket
    cache whose code was corrupted to "FLA" beside an FL profile would
    otherwise have `cache_code` read as unknown, skip the mismatch check
    below (which requires BOTH sides known), get backfilled to the
    resident's "FL", and have CA's real brackets projected and LABELED as
    Florida tax.

    The INVERSE regime contradiction is also rejected (Codex round-15 P1):
    a `code` that IS a legitimate no-tax state for this cache's OWN
    declared year (per `_none_type_allowed`) must never be trusted for
    bracket-based math regardless of what `type` claims — a
    `code: "FL", type: "graduated_income"` cache retaining real CA
    brackets/standard_deduction would otherwise sail past the
    `type == "none"` guard above (which only fires when type actually says
    "none") and have this function invent a Florida tax liability from the
    leftover CA brackets. Checked before brackets are even read."""
    state_cfg = tax_law.get("state") or {}
    if state_cfg.get("type") == "none":
        return None
    if _cache_state_code_present_but_invalid(state_cfg):
        return None
    _regime_code = _normalize_state_code(state_cfg.get("code"))
    if _regime_code is not None and _none_type_allowed(
            _regime_code, tax_law.get("tax_year")):
        return None
    brackets = state_cfg.get("brackets") or []
    if not brackets:
        return None
    resident = _normalize_state_code(resident_state)
    cache_code = _normalize_state_code(state_cfg.get("code"))
    # A known cache-vs-resident mismatch is disqualifying regardless of the
    # has_prior_state gate below: an FL resident with an FL prior-year
    # state section (has_prior_state=True) but a stale CA cache left beside
    # the workspace must NOT have CA brackets projected onto FL income —
    # has_prior_state is only a "files a state return" proxy, not proof the
    # cache is for the RIGHT state (round-6 P1, the graduated-brackets twin
    # of _resolve_state_no_income_tax's no-tax-flag guard below). Unknown
    # resident or unknown cache code falls through unchanged.
    if resident is not None and cache_code is not None and resident != cache_code:
        return None
    # Code resolution order: explicit cache field, then the taxpayer's
    # resident state, then a placeholder (older caches lack `state.code`).
    code = cache_code or resident or "STATE"
    # TN NEVER gets generic bracket math (Codex round-23 P2): post-repeal
    # (TY2021+) TN is type "none" and already short-circuits via the
    # _none_type_allowed inverse-contradiction guard above, so reaching
    # this point with a TN code means a pre-repeal (or year-corrupt) cache
    # carrying the Hall-tax rate as brackets. The Hall tax applied ONLY to
    # interest and dividends — this function builds taxable income from
    # wages/SE/capital gains too, so routing it through ordinary
    # graduated-state math would fabricate a liability for a TY2020 TN
    # wage earner. Pre-repeal TN is UNSUPPORTED (skip, the safe
    # under-statement direction) rather than mismodeled. Checked after the
    # resident backfill so a code-less Hall cache beside a TN resident is
    # caught too.
    if code == "TN":
        return None
    # The prior-section fallback requires the PRIOR SECTION'S OWN state
    # identity to match the cache's ACTUAL declared code, not merely "some
    # known state section exists" (Codex round-15 P2): adding florida_state
    # to STATE_SECTION_KEYS made "has_prior_state" collapse every modeled
    # state into one interchangeable signal — with an unusable resident
    # (e.g. a broken/missing address.state), a prior florida_state section
    # would satisfy this gate for a COMPLETELY UNRELATED current-year CA
    # (or other) cache, publishing that cache's liability with zero
    # evidence the taxpayer is even a CA resident this year.
    # _detect_prior_state_code resolves WHICH state the prior section
    # actually represents. Compared against `cache_code` specifically (not
    # the resolved `code`, which can be the generic "STATE" placeholder
    # when the cache is code-LESS): a code-less legacy cache makes no
    # specific state claim to contradict, so the historical permissive
    # "any known prior section is enough" leniency still applies there —
    # only a cache that names a SPECIFIC, conflicting state code loses it.
    prior_state_code = (_detect_prior_state_code(getattr(prior, "result", {}))
                        if prior is not None else None)
    if cache_code is not None:
        has_prior_state = prior_state_code == cache_code
    else:
        has_prior_state = prior_state_code is not None
    if not has_prior_state and (resident is None or resident != code):
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
    # The deductible half of SE tax is a federal above-the-line adjustment
    # already inside federal AGI — and CA Form 540 STARTS from federal AGI,
    # so the state proxy must subtract it too. Omitting it inflated the
    # §19136.3 high-income test (~$1.01M SE income read as over the $1M
    # cliff when true CA AGI was ~$986K — Codex P2 on PR #35). Mirrors the
    # federal-side compute_se_tax call.
    if proj.self_employment_net > 0:
        fed = tax_law.get("federal", {}) or {}
        se = compute_se_tax(
            proj.self_employment_net,
            ss_wage_base=(fed.get("social_security", {}) or {}).get("wage_base"),
            w2_ss_wages=proj.ss_wages,
        )
        se_deduction = se["deductible_half"]
    else:
        se_deduction = 0.0
    state_agi = (proj.wages + cap_in_income + proj.ordinary_dividends
                 + proj.taxable_interest + proj.other_income
                 + proj.self_employment_net - se_deduction)
    state_ti = max(0.0, state_agi - std)
    tax, _ = compute_bracket_tax(state_ti, brackets)
    # CA mental-health services tax: 1% of taxable income over $1M is part
    # of Form 540 total tax — omitting it understates the safe-harbor
    # target for exactly the high earners the §19136.3 rule forces onto
    # 90%-of-current (Codex P2 on PR #35). Applied only when the cache
    # carries the block with both fields.
    mh = state_cfg.get("mental_health_surcharge") or {}
    mh_rate, mh_threshold = mh.get("rate"), mh.get("threshold")
    if (isinstance(mh_rate, (int, float)) and not isinstance(mh_rate, bool)
            and isinstance(mh_threshold, (int, float))
            and not isinstance(mh_threshold, bool)
            and math.isfinite(mh_rate) and math.isfinite(mh_threshold)
            and state_ti > mh_threshold):
        tax += mh_rate * (state_ti - mh_threshold)
    balance = round(tax - proj.state_withholding - proj.estimated_state_paid, 2)
    return StateProjection(
        code=code, taxable_income=round(state_ti, 2),
        tax=round(tax, 2), withholding=round(proj.state_withholding, 2),
        estimates_paid=round(proj.estimated_state_paid, 2), balance=balance,
        agi=round(state_agi, 2),
    )


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
