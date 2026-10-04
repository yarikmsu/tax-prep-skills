#!/usr/bin/env python3
"""Federal withholding true-up for /tax-withholding.

Read-only: never writes result.json or taxpayer_profile.json. Reads the ACTUAL
computed liability from result.json (form_1040.line_24_total_tax), compares it to
YTD federal withholding + estimates (from projection_inputs.json) projected at the
current pace, and recommends a W-4 4(c) and/or 1040-ES schedule to close the gap.

When result.json carries a computed california_540 section, a CA true-up block is
added: the actual Form 540 tax vs YTD state withholding, the FTB 5805 safe harbor
(incl. the R&TC §19136.3 high-income 90%-only rule), DE 4 per-paycheck extra
withholding, and a 540-ES voucher schedule under CA's 30/40/0/30 weighting (E7).

Reuses projection for the safe-harbor / 4(c) / voucher math and rollover.py for
the prior-year baseline + path/year validation.
"""

import datetime
import json
import math
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.projection import (  # noqa: E402
    CaSafeHarbor,
    ProjectionError,
    Voucher,
    W4Adjustment,
    SafeHarbor,
    _CA_CALENDAR,
    _CA_CATCHUP,
    _NY_CALENDAR,
    _NY_CATCHUP,
    _FEDERAL_CALENDAR,
    _FEDERAL_CATCHUP,
    _CA_ES_HEADING,
    _CA_ES_WEIGHTING,
    _CURRENT_TAX_UNNETTED_NOTE,
    _PRIOR_AGI_THRESHOLD_TEXT,
    _PRIOR_PRONG_PRECONDITION_NOTE,
    _prior_prong_precondition_applies,
    _ca_split_note,
    _canonical_status,
    _confirmed_flag,
    _full_status_key,
    _voucher_dates_note,
    _w4_lever_note,
    _ca_notes,
    _ca_section_computed,
    _ny_section_computed,
    _nc_section_computed,
    _VoucherCalendar,
    _CatchupNote,
    _ca_status,
    _federal_status,
    _money,
    _nonneg_num,
    _num,
    _prior_baseline_lines,
    _prior_numeric,
    _prior_tax_agi,
    _read_json,
    _require_inputs_tax_year,
    _validate_tax_law_year,
    _voucher_catchup_note,
    compute_ca_estimated_vouchers,
    compute_ca_safe_harbor,
    compute_estimated_vouchers,
    compute_ny_estimated_vouchers,
    compute_safe_harbor,
    compute_w4_4c,
    resolve_as_of,
    elapsed_fraction,
    load_prior_baseline,
    paychecks_available_for_year,
    paychecks_remaining,
    parse_as_of,
    validate_pay_frequency,
)
from agents.lib.tax_computations import (  # noqa: E402
    compute_ny_required_annual_payment,
)
from agents.lib.tax_computations.state.registry import BY_CODE as STATE_REGISTRY  # noqa: E402
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_year_arg,
)
from agents.lib.secure_fs import redact  # noqa: E402
from agents.lib.irs_dates import next_business_day_state  # noqa: E402
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    resolve_under_workspace,
    resolve_workspace,
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
class CaTrueUp:
    """CA (Form 540) true-up block — present only when result.json carries a
    computed california_540 section. Mirrors the federal structure: the
    liability target is the ACTUAL computed CA tax, the safe harbor is the
    FTB 5805 test (incl. the R&TC §19136.3 high-income 90%-only rule), and
    the voucher schedules use the CA 30/40/0/30 weighting. The DE 4
    adjustments are the CA analogue of W-4 4(c): extra per-paycheck state
    withholding over the remaining paychecks."""
    liability: float
    ytd_withholding: float
    projected_full_year_withholding: float
    estimates_paid: float
    projected_paid: float
    safe_harbor: CaSafeHarbor
    zero_shortfall: float
    vouchers_penalty: "list[Voucher]"
    vouchers_zero: "list[Voucher]"
    de4_penalty: W4Adjustment
    de4_zero: W4Adjustment
    # Decided here, not in the renderer, so `--json` carries it too — see
    # ProjectionReport.voucher_catchup_note for why that matters.
    voucher_catchup_note: "str | None" = None
    # The CALENDAR note for the SAME dates, read across BOTH columns — see
    # `projection._voucher_dates_note`. The catch-up note above is scoped to
    # the penalty column, which is all $0.00 whenever the CA harbor is covered
    # or under R&TC §19136's floor; the zero-balance column beside it can carry
    # the whole CA balance on a date that has already passed.
    voucher_dates_note: "str | None" = None


@dataclass(frozen=True)
class _NyHarbor:
    """The four fields `_voucher_catchup_note` duck-types over, plus the label
    it pairs a calendar by.

    NOT a fifth safe-harbor class. `_voucher_catchup_note` reads exactly
    `required_annual`, `withholding`, `estimates_paid` and a `jurisdiction`,
    and binds the instalment weights by that label — the guard that stops a
    federal note being run against California's 30/70/70/100. New York's
    requirement is already computed by `compute_ny_required_annual_payment`, so
    what is missing is only the SHAPE that gate reads, and inventing a full
    harbor to carry four numbers would be a second place for the requirement
    to be decided.
    """
    required_annual: float
    withholding: float
    estimates_paid: float
    shortfall: float
    jurisdiction: str = "NY"


@dataclass(frozen=True)
class NyTrueUp:
    """NY (Form IT-201) true-up block — present only when result.json carries a
    computed `new_york_it201` section AND the tax-law cache is New York's.

    ONE VOUCHER COVERS FOUR TAXES. Form IT-2105 carries the State tax, the New
    York City resident tax, the Yonkers surcharge and the MCTMT together, so
    the liability here is IT-201 LINE 60 — the total taxes — and not line 46.
    A true-up built on line 46 alone would under-state a City resident's
    requirement by the whole of their city tax, and a City resident is the
    population this module exists for.

    The same arithmetic reads through to the withholding: `ytd.state_withholding`
    means ALL New York withholding — boxes 17 and 19 together, plus Yonkers —
    because that is what the one voucher is measured against. The skill says so
    where the figure is entered.

    The instalments are EQUAL QUARTERS, not California's 30/40/0/30, and the
    IT-2104 adjustments are the New York analogue of W-4 4(c).
    """
    liability: float
    nyagi: float
    ytd_withholding: float
    projected_full_year_withholding: float
    estimates_paid: float
    projected_paid: float
    required_annual_payment: float
    # {"current_year": ..., "prior_year": ...} — the floors the requirement is
    # the LESSER of. `prior_year` is absent when no prior return supplied one,
    # which is a different fact from it being larger.
    options: dict
    shortfall: float
    covered: bool
    zero_shortfall: float
    vouchers_penalty: "list[Voucher]"
    vouchers_zero: "list[Voucher]"
    it2104_penalty: W4Adjustment
    it2104_zero: W4Adjustment
    # Whatever `compute_ny_required_annual_payment` said — the no-prior-year
    # note above all, which is SAID rather than assumed.
    notes: "tuple[str, ...]" = ()
    # The AMOUNT half of the same table, which shipped without one. Both other
    # jurisdictions carry it, and §685(c) has the cumulative structure it
    # describes: a $20,000 shortfall at October 1 prints $20,000 against the
    # January row — exactly what January asks — while 75% fell due across
    # April, June and September and keeps accruing. The federal table beside it
    # said so and the New York table did not.
    voucher_catchup_note: "str | None" = None
    voucher_dates_note: "str | None" = None


@dataclass(frozen=True)
class NcTrueUp:
    """NC-40 excludes use tax; the D-400 balance includes it."""
    liability: float
    balance_tax: float
    projected_paid: float
    required_annual_payment: float
    shortfall: float
    zero_shortfall: float
    nc4_penalty: W4Adjustment
    nc4_zero: W4Adjustment
    vouchers_penalty: "list[Voucher]"
    vouchers_zero: "list[Voucher]"
    notes: "tuple[str, ...]"
    voucher_dates_note: "str | None" = None


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
    ca: "CaTrueUp | None" = None
    ny: "NyTrueUp | None" = None
    nc: "NcTrueUp | None" = None
    nc_cache_missing: bool = False
    # A computed New York return with no tax-law cache to measure it against.
    # Said out loud rather than left as an absent section: the §685(c) shares
    # are fetched, so without the cache there is no New York requirement to
    # publish — and a report that simply omits the block reads as "no New York
    # estimated tax is due", which is the over-stating direction for a refund
    # and the under-stating one for a payment.
    ny_cache_missing: bool = False
    ny_it203_manual: bool = False
    # The federal schedule's catch-up note (None when it is not true of the
    # table). Only the PENALTY column can carry it: the note is about
    # instalments, and an instalment is measured against the safe harbor.
    voucher_catchup_note: "str | None" = None
    # The federal schedule's CALENDAR note — whether the dates the two columns
    # share are still ahead of `today`. Read across BOTH columns, which is the
    # whole reason it is separate from the note above: that one is scoped to
    # the penalty column and goes silent the moment §6654(e)(1) or a met
    # harbor zeroes it, while the zero-balance column carries the balance on
    # the same passed date — and the blurb over that table sends the reader to
    # exactly that column once no paychecks remain. See
    # `projection._voucher_dates_note`.
    voucher_dates_note: "str | None" = None
    # Which lever the covered year still has, as the lead of "Recommended
    # actions" — see `projection._w4_lever_note`.
    w4_lever_note: "str | None" = None
    # A malformed stored `as_of_date` inside a CLOSED year, omitted for 12-31
    # — see `projection.resolve_as_of`. In the payload as well as the markdown
    # because /tax-withholding reads `--json` first.
    stale_as_of_note: "str | None" = None
    # Whether an `archive/{year-1}/` prior return was supplied at all, and
    # separate from `degraded`: `degraded` tracks only whether an explicit
    # `prior_year_form_2210_tax_base` was given, which is the common NO now
    # that the federal prong requires one, so the two answer different
    # questions and a reader with only `degraded` cannot tell "no prior return
    # exists" from "one exists and the federal composition was not entered".
    #
    # PAYLOAD-ONLY on this side, and that asymmetry with
    # `ProjectionReport.has_prior_archive` is deliberate rather than an
    # oversight — it was previously described as that field's "twin", which
    # implied a renderer branch here that does not exist and had a maintainer
    # looking for one. The projection twin gates a warning that
    # `recurring_from_prior` (interest/dividends pre-filled from the archive)
    # defaulted to $0; this report annualizes no income at all — its target is
    # the computed liability in result.json — so that warning has nothing to
    # warn about here and inventing one would be a gate firing on correct data.
    #
    # The header clarifier one might expect it to drive gates on
    # `ca_prior_resolved` instead, and must: an archive whose CA section is
    # scaffold-only, or whose computed $0 is held back for want of its
    # confirmation, supplies no harbor, and saying it does points the reader at
    # a figure that is not on the page. Presence of an archive is the weaker
    # fact. Both skills document this field, so it is a contract and not spare
    # weight — see tax-withholding/SKILL.md, pinned by test.
    has_prior_archive: bool = False


def build_inputs(proj_inputs: dict, as_of: datetime.date,
                 pay_frequency: str) -> WithholdingInputs:
    frac = elapsed_fraction(as_of)
    if frac <= 0:
        raise WithholdingError("elapsed fraction must be > 0")
    ytd = proj_inputs.get("ytd", {}) or {}
    # Non-negative for the reason projection.annualize's `ann` states: a
    # negative withholding inflates the shortfall, the vouchers and the 4(c).
    ytd_wh = _nonneg_num(ytd.get("federal_withholding"), "ytd.federal_withholding")
    est = proj_inputs.get("estimated_payments_made", {}) or {}
    # Rounded once, here, for the reason the CA twin below states: the
    # displayed figure and the arithmetic must be the SAME cents-rounded
    # value. Rounding only for display left `required - (withholding +
    # estimates_paid)` a cent off the printed shortfall on a fractional-cent
    # input, and `compute_estimated_vouchers` now reconstructs
    # `shortfall + estimates`, so that drift reaches the voucher column too.
    estimates_paid = round(
        _nonneg_num(est.get("federal"), "estimated_payments_made.federal"), 2)
    proj_wh = round(ytd_wh / frac, 2)
    projected_paid = round(proj_wh + estimates_paid, 2)
    return WithholdingInputs(
        ytd_withholding=round(ytd_wh, 2),
        estimates_paid=estimates_paid,
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


@dataclass(frozen=True)
class _StatePayments:
    ytd_withholding: float
    projected_withholding: float
    estimates_paid: float
    projected_paid: float


@dataclass(frozen=True)
class _StateRequirement:
    required_annual: float
    shortfall: float
    balance_tax: float
    fields: dict
    harbor: "CaSafeHarbor | _NyHarbor | None" = None


@dataclass(frozen=True)
class StateTrueUpSpec:
    """State policy around one shared true-up pipeline.

    Section evidence, liability bases, prior-year eligibility and payment
    calendars remain state rules. The pipeline reads inputs, annualizes paid
    amounts, assembles both targets and schedules, and preserves the existing
    per-state report dataclasses and JSON field names.
    """
    code: str
    report_field: str
    tax_field: str
    agi_field: "str | None"
    computed: Callable[[dict], bool]
    requirement: Callable
    report_type: Callable
    vouchers: Callable
    adjustment: Callable
    adjustment_field: str
    calendar: _VoucherCalendar
    catchup: "_CatchupNote | None"
    cache_required: bool = False
    cache_before_read: bool = False
    explicit_payments: bool = False
    round_estimates: bool = True
    round_tax: bool = True
    prior_pair_required: bool = False
    expose_payment_inputs: bool = True
    cache_missing_field: "str | None" = None
    cache_missing_note: "str | None" = None
    caveat: "str | None" = None
    notes_as_list: bool = False
    heading: str = ""
    header_rows: tuple = ()
    header_lines: "Callable | None" = None
    requirement_lines: "Callable | None" = None
    adjustment_heading: str = ""
    schedule_heading: str = ""
    schedule_intro: "str | None" = None
    table_header: str = "| Installment | Due | To avoid penalty | To zero balance |"
    schedule_notes: "Callable | None" = None

    @property
    def section(self) -> str:
        return STATE_REGISTRY[self.code].section


def _read_state_inputs(spec: StateTrueUpSpec, result: dict):
    sec = result.get(spec.section)
    if sec is None:
        return None
    if not isinstance(sec, dict):
        raise WithholdingError(
            f"result.json {spec.section} must be an object, got {type(sec).__name__}")
    if not spec.computed(result):
        return None
    if sec.get(spec.tax_field) is None:
        raise WithholdingError(
            f"{spec.section} has no {spec.tax_field} — run /tax-compute (step 10) "
            f"before the {spec.code} true-up.")
    tax = _num(sec[spec.tax_field], f"{spec.section}.{spec.tax_field}")
    if tax < 0:
        raise WithholdingError(f"{spec.tax_field} must not be negative, got {tax}")
    agi = None
    if spec.agi_field:
        if sec.get(spec.agi_field) is None:
            raise WithholdingError(
                f"{spec.section} has no {spec.agi_field} — required for the "
                f"{spec.code} high-income safe-harbor check; run /tax-compute first.")
        agi = round(_num(sec[spec.agi_field], f"{spec.section}.{spec.agi_field}"), 2)
    return (round(tax, 2) if spec.round_tax else tax), agi


def read_ca_inputs(result: dict) -> "tuple[float, float] | None":
    """Computed Form 540 tax and CA AGI; a template scaffold is skipped."""
    return _read_state_inputs(_STATE_TRUE_UP_SPECS["CA"], result)


def read_ny_inputs(result: dict) -> "tuple[float, float] | None":
    """IT-201 line 60 (State, City, Yonkers and MCTMT) and line 33 NYAGI."""
    return _read_state_inputs(_STATE_TRUE_UP_SPECS["NY"], result)


def _prior_state_tax_agi(spec: StateTrueUpSpec, prior):
    if prior is None:
        return None, None
    result = getattr(prior, "result", {}) or {}
    if not spec.computed(result):
        return None, None
    sec = result.get(spec.section, {}) or {}
    tax = _prior_numeric(sec.get(spec.tax_field), allow_negative=False)
    agi = (_prior_numeric(sec.get(spec.agi_field), allow_negative=True)
           if spec.agi_field else None)
    # NY needs BOTH values for its high-income share. CA independently passes
    # the two values to its existing harbor, and NC has no prior AGI threshold.
    if spec.prior_pair_required and (tax is None or agi is None):
        return None, None
    return tax, agi


def _state_payments(spec: StateTrueUpSpec, inputs: dict, as_of: datetime.date):
    frac = elapsed_fraction(as_of)
    if frac <= 0:
        raise WithholdingError("elapsed fraction must be > 0")
    ytd = inputs.get("ytd", {}) or {}
    estimates = inputs.get("estimated_payments_made", {}) or {}
    if spec.explicit_payments:
        if not isinstance(ytd, dict) or ytd.get("state_withholding") is None:
            raise WithholdingError(f"Record ytd.state_withholding explicitly for {spec.code}")
        if not isinstance(estimates, dict) or estimates.get("state") is None:
            raise WithholdingError("Record estimated_payments_made.state explicitly "
                                   f"for {spec.code}")
    ytd_wh = _nonneg_num(ytd.get("state_withholding"), "ytd.state_withholding")
    paid_estimates = _nonneg_num(estimates.get("state"), "estimated_payments_made.state")
    # CA/NY have always rounded the displayed estimate once before pricing.
    # NC preserves its existing raw input until the combined paid figure.
    if spec.round_estimates:
        paid_estimates = round(paid_estimates, 2)
    projected_wh = round(ytd_wh / frac, 2)
    return _StatePayments(round(ytd_wh, 2), projected_wh, paid_estimates,
                          round(projected_wh + paid_estimates, 2))


def _ca_requirement(spec, result, tax, agi, payments, prior, inputs,
                    filing_status, year, tax_law):
    prior_tax, prior_agi = _prior_state_tax_agi(spec, prior)
    harbor = compute_ca_safe_harbor(
        tax, prior_tax, prior_agi, agi, filing_status,
        withholding=payments.projected_withholding, estimates_paid=payments.estimates_paid)
    return _StateRequirement(harbor.required_annual, harbor.shortfall, tax,
                             {"safe_harbor": harbor}, harbor)


def _ny_requirement(spec, result, tax, agi, payments, prior, inputs,
                    filing_status, year, tax_law):
    prior_tax, prior_agi = _prior_state_tax_agi(spec, prior)
    required = compute_ny_required_annual_payment(
        tax, prior_tax, prior_agi,
        _full_status_key(filing_status, "NY estimated tax"),
        (tax_law.get("state") or {}).get("estimated_tax_safe_harbor"))
    annual = required["required_annual_payment"]
    shortfall = round(max(0.0, annual - payments.projected_paid), 2)
    fields = {"nyagi": agi, "required_annual_payment": annual,
              "options": dict(required["options"]), "shortfall": shortfall,
              "covered": shortfall <= 0, "notes": tuple(required["notes"])}
    harbor = _NyHarbor(annual, payments.projected_withholding, payments.estimates_paid, shortfall)
    return _StateRequirement(annual, shortfall, tax, fields, harbor)


def _nc_observed_due(statutory: datetime.date) -> datetime.date:
    return next_business_day_state(statutory, "NC")


def _nc4_adjustment(shortfall: float, paychecks_left: int) -> W4Adjustment:
    # NC-4 line 2 specifies whole dollars; rounding down misses the target.
    adjustment = compute_w4_4c(shortfall, paychecks_left)
    amount = float(math.ceil(shortfall / paychecks_left)) if paychecks_left > 0 else 0.0
    return replace(adjustment, per_paycheck=amount)


def _nc_vouchers(shortfall, as_of, estimates):
    return compute_estimated_vouchers(shortfall, as_of, estimates, observed=_nc_observed_due)


def _nc_requirement(spec, result, tax, agi, payments, prior, inputs,
                    filing_status, year, tax_law):
    _validate_tax_law_year(tax_law, year)
    state = tax_law.get("state")
    if not isinstance(state, dict) or state.get("code") != "NC":
        raise WithholdingError("NC true-up requires the covered year's NC cache")
    cfg = state.get("estimated_tax")
    if not isinstance(cfg, dict):
        raise WithholdingError("Fetch state.estimated_tax before the NC true-up")
    figures = {}
    for name in ("required_threshold", "current_year_rate", "prior_year_rate"):
        if isinstance(cfg.get(name), bool) or not isinstance(cfg.get(name), (int, float)):
            raise WithholdingError(f"Fetch state.estimated_tax.{name}")
        value = _nonneg_num(cfg[name], f"state.estimated_tax.{name}")
        if value <= 0 or (name.endswith("_rate") and value > 1):
            raise WithholdingError(f"Invalid fetched NC estimated-tax {name}")
        figures[name] = value
    farmer = inputs.get("nc_farmer_fisherman")
    if not isinstance(farmer, bool):
        raise WithholdingError("Answer nc_farmer_fisherman before choosing an NC-40 schedule")
    if farmer:
        raise WithholdingError("Qualifying NC farmers/fishermen require the separate "
                               "January/March worksheet; quarterly targets are refused")
    sec = result[spec.section]
    if sec.get("line_19") is None:
        raise WithholdingError("Computed D-400 requires line_19")
    balance_tax = _nonneg_num(sec["line_19"], "D-400.line_19")
    use_tax = sec.get("line_18_use_tax")
    if use_tax is not None:
        use_tax = _nonneg_num(use_tax, "D-400.line_18_use_tax")
    if abs(balance_tax - round(tax + (use_tax or 0.0), 2)) > 0.01:
        raise WithholdingError("D-400 line 19 must equal line 17 plus answered use tax")
    current = round(tax * figures["current_year_rate"], 2)
    required = current
    notes = ["NC-40 uses income tax after credits (D-400 line 17); use tax "
             "is included only in the balance target (line 19)."]
    confirmed = _confirmed_flag(inputs.get("nc_prior_year_return_filed_12_months_confirmed"),
                                "nc_prior_year_return_filed_12_months_confirmed")
    no_liability = _confirmed_flag(inputs.get("nc_prior_year_no_liability_confirmed"),
                                   "nc_prior_year_no_liability_confirmed")
    prior_tax, _ = _prior_state_tax_agi(spec, prior)
    if no_liability:
        if prior_tax is not None and prior_tax > 0:
            raise WithholdingError("NC no-liability confirmation contradicts the prior D-400")
        required = 0.0
        notes.append("Confirmed no NC liability for the preceding year: subsection (f) exception.")
    elif confirmed and prior_tax is not None:
        required = min(current, round(prior_tax * figures["prior_year_rate"], 2))
    else:
        notes.append("Prior NC filed twelve-month return not confirmed or unavailable; "
                     "current-year share only. This can overstate the payment target.")
    if tax - payments.projected_withholding < figures["required_threshold"]:
        required = 0.0
        notes.append("Income tax less projected withholding is below the fetched NC threshold.")
    shortfall = round(max(0.0, required - payments.projected_paid), 2)
    notes.append("NC-40 dates roll weekends only here; confirm NC legal-holiday dates "
                 "with NCDOR. These annual targets do not erase earlier underpayments: "
                 "D-422 tracks cumulative equal quarters. Reconcile payment dates and pay "
                 "catch-up amounts promptly. Annualized-income and farmer rules are not modeled.")
    if sec.get("line_18_use_tax") is None:
        notes.append("D-400 use tax is unanswered; the balance target may be understated.")
    return _StateRequirement(required, shortfall, balance_tax,
                             {"balance_tax": balance_tax, "required_annual_payment": required,
                              "shortfall": shortfall, "notes": tuple(notes)})


def _ca_requirement_lines(block):
    harbor = block.safe_harbor
    lines = ["### CA safe harbor (FTB 5805)", "",
             "| Test | Amount |", "|---|---:|",
             f"| 90% of current-year CA tax | {_money(harbor.pct90_current)} |"]
    if harbor.prior_based is not None:
        lines.append(f"| {harbor.prior_factor:.0%} of prior-year CA tax | "
                     f"{_money(harbor.prior_based)} |")
    lines += [f"| **Required annual payment** | {_money(harbor.required_annual)} |",
              f"| **Shortfall** | {_money(harbor.shortfall)} |",
              f"| Status | {_ca_status(harbor, tax_is_actual=True)} |", ""]
    return lines + _ca_notes(harbor)


def _ny_requirement_lines(block):
    lines = ["### Required annual payment (Tax Law §685(c))", "",
             "| Test | Amount |", "|---|---:|",
             f"| Current-year share | {_money(block.options['current_year'])} |"]
    if "prior_year" in block.options:
        lines.append(f"| Prior-year share | {_money(block.options['prior_year'])} |")
    lines += [f"| **Required annual payment** (the lesser) | "
              f"{_money(block.required_annual_payment)} |",
              f"| **Shortfall** | {_money(block.shortfall)} |",
              f"| Status | {'covered' if block.covered else 'NOT covered'} |", ""]
    return lines + [f"- {note}" for note in block.notes]


def _nc_header_lines(block):
    return [f"- Income tax after credits: **{_money(block.liability)}**",
            f"- Required annual NC payment: **{_money(block.required_annual_payment)}**",
            f"- Projected withholding and estimates: **{_money(block.projected_paid)}**",
            f"- NC-4 additional withholding per paycheck: "
            f"**{_money(block.nc4_penalty.per_paycheck)}** to meet the requirement, "
            f"**{_money(block.nc4_zero.per_paycheck)}** to reach zero balance.", ""]


def _ca_schedule_notes(block):
    lines = []
    for column, note in (("To avoid penalty", _ca_split_note(
            block.vouchers_penalty, block.safe_harbor.estimates_paid)),
                         ("To zero balance", _ca_split_note(
            block.vouchers_zero, block.safe_harbor.estimates_paid))):
        if note:
            lines += ["", f"**{column}:** {note[0]}"]
    return lines


def _nc_schedule_notes(block):
    return [f"- {note}" for note in block.notes]


_STATE_TRUE_UP_SPECS = {
    "CA": StateTrueUpSpec(
        "CA", "ca", "ca_total_tax", "ca_agi", _ca_section_computed,
        _ca_requirement, CaTrueUp, compute_ca_estimated_vouchers, compute_w4_4c,
        "de4", _CA_CALENDAR, _CA_CATCHUP,
        heading="## California true-up (Form 540)",
        header_rows=(("CA total tax (target, actual computed)", "liability"),
                     ("YTD CA withholding", "ytd_withholding"),
                     ("Projected full-year CA withholding (current pace)", "projected_full_year_withholding"),
                     ("CA estimates paid", "estimates_paid"),
                     ("**Projected CA paid**", "projected_paid")),
        requirement_lines=_ca_requirement_lines,
        adjustment_heading="### DE 4 per-paycheck extra CA withholding",
        schedule_heading=_CA_ES_HEADING, schedule_intro=_CA_ES_WEIGHTING,
        schedule_notes=_ca_schedule_notes,
        caveat="- State true-up covers CA only, anchored to the actual computed "
               "Form 540 tax. CA mandatory e-pay (R&TC §19011.5): once any "
               "estimated/extension payment exceeds $20,000 or total tax "
               "liability exceeds $80,000, ALL subsequent CA payments must be "
               "electronic (FTB Web Pay) — a 1% penalty applies to paper payments."),
    "NY": StateTrueUpSpec(
        "NY", "ny", "line_60_total_taxes", "line_33_nyagi",
        _ny_section_computed, _ny_requirement, NyTrueUp, compute_ny_estimated_vouchers,
        compute_w4_4c, "it2104", _NY_CALENDAR, _NY_CATCHUP,
        cache_required=True, prior_pair_required=True, cache_missing_field="ny_cache_missing",
        notes_as_list=True,
        heading="## New York true-up (Form IT-201)",
        header_rows=(("NY total taxes (IT-201 line 60 — State, City, Yonkers and MCTMT)", "liability"),
                     ("New York AGI (line 33)", "nyagi"),
                     ("YTD New York withholding (all of it)", "ytd_withholding"),
                     ("Projected full-year NY withholding (current pace)", "projected_full_year_withholding"),
                     ("NY estimates paid", "estimates_paid"),
                     ("**Projected NY paid**", "projected_paid")),
        requirement_lines=_ny_requirement_lines,
        adjustment_heading="### IT-2104 per-paycheck extra NY withholding",
        schedule_heading="### IT-2105 installment schedule (four EQUAL quarters)",
        schedule_intro="One voucher covers the State tax, the New York City "
                       "resident tax, the Yonkers surcharge and the MCTMT "
                       "together — which is why the target above is line 60 and not line 46.",
        caveat="- The New York true-up reads `ytd.state_withholding` as ALL "
               "New York withholding — W-2 box 17, box 19 for a New York "
               "City locality, and any Yonkers amount together — because "
               "one Form IT-2105 voucher is measured against all of them. "
               "Entering the state figure alone over-states the shortfall.",
        cache_missing_note="- **No New York true-up was computed**: result.json carries a "
                           "computed IT-201 but no tax-law cache was supplied, and the "
                           "Tax Law §685(c) shares and thresholds are FETCHED — they are "
                           "New York's own and may diverge from the federal ones. Re-run "
                           "with the year's `tax_law_{year}.json`. This is NOT a finding "
                           "that no New York estimated tax is due."),
    "NC": StateTrueUpSpec(
        "NC", "nc", "line_17", None, _nc_section_computed,
        _nc_requirement, NcTrueUp, _nc_vouchers, _nc4_adjustment, "nc4",
        _VoucherCalendar(form="NC-40", accrual="G.S. 105-163.15(b)",
                         bill="on Form D-422 or the NCDOR bill", step="the NC worksheet",
                         observed_due=_nc_observed_due,
                         due_date_caveat=" Confirm NC's return deadline with NCDOR; this lookup is federal."),
        None, cache_required=True, cache_before_read=True, explicit_payments=True,
        round_estimates=False, round_tax=False, expose_payment_inputs=False,
        cache_missing_field="nc_cache_missing",
        heading="## North Carolina true-up (Form D-400)", header_lines=_nc_header_lines,
        schedule_heading="### NC-40 schedule (equal quarters)",
        table_header="| Installment | Due | Requirement shortfall | Zero balance |",
        schedule_notes=_nc_schedule_notes,
        cache_missing_note="- **No NC true-up computed:** the completed D-400 requires "
                           "the covered year's fetched NC cache. This does not mean no NC payment is due."),
}


def _build_state_true_up(spec: StateTrueUpSpec, result: dict, inputs: dict, prior,
                         filing_status, as_of, paychecks_left, year, tax_law=None, today=None):
    if spec.cache_before_read and (not spec.computed(result) or not tax_law):
        return None
    state_inputs = _read_state_inputs(spec, result)
    if state_inputs is None or (spec.cache_required and not tax_law):
        return None
    tax, agi = state_inputs
    payments = _state_payments(spec, inputs, as_of)
    requirement = spec.requirement(spec, result, tax, agi, payments, prior, inputs,
                                   filing_status, year, tax_law)
    zero = round(max(0.0, requirement.balance_tax - payments.projected_paid), 2)
    penalty_vouchers = spec.vouchers(requirement.shortfall, as_of, payments.estimates_paid)
    zero_vouchers = spec.vouchers(zero, as_of, payments.estimates_paid)
    fields = dict(requirement.fields)
    fields.update(liability=tax, projected_paid=payments.projected_paid, zero_shortfall=zero,
                  vouchers_penalty=penalty_vouchers, vouchers_zero=zero_vouchers,
                  voucher_dates_note=_voucher_dates_note(
                      spec.calendar, [penalty_vouchers], year, today,
                      balance_schedules=[zero_vouchers]))
    fields[spec.adjustment_field + "_penalty"] = spec.adjustment(
        requirement.shortfall, paychecks_left)
    fields[spec.adjustment_field + "_zero"] = spec.adjustment(zero, paychecks_left)
    if spec.expose_payment_inputs:
        fields.update(ytd_withholding=payments.ytd_withholding,
                      projected_full_year_withholding=payments.projected_withholding,
                      estimates_paid=payments.estimates_paid)
    if spec.catchup:
        fields["voucher_catchup_note"] = _voucher_catchup_note(
            spec.catchup, penalty_vouchers, requirement.harbor, as_of)
    return spec.report_type(**fields)


def build_ca_true_up(result: dict, proj_inputs: dict, prior,
                     filing_status: "str | None", as_of: datetime.date,
                     paychecks_left: int, year: int,
                     today: "datetime.date | None" = None) -> "CaTrueUp | None":
    _canonical_status(filing_status, "CA true-up")
    return cast("CaTrueUp | None", _build_state_true_up(
        _STATE_TRUE_UP_SPECS["CA"], result, proj_inputs, prior, filing_status,
        as_of, paychecks_left, year, today=today))


def build_ny_true_up(result: dict, proj_inputs: dict, prior,
                     filing_status: "str | None", as_of: datetime.date,
                     paychecks_left: int, year: int, tax_law: "dict | None",
                     today: "datetime.date | None" = None) -> "NyTrueUp | None":
    _canonical_status(filing_status, "NY true-up")
    return cast("NyTrueUp | None", _build_state_true_up(
        _STATE_TRUE_UP_SPECS["NY"], result, proj_inputs, prior, filing_status,
        as_of, paychecks_left, year, tax_law, today))


def build_nc_true_up(result: dict, proj_inputs: dict, prior,
                     filing_status: "str | None", as_of: datetime.date,
                     paychecks_left: int, year: int, tax_law: "dict | None",
                     today: "datetime.date | None" = None) -> "NcTrueUp | None":
    _canonical_status(filing_status, "NC true-up")
    return cast("NcTrueUp | None", _build_state_true_up(
        _STATE_TRUE_UP_SPECS["NC"], result, proj_inputs, prior, filing_status,
        as_of, paychecks_left, year, tax_law, today))


def build_report(result: dict, proj_inputs: dict, prior, year: int,
                 as_of: datetime.date,
                 today: "datetime.date | None" = None,
                 tax_law: "dict | None" = None) -> TrueUpReport:
    """Build the report. `today` is the real date, NOT the as-of date.

    Optional, and omitting it omits the calendar notes rather than guessing
    at them — see `projection.build_report`, which states the whole of why.

    NY and NC read their own fetched estimated-payment rules from `tax_law`.
    Without it their blocks are omitted with explicit missing-cache notes;
    federal and CA advice retains the existing cache-independent behavior.
    """
    from agents.lib.tax_computations.state.registry import selected_ny_return
    try:
        ny_form = selected_ny_return(result)
    except ValueError as exc:
        raise WithholdingError(str(exc)) from None
    pay_frequency = validate_pay_frequency(proj_inputs.get("pay_frequency", "biweekly"))
    liability = read_liability(result, year)
    inputs = build_inputs(proj_inputs, as_of, pay_frequency)
    inputs = replace(
        inputs,
        paychecks_remaining=paychecks_available_for_year(
            pay_frequency, year, today, inputs.paychecks_remaining))

    # Validated eagerly, regardless of degraded state — mirrors
    # projection.build_report's own pattern. compute_safe_harbor itself only
    # validates on its non-degraded branch (degraded mode has no use for
    # filing_status, so it deliberately does not demand one), which left the
    # common degraded case here reaching compute_ca_safe_harbor's own
    # unconditional check only when a CA section exists — a malformed status
    # with no CA section went unvalidated end to end.
    filing_status = _canonical_status(
        (result.get("meta", {}) or {}).get("filing_status"), "withholding true-up")
    prior_tax, prior_agi = _prior_tax_agi(
        proj_inputs.get("prior_year_form_2210_tax_base"),
        proj_inputs.get("prior_year_form_2210_agi"),
        _confirmed_flag(proj_inputs.get("prior_year_zero_liability_confirmed"),
                        "prior_year_zero_liability_confirmed"),
        _confirmed_flag(
            proj_inputs.get("prior_year_return_filed_12_months_confirmed"),
            "prior_year_return_filed_12_months_confirmed"))
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
        safe_harbor.shortfall, as_of, inputs.estimates_paid)
    vouchers_zero = compute_estimated_vouchers(
        zero_shortfall, as_of, inputs.estimates_paid)

    state_reports = {
        spec.report_field: _build_state_true_up(
            spec, result, proj_inputs, prior, filing_status, as_of,
            inputs.paychecks_remaining, year, tax_law, today)
        for spec in _STATE_TRUE_UP_SPECS.values()
    }
    cache_missing = {
        spec.cache_missing_field: state_reports[spec.report_field] is None
        and not tax_law and spec.computed(result)
        for spec in _STATE_TRUE_UP_SPECS.values() if spec.cache_missing_field
    }

    return TrueUpReport(
        year=year, as_of=as_of.strftime("%m-%d"), pay_frequency=pay_frequency,
        liability=liability,
        baseline_source=("projection_inputs.json completed Form 2210 line-8 base"
                         if prior_tax is not None else None),
        degraded=safe_harbor.degraded, has_prior_archive=prior is not None,
        inputs=inputs, safe_harbor=safe_harbor,
        penalty_target=penalty_target, zero_target=zero_target,
        vouchers_penalty=vouchers_penalty, vouchers_zero=vouchers_zero,
        w4_penalty=w4_penalty, w4_zero=w4_zero,
        ca=state_reports["ca"], ny=state_reports["ny"],
        nc=state_reports[_STATE_TRUE_UP_SPECS["NC"].report_field],
        ny_cache_missing=cache_missing.get("ny_cache_missing", False),
        nc_cache_missing=cache_missing.get("nc_cache_missing", False),
        ny_it203_manual=ny_form == "IT203",
        voucher_catchup_note=_voucher_catchup_note(
            _FEDERAL_CATCHUP, vouchers_penalty, safe_harbor, as_of),
        voucher_dates_note=_voucher_dates_note(
            _FEDERAL_CALENDAR, [vouchers_penalty], year, today,
            balance_schedules=[vouchers_zero]),
        # A balance in ANY supported state keeps a payment target alive after
        # the federal withholding lever closes. Take the largest state gap.
        w4_lever_note=_w4_lever_note(
            inputs.paychecks_remaining, year, zero_shortfall,
            max((block.zero_shortfall for block in state_reports.values()
                 if block is not None), default=0.0)),
    )


def _render_state_true_up(spec: StateTrueUpSpec, block) -> list[str]:
    """Common payment/adjustment/schedule layout around state policy rows."""
    lines = ["", spec.heading, ""]
    if spec.header_lines:
        lines += spec.header_lines(block)
    else:
        lines += ["| Item | Amount |", "|---|---:|"]
        lines += [f"| {label} | {_money(getattr(block, field))} |"
                  for label, field in spec.header_rows]
        lines += [""]
        if spec.requirement_lines:
            lines += spec.requirement_lines(block)
        penalty = getattr(block, spec.adjustment_field + "_penalty")
        zero = getattr(block, spec.adjustment_field + "_zero")
        lines += ["", spec.adjustment_heading,
                  f"- To avoid penalty (safe harbor): **{_money(penalty.per_paycheck)}"
                  f"/paycheck** over {penalty.paychecks_remaining} remaining paychecks",
                  f"- To reach zero balance: **{_money(zero.per_paycheck)}/paycheck**", ""]
    lines += [spec.schedule_heading, ""]
    if spec.schedule_intro:
        lines += [spec.schedule_intro, ""]
    lines += [spec.table_header, "|---|---|---:|---:|"]
    for penalty, zero in zip(block.vouchers_penalty, block.vouchers_zero):
        lines.append(f"| Q{penalty.quarter} | {penalty.due_date} | "
                     f"{_money(penalty.amount)} | {_money(zero.amount)} |")
    if spec.schedule_notes:
        lines += spec.schedule_notes(block)
    # Dates belong to both columns; the catch-up note belongs to penalty only.
    for note in (block.voucher_dates_note, getattr(block, "voucher_catchup_note", None)):
        if note:
            lines += ["", note]
    return lines


def render_markdown(r: TrueUpReport) -> str:
    i = r.inputs
    lines = [f"# Withholding True-Up — TY{r.year} (as of {r.as_of})", ""]
    if r.stale_as_of_note:
        lines += [r.stale_as_of_note, ""]

    lines += ["## Assumptions", ""]
    lines.append("- Liability target is the **actual computed** tax from result.json "
                 "(form_1040 line 24), not an annualized estimate.")
    lines.append(f"- Elapsed fraction of year: {i.elapsed_fraction:.0%}")
    lines.append(f"- Pay frequency: {r.pay_frequency} "
                 f"({i.paychecks_remaining} paychecks remaining)")
    lines.append("- Projected full-year withholding assumes the rest of the year "
                 "continues at the YTD withholding rate.")
    # Shared with projection.render_markdown — see `_prior_baseline_lines` for
    # why "none" is not "no archive" and what the CA gate is doing. Only the
    # attribute path to the CA harbor differs between the two reports.
    lines += _prior_baseline_lines(
        r.baseline_source, r.year,
        ca_prior_resolved=(r.ca is not None
                           and r.ca.safe_harbor.prior_based is not None),
        degraded=r.degraded)
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
    # Only the PENALTY row can be excused by §6654(e)(1), so only it reads its
    # status through `_federal_status` — the zero-balance row tracks the tax
    # actually due and the floor says nothing about that. Sharing the helper
    # with `projection.render_markdown` is the point: a bare "covered" against
    # a visible balance owed reads as a bug in the report, and one sentence in
    # one place is what keeps the two reports from explaining it differently.
    # `tax_is_actual=True` is what the two reports do NOT share. /tax-project
    # annualizes the tax; here the tax is the computed liability stated two
    # tables up as "the **actual computed** tax", and the withholding pace is
    # the annualized side. The shared sentence would otherwise point this
    # reader at the one input that cannot move.
    for t, label, status in (
            (r.penalty_target, "Safe harbor (avoid penalty)",
             _federal_status(r.safe_harbor, tax_is_actual=True)),
            (r.zero_target, "Zero balance (full liability)",
             "covered" if r.zero_target.covered else "underpaid")):
        lines.append(
            f"| {label} | {_money(t.target_amount)} | {_money(t.projected_paid)} | "
            f"{_money(t.shortfall)} | {status} |")
    lines.append("")
    if r.safe_harbor.current_tax_unnetted:
        lines += [_CURRENT_TAX_UNNETTED_NOTE, ""]
    if r.safe_harbor.prior_agi_missing:
        lines += ["- **Prior-year AGI unavailable** — assumed at or below the "
                  f"{_PRIOR_AGI_THRESHOLD_TEXT} threshold (100% factor); if "
                  "prior AGI exceeded it, the 110% harbor applies and the "
                  "safe-harbor target shown may be UNDERSTATED.", ""]
    if _prior_prong_precondition_applies(r.safe_harbor):
        lines += [_PRIOR_PRONG_PRECONDITION_NOTE, ""]

    # The lead names the lever the year still HAS — `projection._w4_lever_note`,
    # shared with /tax-project so the two cannot describe §6654(g)(1)
    # differently. Where no payroll remains it says the opposite, which is what
    # the 4(c) figures immediately below then read $0.00 for.
    lines += ["## Recommended actions", ""]
    if r.w4_lever_note:
        lines += [r.w4_lever_note, ""]
    lines += ["### W-4 line 4(c) per-paycheck extra withholding",
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
    # DATES first, then AMOUNTS. The dates belong to the whole table and the
    # closed-accrual half of that note contradicts the catch-up note below it
    # in as many words, which only lands if the reader meets it first.
    if r.voucher_dates_note:
        lines += ["", r.voucher_dates_note]
    # The penalty column is the one the catch-up note is about: it targets the
    # safe harbor, which is what an instalment is measured against. This
    # renderer shipped without the note that `projection.render_markdown`
    # carries, so the same under-stated earliest voucher reached
    # /tax-withholding's reader bare — and /tax-safe-harbor step 0, which hands
    # workspace users to BOTH tools, told them each one's table admits the
    # simplification.
    if r.voucher_catchup_note:
        lines += ["", r.voucher_catchup_note]
    for spec in _STATE_TRUE_UP_SPECS.values():
        block = getattr(r, spec.report_field)
        if block is not None:
            lines += _render_state_true_up(spec, block)
    lines += ["",
              "## Caveats",
              "- This is a planning projection, not a filed return. Re-run after any "
              "material income or withholding change."]
    for spec in _STATE_TRUE_UP_SPECS.values():
        if getattr(r, spec.report_field) is not None and spec.caveat:
            lines.append(spec.caveat)
    if r.ny_it203_manual:
        lines += ["> **NY IT-203 payment targets require manual worksheets:** future NY withholding, residency changes and prior-year resident eligibility cannot be inferred from mixed-state YTD totals. No NY target or refund is forecast here.", ""]
    for spec in _STATE_TRUE_UP_SPECS.values():
        if (spec.cache_missing_field and getattr(r, spec.cache_missing_field)
                and spec.cache_missing_note):
            lines.append(spec.cache_missing_note)
    if (not r.ny_it203_manual
            and not any(getattr(r, spec.report_field) is not None
                        or (spec.cache_missing_field and getattr(r, spec.cache_missing_field))
                        for spec in _STATE_TRUE_UP_SPECS.values())):
        codes = list(_STATE_TRUE_UP_SPECS)
        coverage = ", ".join(codes[:-1]) + " and " + codes[-1]
        lines.append("- Federal only; no computed supported state section in result.json — "
                     f"the state withholding true-up is covered for {coverage} returns.")
    lines.append("")
    return "\n".join(lines) + "\n"


def _state_to_dict(spec: StateTrueUpSpec, block):
    if block is None:
        return None
    payload = asdict(block)
    if spec.notes_as_list:
        payload["notes"] = list(block.notes)
    return payload


def _report_to_dict(r: TrueUpReport) -> dict:
    return {
        "year": r.year, "as_of": r.as_of, "pay_frequency": r.pay_frequency,
        "liability": r.liability, "baseline_source": r.baseline_source,
        "has_prior_archive": r.has_prior_archive,
        "degraded": r.degraded, "inputs": vars(r.inputs),
        "safe_harbor": vars(r.safe_harbor),
        "penalty_target": vars(r.penalty_target),
        "zero_target": vars(r.zero_target),
        "vouchers_penalty": [vars(v) for v in r.vouchers_penalty],
        "vouchers_zero": [vars(v) for v in r.vouchers_zero],
        "voucher_catchup_note": r.voucher_catchup_note,
        "voucher_dates_note": r.voucher_dates_note,
        "w4_lever_note": r.w4_lever_note,
        "stale_as_of_note": r.stale_as_of_note,
        "w4_penalty": vars(r.w4_penalty), "w4_zero": vars(r.w4_zero),
        "ny_cache_missing": r.ny_cache_missing,
        "ny_it203_manual": r.ny_it203_manual,
        **{spec.cache_missing_field: getattr(r, spec.cache_missing_field)
           for spec in _STATE_TRUE_UP_SPECS.values() if spec.cache_missing_field},
        **{spec.report_field: _state_to_dict(spec, getattr(r, spec.report_field))
           for spec in _STATE_TRUE_UP_SPECS.values()},
    }


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.withholding")
    parser.add_argument("--result", required=True, help="path to result.json")
    parser.add_argument("--inputs", required=True, help="path to projection_inputs.json")
    add_workspace_argument(parser)
    parser.add_argument("--year", required=True, help="current tax year")
    parser.add_argument(
        "--as-of", dest="as_of", default=None,
        help="MM-DD (default: today, or 12-31 once the year has closed; a valid "
             "stored as_of_date is preserved as YTD provenance in any year)")
    parser.add_argument("--today", default=None,
                        help="override today's date as YYYY-MM-DD (testing only)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args(argv)

    try:
        year = _validate_year_arg(args.year)
        workspace = resolve_workspace(args.workspace).resolve()
        result_path = resolve_under_workspace(args.result, workspace)
        inputs_path = resolve_under_workspace(args.inputs, workspace)
        result = _read_json(result_path)
        proj_inputs = _read_json(inputs_path)

        # Shared with the /tax-project CLI: same file, same four year-bound
        # prior-year fields, so the guard is one function rather than two
        # copies. Raises ProjectionError, which this block already catches.
        _require_inputs_tax_year(proj_inputs, year)

        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
        as_of_str, stale_as_of_note = resolve_as_of(
            args.as_of, proj_inputs.get("as_of_date"), year, today)
        as_of = parse_as_of(as_of_str, year)
        if as_of > today:
            raise WithholdingError(
                f"as_of {as_of.isoformat()} is in the future (today {today.isoformat()})"
            )

        prior = load_prior_baseline(workspace / "archive" / str(year - 1), year - 1)

        # NY and NC read their own fetched shares/thresholds. The federal and
        # California payment rules here are cache-independent. ABSENT is tolerated, so a workspace that never ran /tax-setup
        # still gets the federal report it always got; a New York return then
        # sets `ny_cache_missing` and the report SAYS the cache was missing,
        # rather than printing no New York block and reading as "nothing is
        # due". PRESENT and for the wrong year is refused, because a cache
        # declaring another year carries another year's thresholds.
        tax_law_path = workspace / f"tax_law_{year}.json"
        tax_law = _read_json(tax_law_path) if tax_law_path.exists() else None
        if tax_law is not None:
            _validate_tax_law_year(tax_law, year)
    except (WorkspaceError, WithholdingError, ProjectionError, RolloverError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    try:
        report = build_report(result, proj_inputs, prior, year, as_of,
                              today=today, tax_law=tax_law)
    except (WithholdingError, ProjectionError, ValueError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    if stale_as_of_note:
        report = replace(report, stale_as_of_note=stale_as_of_note)

    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
