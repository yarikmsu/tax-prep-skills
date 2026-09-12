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

Reuses projection.py for the safe-harbor / 4(c) / voucher math and rollover.py for
the prior-year baseline + path/year validation.
"""

import datetime
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

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
    _ca_status,
    _federal_status,
    _money,
    _nonneg_num,
    _num,
    _prior_baseline_lines,
    _prior_ca_tax_agi,
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
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_year_arg,
)
from agents.lib.secure_fs import redact  # noqa: E402
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
    # A computed New York return with no tax-law cache to measure it against.
    # Said out loud rather than left as an absent section: the §685(c) shares
    # are fetched, so without the cache there is no New York requirement to
    # publish — and a report that simply omits the block reads as "no New York
    # estimated tax is due", which is the over-stating direction for a refund
    # and the under-stating one for a payment.
    ny_cache_missing: bool = False
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


def read_ca_inputs(result: dict) -> "tuple[float, float] | None":
    """Return (ca_total_tax, ca_agi) from result.json's california_540 section,
    or None when the return has no CA section (non-CA taxpayer → the CA
    true-up block is skipped).

    Once the section exists, both figures are REQUIRED: a CA section without
    a computed total tax means /tax-compute step 10 didn't finish, and a
    missing ca_agi would silently disable the R&TC §19136.3 high-income
    check — which UNDERSTATES the required payment for a $1M-AGI taxpayer
    (the wrong bias direction) — so both hard-error instead of degrading.
    ca_agi may legitimately be negative (a loss year); ca_total_tax may not.
    """
    sec = result.get("california_540")
    if sec is None:
        return None
    if not isinstance(sec, dict):
        raise WithholdingError(
            f"result.json california_540 must be an object, "
            f"got {type(sec).__name__}")
    # result_template.json ships an ALL-ZERO california_540 scaffold and
    # /tax-setup copies it before /tax-compute step 10 runs — treating that
    # scaffold as a computed $0 CA return would print a confident
    # "required $0 / covered" block for a taxpayer with real CA liability.
    # The gate demands COMPUTED evidence (non-zero compute-output field),
    # not mere section non-emptiness — parsed W-2 Box 17 withholding in
    # ca_withholding_box17 must not vouch for untouched template zeros.
    # A legitimate $0-tax CA return still carries a non-zero
    # ca_standard_deduction / ca_agi.
    if not _ca_section_computed(result):
        return None
    raw_tax = sec.get("ca_total_tax")
    if raw_tax is None:
        raise WithholdingError(
            "california_540 has no ca_total_tax — run /tax-compute (step 10) "
            "before the CA true-up.")
    tax = _num(raw_tax, "california_540.ca_total_tax")
    if tax < 0:
        raise WithholdingError(f"ca_total_tax must not be negative, got {tax}")
    raw_agi = sec.get("ca_agi")
    if raw_agi is None:
        raise WithholdingError(
            "california_540 has no ca_agi — required for the R&TC §19136.3 "
            "high-income safe-harbor check; run /tax-compute (step 10) first.")
    agi = _num(raw_agi, "california_540.ca_agi")
    return round(tax, 2), round(agi, 2)


def read_ny_inputs(result: dict) -> "tuple[float, float] | None":
    """(IT-201 line 60 total taxes, IT-201 line 33 NYAGI), or None for a
    non-New-York return.

    LINE 60, NOT LINE 46. One Form IT-2105 voucher covers the State tax, the
    New York City resident tax, the Yonkers surcharge and the MCTMT together,
    so the requirement is measured against all four. Building this on line 46
    would under-state a City resident's requirement by the whole of their city
    tax — and a City resident is the population this module exists for.

    NYAGI may legitimately be NEGATIVE (line 33 has no floor, and a Schedule C
    loss, a K-1 loss or an NOL carryforward reaches it routinely); the tax may
    not. A missing figure HARD-ERRORS rather than degrading, the same way the
    California twin does: the §685(c) threshold is keyed to NYAGI, and assuming
    it is below one under-states the requirement for exactly the taxpayers the
    higher share is aimed at.
    """
    sec = result.get("new_york_it201")
    if sec is None:
        return None
    if not isinstance(sec, dict):
        raise WithholdingError(
            f"result.json new_york_it201 must be an object, "
            f"got {type(sec).__name__}")
    # A template scaffold is not a computed $0 return. `_ny_section_computed`
    # demands a non-zero compute-OUTPUT field, so parsed W-2 box 17 sitting in
    # line 72 cannot vouch for untouched zeros in lines 33, 39 and 46 — the
    # same guard, and the same reasoning, as the California side.
    if not _ny_section_computed(result):
        return None
    raw_tax = sec.get("line_60_total_taxes")
    if raw_tax is None:
        raise WithholdingError(
            "new_york_it201 has no line_60_total_taxes — run /tax-compute "
            "(step 10) before the New York true-up.")
    tax = _num(raw_tax, "new_york_it201.line_60_total_taxes")
    if tax < 0:
        raise WithholdingError(
            f"line_60_total_taxes must not be negative, got {tax}")
    raw_nyagi = sec.get("line_33_nyagi")
    if raw_nyagi is None:
        raise WithholdingError(
            "new_york_it201 has no line_33_nyagi — required for the Tax Law "
            "§685(c) high-income share; run /tax-compute (step 10) first.")
    return round(tax, 2), round(_num(raw_nyagi, "new_york_it201.line_33_nyagi"), 2)


def _prior_ny_tax_nyagi(prior):
    """(prior IT-201 line 60, prior line 33) from the archived section, or
    (None, None) when there is no computed one.

    An all-zero template scaffold must not become a $0 prior-year tax: that
    would make ANY current shortfall "covered" through the prior-year floor.
    `_ny_section_computed` is what separates a computed $0 from an untouched
    template, and it is the only guard here that can be wrong in the
    under-stating direction.
    """
    if prior is None:
        return None, None
    result = getattr(prior, "result", {}) or {}
    if not _ny_section_computed(result):
        return None, None
    sec = result.get("new_york_it201", {}) or {}
    tax = _prior_numeric(sec.get("line_60_total_taxes"), allow_negative=False)
    nyagi = _prior_numeric(sec.get("line_33_nyagi"), allow_negative=True)
    if tax is None or nyagi is None:
        # BOTH or NEITHER. `compute_ny_required_annual_payment` REFUSES a
        # prior-year tax handed over without its NYAGI, and it is right to:
        # the higher share applies above a NYAGI threshold, so assuming the
        # lower one under-states the requirement for exactly the taxpayers it
        # is aimed at. Dropping the pair here means the current-year share is
        # the only floor, which over-states — the degrade direction.
        return None, None
    return tax, nyagi


def build_ny_true_up(result: dict, proj_inputs: dict, prior,
                     filing_status: "str | None", as_of: datetime.date,
                     paychecks_left: int, year: int, tax_law: "dict | None",
                     today: "datetime.date | None" = None) -> "NyTrueUp | None":
    """Assemble the New York true-up block, or None for a non-NY return.

    `tax_law` is required for New York and for no other jurisdiction here: the
    §685(c) shares and thresholds are FETCHED, deliberately not shared with the
    federal constants, because they are New York's and may diverge. Without the
    cache this returns None rather than guessing — and the caller says why,
    rather than leaving a New York taxpayer's report silently state-less.
    """
    ny_inputs = read_ny_inputs(result)
    if ny_inputs is None:
        return None
    if not tax_law:
        return None
    ny_tax, nyagi = ny_inputs
    frac = elapsed_fraction(as_of)
    if frac <= 0:
        raise WithholdingError("elapsed fraction must be > 0")
    ytd = proj_inputs.get("ytd", {}) or {}
    ytd_state_wh = _nonneg_num(ytd.get("state_withholding"), "ytd.state_withholding")
    est = proj_inputs.get("estimated_payments_made", {}) or {}
    state_estimates = round(
        _nonneg_num(est.get("state"), "estimated_payments_made.state"), 2)
    proj_state_wh = round(ytd_state_wh / frac, 2)
    projected_paid = round(proj_state_wh + state_estimates, 2)

    prior_tax, prior_nyagi = _prior_ny_tax_nyagi(prior)
    required = compute_ny_required_annual_payment(
        ny_tax, prior_tax, prior_nyagi,
        # `_full_status_key`, not `_canonical_status(...).lower()`. The two
        # agree on every status except QSS, which the hand-rolled form leaves
        # as "qss" and the helper maps to "mfj" — harmless while
        # `compute_ny_required_annual_payment` only tests `== "mfs"`, and the
        # one that silently misses the day it grows an MFJ-keyed lookup. Every
        # other New York entry point in this repository takes the helper's
        # answer.
        _full_status_key(filing_status, "NY estimated tax"),
        (tax_law.get("state") or {}).get("estimated_tax_safe_harbor"))
    required_annual = required["required_annual_payment"]
    shortfall = round(max(0.0, required_annual - projected_paid), 2)
    zero_shortfall = round(max(0.0, ny_tax - projected_paid), 2)

    vouchers_penalty = compute_ny_estimated_vouchers(
        shortfall, as_of, state_estimates)
    vouchers_zero = compute_ny_estimated_vouchers(
        zero_shortfall, as_of, state_estimates)
    return NyTrueUp(
        liability=ny_tax,
        nyagi=nyagi,
        ytd_withholding=round(ytd_state_wh, 2),
        projected_full_year_withholding=proj_state_wh,
        estimates_paid=state_estimates,
        projected_paid=projected_paid,
        required_annual_payment=required_annual,
        options=dict(required["options"]),
        shortfall=shortfall,
        covered=shortfall <= 0,
        zero_shortfall=zero_shortfall,
        vouchers_penalty=vouchers_penalty,
        vouchers_zero=vouchers_zero,
        it2104_penalty=compute_w4_4c(shortfall, paychecks_left),
        it2104_zero=compute_w4_4c(zero_shortfall, paychecks_left),
        notes=tuple(required["notes"]),
        voucher_catchup_note=_voucher_catchup_note(
            _NY_CATCHUP, vouchers_penalty,
            _NyHarbor(required_annual=required_annual,
                      withholding=proj_state_wh,
                      estimates_paid=state_estimates,
                      shortfall=shortfall),
            as_of),
        voucher_dates_note=_voucher_dates_note(
            _NY_CALENDAR, [vouchers_penalty], year, today,
            balance_schedules=[vouchers_zero]),
    )


def build_ca_true_up(result: dict, proj_inputs: dict, prior,
                     filing_status: "str | None", as_of: datetime.date,
                     paychecks_left: int, year: int,
                     today: "datetime.date | None" = None) -> "CaTrueUp | None":
    """Assemble the CA true-up block, or None for a non-CA return.

    `year` is the covered tax year, taken as an argument rather than read off
    `as_of.year`. The two agree only because `_main` builds `as_of` with
    `parse_as_of(as_of_str, year)` two frames away — nothing in this
    signature enforces it, and a caller constructing `as_of` differently
    would silently compute the CA calendar note (and its `_return_due_date`
    lookup) for one year while the federal note used another.

    `today` is the real date, not the as-of date, and only the calendar note
    reads it — see `build_report`.
    """
    ca_inputs = read_ca_inputs(result)
    if ca_inputs is None:
        return None
    ca_tax, ca_agi = ca_inputs
    frac = elapsed_fraction(as_of)
    if frac <= 0:
        raise WithholdingError("elapsed fraction must be > 0")
    ytd = proj_inputs.get("ytd", {}) or {}
    ytd_state_wh = _nonneg_num(ytd.get("state_withholding"), "ytd.state_withholding")
    est = proj_inputs.get("estimated_payments_made", {}) or {}
    # Rounded once, here — the voucher math below and the displayed
    # `estimates_paid` must read the same cents-rounded figure, not an
    # unrounded local diverging from a separately-rounded display value.
    state_estimates = round(
        _nonneg_num(est.get("state"), "estimated_payments_made.state"), 2)
    proj_state_wh = round(ytd_state_wh / frac, 2)
    projected_paid = round(proj_state_wh + state_estimates, 2)

    prior_ca_tax, prior_ca_agi = _prior_ca_tax_agi(prior)
    safe_harbor = compute_ca_safe_harbor(
        ca_tax, prior_ca_tax, prior_ca_agi, ca_agi, filing_status,
        withholding=proj_state_wh, estimates_paid=state_estimates)
    zero_shortfall = round(max(0.0, ca_tax - projected_paid), 2)

    vouchers_penalty = compute_ca_estimated_vouchers(
        safe_harbor.shortfall, as_of, state_estimates)
    vouchers_zero = compute_ca_estimated_vouchers(
        zero_shortfall, as_of, state_estimates)
    return CaTrueUp(
        liability=ca_tax,
        ytd_withholding=round(ytd_state_wh, 2),
        projected_full_year_withholding=proj_state_wh,
        estimates_paid=state_estimates,
        projected_paid=projected_paid,
        safe_harbor=safe_harbor,
        zero_shortfall=zero_shortfall,
        vouchers_penalty=vouchers_penalty,
        vouchers_zero=vouchers_zero,
        de4_penalty=compute_w4_4c(safe_harbor.shortfall, paychecks_left),
        de4_zero=compute_w4_4c(zero_shortfall, paychecks_left),
        voucher_catchup_note=_voucher_catchup_note(
            _CA_CATCHUP, vouchers_penalty, safe_harbor, as_of),
        voucher_dates_note=_voucher_dates_note(
            _CA_CALENDAR, [vouchers_penalty], year, today,
            balance_schedules=[vouchers_zero]),
    )


def build_report(result: dict, proj_inputs: dict, prior, year: int,
                 as_of: datetime.date,
                 today: "datetime.date | None" = None,
                 tax_law: "dict | None" = None) -> TrueUpReport:
    """Build the report. `today` is the real date, NOT the as-of date.

    Optional, and omitting it omits the calendar notes rather than guessing
    at them — see `projection.build_report`, which states the whole of why.

    `tax_law` is the year's cache, and NEW YORK is the only block that reads
    it: the §685(c) shares and thresholds are FETCHED, deliberately not shared
    with the federal constants. It is optional so every existing caller keeps
    working; a New York return built without it gets no New York block, and
    the renderer SAYS the cache was missing rather than leaving a New York
    taxpayer's report silently state-less.
    """
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

    ca = build_ca_true_up(result, proj_inputs, prior, filing_status, as_of,
                          inputs.paychecks_remaining, year, today=today)
    ny = build_ny_true_up(result, proj_inputs, prior, filing_status, as_of,
                          inputs.paychecks_remaining, year, tax_law,
                          today=today)
    # `not tax_law`, matching the builder's own gate. `tax_law is None` was the
    # first spelling and disagreed with it: an EMPTY dict produced no New York
    # block and no explanation either, which is the silent-omission case this
    # flag exists to prevent.
    ny_cache_missing = (ny is None and not tax_law
                        and read_ny_inputs(result) is not None)

    return TrueUpReport(
        year=year, as_of=as_of.strftime("%m-%d"), pay_frequency=pay_frequency,
        liability=liability,
        baseline_source=("projection_inputs.json completed Form 2210 line-8 base"
                         if prior_tax is not None else None),
        degraded=safe_harbor.degraded, has_prior_archive=prior is not None,
        inputs=inputs, safe_harbor=safe_harbor,
        penalty_target=penalty_target, zero_target=zero_target,
        vouchers_penalty=vouchers_penalty, vouchers_zero=vouchers_zero,
        w4_penalty=w4_penalty, w4_zero=w4_zero, ca=ca, ny=ny,
        ny_cache_missing=ny_cache_missing,
        voucher_catchup_note=_voucher_catchup_note(
            _FEDERAL_CATCHUP, vouchers_penalty, safe_harbor, as_of),
        voucher_dates_note=_voucher_dates_note(
            _FEDERAL_CALENDAR, [vouchers_penalty], year, today,
            balance_schedules=[vouchers_zero]),
        # BOTH state blocks, not just California. `_W4_LEVER_SPENT_CLEAR_
        # STATE_DUE` exists because "no remaining payment target" was a lie for
        # a CA taxpayer with a state balance; the New York block reproduced the
        # closed bug on the day it landed — a closed year with a $6,000 New
        # York shortfall printed "No federal balance is projected due, so there
        # is no remaining payment target for this report" directly above the
        # table showing it. A return has ONE resident state, so at most one of
        # the two is real; `max` is the answer in every case and needs no
        # branch to say which.
        w4_lever_note=_w4_lever_note(
            inputs.paychecks_remaining, year, zero_shortfall,
            max(ca.zero_shortfall if ca is not None else 0.0,
                ny.zero_shortfall if ny is not None else 0.0)),
    )


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
    if r.ca is not None:
        c = r.ca
        csh = c.safe_harbor
        lines += ["",
                  "## California true-up (Form 540)", "",
                  "| Item | Amount |", "|---|---:|",
                  f"| CA total tax (target, actual computed) | {_money(c.liability)} |",
                  f"| YTD CA withholding | {_money(c.ytd_withholding)} |",
                  f"| Projected full-year CA withholding (current pace) | "
                  f"{_money(c.projected_full_year_withholding)} |",
                  f"| CA estimates paid | {_money(c.estimates_paid)} |",
                  f"| **Projected CA paid** | {_money(c.projected_paid)} |", "",
                  "### CA safe harbor (FTB 5805)", "",
                  "| Test | Amount |", "|---|---:|",
                  f"| 90% of current-year CA tax | {_money(csh.pct90_current)} |"]
        if csh.prior_based is not None:
            lines.append(f"| {csh.prior_factor:.0%} of prior-year CA tax | "
                         f"{_money(csh.prior_based)} |")
        lines += [f"| **Required annual payment** | {_money(csh.required_annual)} |",
                  f"| **Shortfall** | {_money(csh.shortfall)} |",
                  f"| Status | {_ca_status(csh, tax_is_actual=True)} |", ""]
        lines += _ca_notes(csh)
        lines += ["",
                  "### DE 4 per-paycheck extra CA withholding",
                  f"- To avoid penalty (safe harbor): **{_money(c.de4_penalty.per_paycheck)}"
                  f"/paycheck** over {c.de4_penalty.paychecks_remaining} remaining paychecks",
                  f"- To reach zero balance: **{_money(c.de4_zero.per_paycheck)}/paycheck**",
                  "",
                  # Shared with projection.render_markdown. This heading used
                  # to hardcode "(30% / 40% / 0% / 30%)" above amounts that
                  # follow a different split, and unlike the projection table
                  # there is no Share column here — the heading was the ONLY
                  # place the split was stated, so it was the only place it
                  # could be stated falsely.
                  _CA_ES_HEADING, "",
                  _CA_ES_WEIGHTING, "",
                  "| Installment | Due | To avoid penalty | To zero balance |",
                  "|---|---|---:|---:|"]
        for vp, vz in zip(c.vouchers_penalty, c.vouchers_zero):
            lines.append(f"| Q{vp.quarter} | {vp.due_date} | {_money(vp.amount)} | "
                         f"{_money(vz.amount)} |")
        # Both columns can depart from the statutory weighting independently:
        # the penalty column runs on the safe-harbor shortfall and the
        # zero-balance one on the full liability, so an estimate already paid
        # can move one and not the other.
        for column, note in (("To avoid penalty",
                              _ca_split_note(c.vouchers_penalty,
                                             csh.estimates_paid)),
                             ("To zero balance",
                              _ca_split_note(c.vouchers_zero,
                                             csh.estimates_paid))):
            if note:
                lines += ["", f"**{column}:** {note[0]}"]
        if c.voucher_dates_note:
            lines += ["", c.voucher_dates_note]
        if c.voucher_catchup_note:
            lines += ["", c.voucher_catchup_note]
    if r.ny is not None:
        n = r.ny
        lines += ["",
                  "## New York true-up (Form IT-201)", "",
                  "| Item | Amount |", "|---|---:|",
                  f"| NY total taxes (IT-201 line 60 — State, City, Yonkers "
                  f"and MCTMT) | {_money(n.liability)} |",
                  f"| New York AGI (line 33) | {_money(n.nyagi)} |",
                  f"| YTD New York withholding (all of it) | "
                  f"{_money(n.ytd_withholding)} |",
                  f"| Projected full-year NY withholding (current pace) | "
                  f"{_money(n.projected_full_year_withholding)} |",
                  f"| NY estimates paid | {_money(n.estimates_paid)} |",
                  f"| **Projected NY paid** | {_money(n.projected_paid)} |", "",
                  "### Required annual payment (Tax Law §685(c))", "",
                  "| Test | Amount |", "|---|---:|",
                  f"| Current-year share | "
                  f"{_money(n.options['current_year'])} |"]
        if "prior_year" in n.options:
            lines.append(f"| Prior-year share | "
                         f"{_money(n.options['prior_year'])} |")
        lines += [f"| **Required annual payment** (the lesser) | "
                  f"{_money(n.required_annual_payment)} |",
                  f"| **Shortfall** | {_money(n.shortfall)} |",
                  f"| Status | "
                  f"{'covered' if n.covered else 'NOT covered'} |", ""]
        for ny_note in n.notes:
            lines += [f"- {ny_note}"]
        lines += ["",
                  "### IT-2104 per-paycheck extra NY withholding",
                  f"- To avoid penalty (safe harbor): "
                  f"**{_money(n.it2104_penalty.per_paycheck)}/paycheck** over "
                  f"{n.it2104_penalty.paychecks_remaining} remaining paychecks",
                  f"- To reach zero balance: "
                  f"**{_money(n.it2104_zero.per_paycheck)}/paycheck**",
                  "",
                  "### IT-2105 installment schedule (four EQUAL quarters)", "",
                  "One voucher covers the State tax, the New York City "
                  "resident tax, the Yonkers surcharge and the MCTMT "
                  "together — which is why the target above is line 60 and "
                  "not line 46.", "",
                  "| Installment | Due | To avoid penalty | To zero balance |",
                  "|---|---|---:|---:|"]
        for vp, vz in zip(n.vouchers_penalty, n.vouchers_zero):
            lines.append(f"| Q{vp.quarter} | {vp.due_date} | "
                         f"{_money(vp.amount)} | {_money(vz.amount)} |")
        if n.voucher_dates_note:
            lines += ["", n.voucher_dates_note]
        if n.voucher_catchup_note:
            lines += ["", n.voucher_catchup_note]
    lines += ["",
              "## Caveats",
              "- This is a planning projection, not a filed return. Re-run after any "
              "material income or withholding change."]
    if r.ny is not None:
        lines.append("- The New York true-up reads `ytd.state_withholding` as ALL "
                     "New York withholding — W-2 box 17, box 19 for a New York "
                     "City locality, and any Yonkers amount together — because "
                     "one Form IT-2105 voucher is measured against all of them. "
                     "Entering the state figure alone over-states the shortfall.")
    if r.ny_cache_missing:
        lines.append("- **No New York true-up was computed**: result.json carries a "
                     "computed IT-201 but no tax-law cache was supplied, and the "
                     "Tax Law §685(c) shares and thresholds are FETCHED — they are "
                     "New York's own and may diverge from the federal ones. Re-run "
                     "with the year's `tax_law_{year}.json`. This is NOT a finding "
                     "that no New York estimated tax is due.")
    if r.ca is not None:
        lines.append("- State true-up covers CA only, anchored to the actual computed "
                     "Form 540 tax. CA mandatory e-pay (R&TC §19011.5): once any "
                     "estimated/extension payment exceeds $20,000 or total tax "
                     "liability exceeds $80,000, ALL subsequent CA payments must be "
                     "electronic (FTB Web Pay) — a 1% penalty applies to paper "
                     "payments.")
    elif r.ny is None and not r.ny_cache_missing:
        lines.append("- Federal only; no california_540 or new_york_it201 section in "
                     "result.json — the state withholding true-up is covered for CA "
                     "and NY returns.")
    lines.append("")
    return "\n".join(lines) + "\n"


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
        "ny": (None if r.ny is None else {
            "liability": r.ny.liability,
            "nyagi": r.ny.nyagi,
            "ytd_withholding": r.ny.ytd_withholding,
            "projected_full_year_withholding": r.ny.projected_full_year_withholding,
            "estimates_paid": r.ny.estimates_paid,
            "projected_paid": r.ny.projected_paid,
            "required_annual_payment": r.ny.required_annual_payment,
            "options": r.ny.options,
            "shortfall": r.ny.shortfall,
            "covered": r.ny.covered,
            "zero_shortfall": r.ny.zero_shortfall,
            "vouchers_penalty": [vars(v) for v in r.ny.vouchers_penalty],
            "vouchers_zero": [vars(v) for v in r.ny.vouchers_zero],
            "it2104_penalty": vars(r.ny.it2104_penalty),
            "it2104_zero": vars(r.ny.it2104_zero),
            "notes": list(r.ny.notes),
            "voucher_catchup_note": r.ny.voucher_catchup_note,
            "voucher_dates_note": r.ny.voucher_dates_note,
        }),
        "ca": (None if r.ca is None else {
            "liability": r.ca.liability,
            "ytd_withholding": r.ca.ytd_withholding,
            "projected_full_year_withholding": r.ca.projected_full_year_withholding,
            "estimates_paid": r.ca.estimates_paid,
            "projected_paid": r.ca.projected_paid,
            "safe_harbor": vars(r.ca.safe_harbor),
            "zero_shortfall": r.ca.zero_shortfall,
            "vouchers_penalty": [vars(v) for v in r.ca.vouchers_penalty],
            "vouchers_zero": [vars(v) for v in r.ca.vouchers_zero],
            "voucher_catchup_note": r.ca.voucher_catchup_note,
            "voucher_dates_note": r.ca.voucher_dates_note,
            "de4_penalty": vars(r.ca.de4_penalty),
            "de4_zero": vars(r.ca.de4_zero),
        }),
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

        # Read for the NEW YORK block and for nothing else — the §685(c)
        # shares are fetched, the federal and California figures here are
        # not. ABSENT is tolerated, so a workspace that never ran /tax-setup
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
