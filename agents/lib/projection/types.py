#!/usr/bin/env python3
"""The records `/tax-project` passes around, and the two helpers that read them.

Its own file because the COMPUTE side and the RENDER side both need them, and
`render.py` must not import the package `__init__` that re-exports it — the
same cycle `tax_computations/_shared.py` exists to avoid.

WHAT BELONGS HERE is a record and nothing that decides a figure. `_money` and
`_prior_year_floor_applied` are here because they are read OFF these records
rather than computed into them: one formats a field, the other answers a
question about a `CaSafeHarbor` that a caller and a note-writer both ask.
"""

import datetime  # noqa: F401 — annotations on these records name it
from dataclasses import dataclass, field, replace  # noqa: F401

import agents.lib  # noqa: F401 — the host-interpreter guard


class ProjectionError(Exception):
    """Raised when /tax-project cannot safely proceed."""


# CA 540-ES installments are NOT four equal quarters: 30% / 40% / 0% / 30%
# per R&TC §19136.1 (FTB Form 540-ES instructions) — there is NO September
# (Q3) installment. Q4 is due Jan 15 of the following year, like federal.
# Structural statutory constants (weights are set in statute, not indexed).
CA_ES_SCHEDULE = [(1, (4, 15), 0.30), (2, (6, 15), 0.40),
                  (3, (9, 15), 0.00), (4, (1, 15), 0.30)]


# R&TC §19136 (FTB 5805 Part I): NO CA underpayment penalty when current-year
# tax minus withholding is under $500 ($250 MFS). Statutory-fixed, not indexed.
CA_ES_DE_MINIMIS = 500
CA_ES_DE_MINIMIS_MFS = 250


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
    # The deductions the year is projected on, as the taxpayer declares them
    # (AA21): the expected itemized total, federal and state, or 0 for the
    # standard deduction. None is "not answered" and refuses where it is
    # read — the standard deduction in its place over-states an itemizer's
    # tax and the payments the projection recommends.
    itemized_deductions: "float | None" = None
    state_itemized_deductions: "float | None" = None
    # The year's expected cash gifts to charity (AA22), read from TY2026 for
    # §170(p)'s deduction for non-itemizers. None refuses there, as above.
    charitable_cash_gifts: "float | None" = None
    # Full Schedule A inputs for this projected year, confirmed as forecasts.
    # None preserves the declared-total path for older workspaces.
    schedule_a_inputs: "dict | None" = None
    schedule_a_charitable_limits_confirmed: bool = False
    # Confirmed full-year worksheet outputs and jurisdiction answers (W10).
    state_modification_amounts: "dict | None" = None
    state_local_applicability: "dict | None" = None
    # Reviewed full-year Form 6251 preferences and credit worksheets (AA42).
    amt_projection: dict | None = None
    # Independently reviewed regular FTC when AMT remains unmodeled (AA78).
    foreign_tax_credit_projection: dict | None = None


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
    deduction: float = 0.0       # the standard or the declared itemized, as chosen
    itemized: bool = False       # whether the declared itemized total was it
    # The other deductions between AGI and taxable income, as applied, so
    # the report's table adds up.
    schedule_1a_deduction: float = 0.0
    qbi_deduction: float = 0.0
    charitable_non_itemizer_deduction: float = 0.0  # §170(p), line 13c
    schedule_a_repriced: bool = False
    schedule_a_notes: list[str] = field(default_factory=list)
    amt: float | None = None
    amt_details: dict | None = None
    amt_note: str | None = None
    foreign_tax_credit: float = 0.0
    minimum_tax_credit: float = 0.0


@dataclass(frozen=True)
class StateProjection:
    code: str
    taxable_income: float
    tax: float
    withholding: float
    estimates_paid: float
    balance: float
    # Projected state AGI (income before the state deduction; state
    # optional confirmed modification worksheet amounts apply). Feeds R&TC §19136.3's
    # high-income test, which is on CURRENT-year CA AGI.
    agi: float = 0.0
    deduction: float = 0.0       # the larger of the standard and the declared itemized
    itemized: bool = False       # whether the declared itemized total was it
    # Dedicated NY/NC forecasts distinguish income taxes, refundable credits
    # and use tax. `tax` is the total before payments; no hidden credit netting.
    form_modeled: bool = False
    state_income_tax: float = 0.0
    local_income_tax: float = 0.0
    refundable_credits: float = 0.0
    use_tax: float = 0.0


@dataclass(frozen=True)
class StatePaymentSchedule:
    code: str
    form: str
    current_year_base: float
    options: dict
    required_annual: float
    withholding: float
    estimates_paid: float
    shortfall: float
    de_minimis: bool
    prior_no_liability: bool
    vouchers: list
    notes: list[str]
    voucher_catchup_note: str | None = None
    voucher_dates_note: str | None = None

    @property
    def jurisdiction(self) -> str:
        return self.code


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
    # §6654(e)(1): current tax minus WITHHOLDING (estimates do not count
    # toward this test) under $1,000 → no federal underpayment penalty can
    # arise regardless of the shortfall shown. The CaSafeHarbor analogue
    # below shipped with its de minimis; this side went without one, so the
    # federal report demanded catch-up payments for a penalty `compute_
    # form_2210` reports as impossible on the very same figures.
    de_minimis: bool = False
    # How far UNDER the floor the measured balance sits, 0.0 when the floor
    # does not apply. The floor is a cliff evaluated against an ANNUALIZED
    # projection, so "no penalty applies" is only as good as the projection:
    # this is the margin that has to be wrong for the excuse to evaporate.
    # Carried as the continuous quantity rather than a "near the cliff" flag —
    # any threshold for near would be invented, and the repo's rule is to
    # model the continuous version where a statute has a discontinuity.
    de_minimis_margin: float = 0.0
    # The gap the harbor asked for BEFORE the floor suppressed it. `shortfall`
    # is the suppressed one, so without this the payload keeps no record of
    # what was suppressed and the table prints a Required/Paid pair that does
    # not reconcile with its own Shortfall column.
    shortfall_before_de_minimis: float = 0.0
    # What the harbor is short once the floor LAPSES, ONE FIELD PER PATH, and
    # 0.0 is the answer that matters on each: nothing attaches that way,
    # because the required annual payment is met on its own. The status cell
    # asserted the opposite in every de-minimis case — $5,000 of tax against
    # $4,500 withheld and a $4,000 prior-year prong printed "$500 more tax and
    # it attaches", while at $5,500 the prong still binds against $4,500 paid
    # and nothing attaches at all. Over-stating what the taxpayer must pay is
    # the hard error whether it arrives as a total or, as here, as a warning.
    #
    # They were one field — the worse of the two — and that is the same error
    # a level down: /tax-withholding's tax does not move, so a positive coming
    # from the MORE-TAX path was printed there as a warning about withholding
    # falling, on $0 of withholding that cannot fall. A renderer reads the path
    # its own inputs can take; see `_de_minimis_cliff_paths`.
    de_minimis_cliff_more_tax: float = 0.0
    de_minimis_cliff_less_withholding: float = 0.0
    # The current-year line-4 limitation, true only
    # where an un-netted figure is actually driving a payment: a positive
    # shortfall with the floor not applying. §6654 measures Form 2210 line 4 —
    # line 24 MINUS the part-IV refundable credits — and line 4 is what feeds
    # both the 90% prong and the §6654(e)(1) floor. What arrives here is line
    # 24, which sits above those credits, so the required payment is too high
    # and the floor is applied too rarely: $5,000 of tax, $1,000 of refundable
    # AOTC and $3,500 withheld is a true $500 balance the floor excuses, shown
    # here as $1,500 with a payment to make.
    #
    # Flagged rather than derived because
    # neither caller HAS the credits. `project_federal_tax` applies the CTC as
    # a nonrefundable credit only and models no EIC or ACTC, and result.json
    # carries a field for the refundable AOTC and for the Schedule 3 Part II
    # aggregate — which mixes real refundable credits with an extension PAYMENT
    # and with excess social security, a §31 credit belonging to the OTHER side
    # of the floor's subtraction. Netting what is visible would leave the
    # disclosure needed anyway while making the figure look derived.
    current_tax_unnetted: bool = False
    # Which instalment calendar this harbor's figures belong to. Read ONLY by
    # `_voucher_catchup_note`, to refuse a note bound to the other
    # jurisdiction's weights — see `_CatchupNote`. A plain label rather than an
    # isinstance check because that gate is duck-typed on purpose.
    jurisdiction: str = "federal"


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
    # The PRIOR-year prong of the same subdivision, proved by a Form 540 total
    # tax already under the floor — and ABOVE $0, the $0 case being carried by
    # its own note pair in `_ca_notes` rather than by this flag. Acted on at
    # every AGI: see `prior_year_floor_applied` for why §19136.3 does not
    # reach it, and for the round in which this engine thought it might.
    prior_year_floor: bool = False
    # The UNKNOWN sibling of the flag above, and of `_CA_EITHER_YEAR_FLOOR_NOTE`
    # below it: both of those read a KNOWN prior-year Form 540 total, and an
    # ABSENT one fell between them into silence — beside a live 540-ES
    # schedule, and beside an all-zero one whose estimates arrived late, where
    # the requirement is met and an instalment penalty has accrued anyway.
    # Both states, one flag: `_ca_notes` picks the note by `shortfall`, since
    # only one of the two can honestly say "the schedule below". Deliberately
    # NOT folded into `degraded`; the note constants name the rule and
    # `compute_ca_safe_harbor` says why the two must stay apart at the
    # §19136.3 limitation.
    prior_year_floor_unknown: bool = False
    # Distance under the CA floor — see SafeHarbor.de_minimis_margin.
    de_minimis_margin: float = 0.0
    # CA reads `ca_total_tax` as filed. The CA note stays live and
    # deliberately narrower because which credits FTB 5805 nets is not
    # something this engine has verified.
    #
    # There is deliberately NO federal twin of this field. `SafeHarbor`
    # carried one, always False and read by nothing, once the federal prong
    # began requiring an explicit completed Form 2210 line-8 composition —
    # one name meaning "live per-run flag" on this class and "compile-time
    # constant" on its sibling. A `--json` consumer writing one handler for
    # `*.prior_tax_unnetted` read that permanent false as "the federal prior
    # base IS netted", a claim the engine never makes. Do not re-add it: the
    # federal answer is that the question cannot arise.
    prior_tax_unnetted: bool = False
    # See SafeHarbor for all of these — same rule, same floor arithmetic, R&TC
    # §19136 in place of §6654(e)(1) and §6654(f). The CA note is deliberately
    # narrower for the same reason: which credits FTB 5805
    # nets is not a list this engine has verified.
    shortfall_before_de_minimis: float = 0.0
    de_minimis_cliff_more_tax: float = 0.0
    de_minimis_cliff_less_withholding: float = 0.0
    current_tax_unnetted: bool = False
    # See SafeHarbor.jurisdiction. "CA" here is what makes pairing this object
    # with the FEDERAL catch-up note a refusal instead of a wrong verdict.
    jurisdiction: str = "CA"

    @property
    def prior_year_floor_applied(self) -> bool:
        """The prior-year floor is not merely PRESENT but carrying the year —
        and it carries it at EVERY AGI, §19136.3 included.

        §19136.3 is two subdivisions and its whole operative text modifies IRC
        §6654(d)(1)(B) so that "clause (ii) shall not apply" above $1,000,000
        ($500,000 MFS). Clause (ii) is the prior-year component of the REQUIRED
        ANNUAL PAYMENT. R&TC §19136(c)(2) is not part of that computation at
        all — it is a bar on IMPOSING the addition to tax ("an addition to tax
        shall not be imposed under this section if...") — and §19136.3 does not
        mention it. FTB 5805's own General Information keeps them in that
        order: the floor sentence is unconditional ("you are not subject to the
        underpayment of estimated tax penalty"), while the $1,000,000 sentence
        is the one carrying the proviso — those taxpayers "must use the tax on
        their ... tax return to figure their penalty IF THEY ARE SUBJECT to the
        underpayment of estimated tax penalty". So the limitation decides
        `required_annual`, which stays at 90% of the current year, and this
        decides whether falling short of it costs anything.

        This read `and not self.high_income_90_only` for one round, on the
        worry that §19136.3 might reach (c)(2) after all — while the note
        printed beside it in the taxpayer's own report asserted the opposite in
        so many words ("a separate rule that §19136.3 does not reach ... the
        penalty may not apply at all"). A 540-ES schedule and that sentence
        cannot both be right, and the pair erred by collecting money the state
        cannot penalise not having.

        `or self.prior_tax == 0` because a $0 prior year proves the same prong
        by the same bound, and is only split off `prior_year_floor` so that the
        $0 note pair in `_ca_notes` is not printed twice over. Below the
        limitation it needed nothing here — the prong already zeroes
        `required_annual` — and at or above it, it needed exactly this. A
        negative prior tax is nonsense input and reaches neither term, which
        leaves it in the OVER-stating direction where nonsense belongs.

        Every consumer asking "can a penalty still arise at all?" wants THIS
        one, and spelling its ingredients out again at each such site is
        exactly how they got out of step: the flag shipped folded into
        `no_penalty` in `compute_ca_safe_harbor`, while the de-minimis cliff
        and the voucher timing note went on testing `de_minimis` alone and kept
        warning a taxpayer the subdivision had already cleared.

        A PROPERTY, not a second stored bool. Both reports build their `--json`
        harbor payload with `vars()`, which sees fields and not properties, so
        this cannot reach a consumer as a name whose meaning depends on which
        class it was read off — `prior_tax_unnetted` above is the note left by
        the run where that happened. There is no federal twin for the same
        reason: §6654 has no floor on the PRIOR year at all, only §6654(e)(2)'s
        zero, which this engine already expresses as a $0 required annual
        payment and which therefore needs nothing to survive past the
        current-year floor.
        """
        return self.prior_year_floor or self.prior_tax == 0


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
    # CA's weighted schedule stays separate from the NY/NC equal quarters.
    ca_safe_harbor: "CaSafeHarbor | None" = None
    ca_vouchers: "list[Voucher]" = field(default_factory=list)
    state_payment_schedule: StatePaymentSchedule | None = None
    irmaa: dict | None = None
    # The voucher catch-up notes, decided in build_report so the markdown and
    # the `--json` payload cannot disagree about them. They were computed
    # inside the renderer, which put them in the one output both consuming
    # skills read SECOND — /tax-project step 5 takes `--json` and step 6
    # re-runs for markdown, so an agent summarising the payload handed over
    # the under-funded earliest voucher with nothing marking it. Every other
    # renderer-only warning here (`nonresident_income_warning`,
    # `state_unsupported_note`) is a field for the same reason. None = the
    # note is not true of that schedule; see `_voucher_catchup_note`.
    voucher_catchup_note: "str | None" = None
    ca_voucher_catchup_note: "str | None" = None
    # The CALENDAR notes for the same two schedules — whether the dates beside
    # the amounts are still ahead. Fields for the reason above, and separate
    # from the catch-up pair because the two are independently true: a schedule
    # can be correctly split and entirely obsolete. See `_voucher_dates_note`.
    # None both while every printed date is still open and when the report was
    # built without a `today` to compare against.
    voucher_dates_note: "str | None" = None
    ca_voucher_dates_note: "str | None" = None
    # Which lever the covered year still has, as the lead of "Recommended
    # actions". Always set by `build_report`; see `_w4_lever_note`.
    w4_lever_note: "str | None" = None
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
    # Set by `_main` when a malformed stored `as_of_date` inside a CLOSED year
    # is omitted and 12-31 is used — see `resolve_as_of`. A field rather than a renderer
    # decision for the reason `voucher_catchup_note` above is one: both
    # consuming skills read `--json` first, and a banner that says the YTD
    # figures may not be the year's is not one to leave out of the payload
    # they summarise from.
    stale_as_of_note: "str | None" = None
    # Non-null for a taxed state without California's dedicated projection
    # block. This projection does not implement those states' full returns.
    # A FIELD, not a renderer decision, for the
    # reason `stale_as_of_note` above is one, and it is the same defect twice
    # over: the hedge first went onto the W-4 lede only, then onto the
    # markdown table only. Both consuming skills instruct the agent to read
    # the payload BEFORE summarising anything, and the payload's `state.balance`
    # carried no qualification at all — on an open year `w4_lever_note` is the
    # lead sentence, which has no state clause, so an agent following the
    # documented flow reported a bracket estimate as an amount owed.
    state_bracket_estimate_note: "str | None" = None
    # True when an `archive/{year-1}/` prior return was passed to
    # `build_report` at all. Independent of `degraded`: `degraded` now tracks
    # only whether an explicit `prior_year_form_2210_tax_base` was supplied
    # (the two used to coincide, before an explicit base could be given
    # without an archive), and `recurring_from_prior` is populated by the
    # SKILL from that archive — this engine never reads it automatically. A
    # False here is what the renderer's recurring-income-defaulted-to-0
    # warning gates on, not `degraded`.
    has_prior_archive: bool = False
    # A separate filer's deduction can be forced by the spouse's choice, which
    # the projection does not ask: it prices the choice that deducts more, and
    # where the spouse forces the other one the tax reads low. Under-stated advisory
    # output degrades with a banner (AGENTS.md §4), so each is a field the
    # `--json` payload carries too. Federal: set when the standard deduction
    # was taken (§63(c)(6)(A) denies it once the spouse itemizes). State: set
    # whenever a state is projected, since a both-or-neither rule binds either
    # way. See `_mfs_deduction_notes`.
    mfs_deduction_note: "str | None" = None
    state_mfs_deduction_note: "str | None" = None
    # A declared itemized total is priced as entered while parts of it move
    # with income, so it can read either way; the payload says so wherever
    # either projection itemized. See `_itemized_deduction_note`.
    itemized_deduction_note: "str | None" = None
    # §170(p)'s deduction takes the declared gifts as entered, and only some
    # gifts qualify; set wherever it was taken. See `_charitable_gifts_note`.
    charitable_gifts_note: "str | None" = None


def _money(v) -> str:
    """Whole dollars, EXCEPT where that would print a non-zero amount as $0.

    In a tax table "$0" means nothing is owed, and the CA de minimis path
    prints genuinely-zero vouchers meaning exactly that — so rounding $0.01 to
    the same string makes a real obligation indistinguishable from its
    absence. It also produced the visible contradiction that surfaced this: a
    voucher schedule reading "| Q4 | 2027-01-15 | 100% | $0 |", a hundred
    percent of nothing. Reachable whenever a shortfall survives the $500 CA
    floor by cents (tax minus withholding of $500.03 leaves $0.03 to
    schedule), and the same holds for any sub-dollar figure anywhere in either
    report. Cents appear only in that window; every ordinary figure is
    unchanged.
    """
    if v is None:
        return "—"
    cents = round(abs(v), 2)
    body = (f"${cents:,.2f}" if 0 < cents < 0.995 else f"${abs(v):,.0f}")
    return f"({body})" if v < 0 else body


def _prior_year_floor_applied(harbor) -> bool:
    """`CaSafeHarbor.prior_year_floor_applied` off a duck-typed harbor.

    `getattr` for the reason `jurisdiction` uses it two gates down: both
    callers are shared with the FEDERAL harbor, which carries no twin of this
    field on purpose (see `CaSafeHarbor.prior_tax_unnetted` for what a
    permanent False on the sibling class cost once), and the stubs these gates
    are tested with are not `CaSafeHarbor`s either. One helper rather than the
    same `getattr` string at each site, so the field can be renamed without
    leaving a silent False behind at whichever site was missed.
    """
    return getattr(harbor, "prior_year_floor_applied", False)
