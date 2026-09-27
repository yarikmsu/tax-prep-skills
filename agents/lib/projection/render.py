#!/usr/bin/env python3
"""The markdown `/tax-project` prints, and the prose it is built from.

Half of what used to be `projection.py` by symbol count was NOT computation:
eighteen `_*_NOTE` constants, the note-builders that assemble them, and
`render_markdown` itself. They are here because the boundary is clean in the
direction that matters — the notes are read by the renderers and by nothing
that decides a figure, and `render_markdown` is called by `__main__` alone.

It imports the records from `types.py` and never from the package `__init__`,
which would be a cycle.
"""

from dataclasses import dataclass

import agents.lib  # noqa: F401 — the host-interpreter guard
from agents.lib.projection.types import (
    CA_ES_DE_MINIMIS,
    CA_ES_DE_MINIMIS_MFS,
    CA_ES_SCHEDULE,
    CaSafeHarbor,
    ProjectionReport,
    SafeHarbor,
    _money,
    _prior_year_floor_applied,
)
from agents.lib.tax_computations import (
    DE_MINIMIS_BALANCE,
    PRIOR_YEAR_AGI_THRESHOLD,
    PRIOR_YEAR_AGI_THRESHOLD_MFS,
)


_DEGRADED_MODE_LINE = (
    "- **Degraded mode:** no completed covered-year Form 2210 line-8 "
    "pre-factor prior-tax base was supplied. The prior-year prong is "
    "unresolved, so this report shows the 90%-of-current-year test "
    "only. Do not substitute raw Form 1040 line 24; complete the "
    "covered-year line-8 composition and enter "
    "`prior_year_form_2210_tax_base` to enable that prong.")


def _prior_baseline_lines(baseline_source: "str | None", year: int,
                          ca_prior_resolved: bool, degraded: bool) -> list:
    """The "Prior-year baseline" bullet and its degraded-mode companion.

    ONE copy, shared by `projection.render_markdown` and
    `withholding.render_markdown`. Both used to spell out the same two
    sentences and the same `archive/{year-1}/` interpolation independently,
    which is the drift `_PRIOR_AGI_THRESHOLD_TEXT` and `_DeMinimisFloor` were
    introduced to prevent one round earlier — and a correction here has to
    land on both readers or the two reports disagree about the same archive.

    `ca_prior_resolved` is passed in rather than derived: the two reports
    reach the CA harbor by different attribute paths, and that difference is
    genuinely theirs. Everything below this line is not.

    The clarifier is gated on the CA prong having ACTUALLY resolved, not on an
    archive merely existing: "no FEDERAL line-8 base" is not "no archive", but
    a federal-only return renders no CA section at all, and a CA one whose
    prior figures came back empty prints its own "no usable prior-year CA
    baseline" below. Saying the archive supplies a harbor in either case
    points at a figure the report does not carry.
    """
    clarifier = ""
    if baseline_source is None and ca_prior_resolved:
        clarifier = (" (federal only; an `archive/{}/` prior return IS present "
                     "and still supplies the CA prior-year harbor below)"
                     .format(year - 1))
    lines = [f"- Prior-year baseline: "
             f"{baseline_source or 'none — DEGRADED MODE'}{clarifier}"]
    if degraded:
        lines.append(_DEGRADED_MODE_LINE)
    return lines


_CA_ES_HEADING = "### CA 540-ES voucher schedule"


# The heading does NOT carry the statutory split. It used to read
# "(30% / 40% / 0% / 30%)" in BOTH renderers, above amounts that follow that
# weighting only when all four instalments are open and no estimate has been
# paid. A heading cannot qualify itself, so the weighting moved into prose
# that can — and into ONE copy, because fixing this in projection.py alone
# left withholding.py asserting the contradicting form, with each module's
# tests pinning the opposite contract for the same string.
_CA_ES_WEIGHTING = (
    "California installments are NOT four equal quarters: the statutory "
    "weighting is 30% / 40% / 0% / 30%, so there is no September (Q3) "
    "payment (R&TC §19136.1; FTB Form 540-ES).")


_CA_ES_SHARE_COLUMN_CAVEAT = (
    " The Share column reports what THIS schedule allocates, which equals the "
    "statutory weighting only while all four installments are open and no "
    "estimate has already been paid.")


_CA_SPLIT_CLOSED = (
    "The instalments above are not in the statutory 30% / 40% / 0% / 30% "
    "proportions: earlier installments have closed, and the remaining "
    "shortfall is reallocated across the open installments in proportion to "
    "their statutory weights.")


_CA_SPLIT_EARLY_ESTIMATE = (
    "The instalments above are not in the statutory 30% / 40% / 0% / 30% "
    "proportions: estimates already paid are credited against the cumulative "
    "30% / 70% / 70% / 100% checkpoints R&TC §19136.1 actually tests, so an "
    "installment already covered by those payments schedules $0 and the "
    "balance falls on the first checkpoint that is not yet met. Paying the "
    "amounts as listed meets every checkpoint.")


_CA_SPLIT_ROUNDING = (
    "The instalments above differ from the statutory 30% / 40% / 0% / 30% "
    "proportions only by cents rounding: the instalments are whole cents and "
    "the shortfall is too small to split in those proportions exactly. The "
    "amounts, not the proportions, are what to pay.")


def _ca_split_diverges(vouchers: list) -> bool:
    """Whether `vouchers` depart from the statutory 30/40/0/30 weighting.

    Measured off the AMOUNTS, not inferred from which path produced them. The
    first version of this gate asked `len(vouchers) < len(CA_ES_SCHEDULE)`,
    which sees the closed-instalment cause and is blind to the other two.
    An all-zero column (the de minimis path) has no split to describe.
    """
    total = round(sum(v.amount for v in vouchers), 2)
    if total <= 0:
        return False
    statutory = {q: w for q, _, w in CA_ES_SCHEDULE}
    return any(share != round(100 * statutory.get(v.quarter, 0.0))
               for v, share in zip(vouchers, _percent_shares(
                   [v.amount for v in vouchers])))


def _ca_split_note(vouchers: list, estimates_paid: float) -> list:
    """The sentence explaining a non-statutory CA instalment split, or [].

    Three causes, one each, and never a cause that did not apply: the
    estimates-already-paid explanation was once printed unconditionally, which
    told a taxpayer who had paid none that their payments caused the split. A
    caveat false of its reader is one they learn to skip.
    """
    if not _ca_split_diverges(vouchers):
        return []
    if len(vouchers) < len(CA_ES_SCHEDULE):
        return [_CA_SPLIT_CLOSED, ""]
    if estimates_paid > 0:
        return [_CA_SPLIT_EARLY_ESTIMATE, ""]
    return [_CA_SPLIT_ROUNDING, ""]


def _percent_shares(amounts: list) -> list:
    """Whole-percent shares of `amounts` that sum to exactly 100.

    Rounding each share on its own lets a four-row column print 24 / 44 / 0 /
    33 under a heading reading "Share of remaining" — 101%. Largest-remainder
    hands the leftover points to the rows that lost the most to truncation, so
    every row stays within one point of its true share and the column sums to
    100 by construction rather than by luck. An all-zero column (the de
    minimis path, where every voucher is $0.00) has no shares to apportion and
    prints 0% throughout.
    """
    total = round(sum(amounts), 2)
    if total <= 0:
        return [0] * len(amounts)
    exact = [100.0 * a / total for a in amounts]
    shares = [int(x) for x in exact]
    by_remainder = sorted(range(len(exact)),
                          key=lambda i: exact[i] - shares[i], reverse=True)
    for i in by_remainder[:100 - sum(shares)]:
        shares[i] += 1
    return shares


# The same §6654(f) gap on the OTHER year, and it reaches further: the
# current-year figure drives BOTH prongs and the §6654(e)(1) floor, where the
# prior-year one drives a single prong. `compute_form_2210` states this
# contract on its own argument — Form 2210 line 4, line 24 MINUS the part-IV
# refundable credits — and neither caller of `compute_safe_harbor` can meet it.
# The direction is one-way (line 4 <= line 24, and the §31 credit the floor
# subtracts is >= the withholding figure passed), so what over-states is the
# shortfall, which is the hard-error direction rather than the banner one.
# Gated on a shortfall actually being asserted: under the floor nothing is
# asked for, and netting cannot take a run OUT of the floor.
# Prose-formatted once from the same constants `prior_year_agi_threshold`
# reads, so the three "prior AGI unavailable" notes below (federal ×2 + CA)
# can never go stale independently of PRIOR_YEAR_AGI_THRESHOLD/_MFS the way a
# bare "$150,000/$75,000" literal in each would.
_PRIOR_AGI_THRESHOLD_TEXT = (
    f"${PRIOR_YEAR_AGI_THRESHOLD:,}/${PRIOR_YEAR_AGI_THRESHOLD_MFS:,}-MFS")


# Attached to the prior-year row wherever it is shown, and NOT gated on a
# confirmation flag. §6654(d)(1)(B)'s closing text — "Clause (ii) shall not
# apply if the preceding taxable year was not a taxable year of 12 months or
# if the individual did not file a return for such preceding taxable year" —
# is a precondition of the prong itself, not of a $0 base. The engine enforces
# the confirmation only at $0, where the prong would zero the requirement
# outright; a POSITIVE base is taken as supplied, because the flags default
# false in every template and refusing the overwhelmingly ordinary case is the
# gate that gets reasoned past.
#
# So the precondition is DISCLOSED rather than enforced here. That keeps it an
# always-true sentence about the figure beside it instead of a warning that
# fires on correct data, and it is the half a reader can act on: a taxpayer
# whose first filing year was short, or who never filed, is reading a target
# built on a prong they do not have, and 100%/110% of a small prior tax is far
# below the 90% they actually owe.
#
# NOT gated on a confirmation flag, but DOES stop at a $0 base — see
# `_prior_prong_precondition_applies` below, which owns both exclusions.
_PRIOR_PRONG_PRECONDITION_NOTE = (
    "- **The prior-year prong assumes a filed, full 12-month prior year.** "
    "§6654(d)(1)(B) withdraws it entirely if the preceding year was not a "
    "12-month year or no return was filed for it — a first short filing year "
    "or a year with no return leaves only the 90%-of-current test, and every "
    "safe-harbor figure here that came from the prior year would then be too "
    "LOW. This engine does not verify either condition for the POSITIVE "
    "prior-year tax it was given; confirm both before relying on the "
    "prior-year prong.")


def _prior_prong_precondition_applies(sh) -> bool:
    """True when `_PRIOR_PRONG_PRECONDITION_NOTE` is a TRUE sentence about the
    report it would be printed in. Shared by both renderers for the reason the
    note itself is: a suppression in only one of them is as much a drift as a
    caveat in only one.

    Two exclusions, and each makes the note a LIE rather than merely noise:

    - DEGRADED — no prior-year figure at all, so the sentence qualifies
      something that is not in the report.
    - A $0 base — reachable ONLY through a confirmation the user gave, and one
      of the two routes to it is §6654(e)(2), whose three conditions (a
      12-month prior year, no liability for it, citizenship or residency
      throughout) do NOT include filing a return. Form 2210's own instructions
      spell that out — the prior year need only have been 12 months "or would
      have been had you been required to file" — and someone under last year's
      filing threshold is the ordinary case, not an exotic one. Telling that
      taxpayer their $0 target "would then be too LOW" contradicts the correct
      result printed beside it, and the remedy the note points at is 90% of
      the current year: money §6654(e)(2) says they do not owe.
      `_prior_tax_agi` refuses an UNCONFIRMED $0, so `prior_tax == 0` here
      always means one of the two routes was affirmed and the precondition
      question has already been asked and answered.

    The §6654(d)(1)(B) closing text the note quotes is a condition of the
    ORDINARY prong, which is the only route to a POSITIVE base — §6654(e)(2)
    is a no-LIABILITY exception and cannot produce one. So a positive base is
    exactly the case the note is true of, and it is the case nothing verifies.
    """
    return not sh.degraded and sh.prior_tax != 0


_CURRENT_TAX_UNNETTED_NOTE = (
    "- **Current-year tax here is a line-24 TOTAL, NOT Form 2210 line 4.** "
    "§6654 measures the tax net of the part-IV credits other than §31, "
    "including the EIC, ACTC, refundable AOTC, net premium tax credit, fuel "
    "credit, the REFUNDABLE adoption credit (Form 8839 line 13 — refundable "
    "for tax years beginning after 2024), and the §1341 credit, and that "
    "netted figure is what "
    "BOTH tests above run on: the 90% prong, and the $1,000 de minimis of "
    "§6654(e)(1), which subtracts the whole §31 credit and so counts excess "
    "social security (Schedule 3 line 11) alongside withholding. This engine "
    "has neither adjustment: the projection treats the child tax credit as "
    "nonrefundable only, and result.json carries no field for the earned "
    "income credit or the additional child tax credit. Both gaps run the same "
    "way, so **the required annual payment and the shortfall above may be "
    "OVERSTATED** — and a year whose refundable credits bring the balance "
    "under $1,000 owes no penalty at any payment pace, however large the "
    "shortfall printed here (`/tax-safe-harbor` step 6 states that floor). "
    "Net those credits off the total tax, re-take 90% of it, and re-check the "
    "floor before paying against this figure. If this year's return claims no "
    "refundable credit at all, the figures above are already correct — but "
    "settle that against Form 2210's own line-3 enumeration for the covered "
    "year rather than against this sentence. Line 3 is where the set is "
    "defined, and it is not closed: the adoption credit named above joined it "
    "for tax years beginning after 2024.")


# Deliberately narrower than the current-year note below: CA conforms to
# §6654 through R&TC §19136, so the same netting question arises for the
# prior-year baseline too, but WHICH CA credits FTB 5805 nets against a
# PRIOR-year total is not a list this engine has verified against the FTB,
# and AGENTS.md §2 would need a live check before one were printed. Naming
# the gap is honest; naming an unverified list would be the lying caveat the
# repo ranks worse than a missing one.
_CA_PRIOR_TAX_UNNETTED_NOTE = (
    "- **Prior-year CA baseline is `ca_total_tax` as filed.** CA conforms to "
    "§6654 through R&TC §19136, and FTB 5805 nets certain CA credits against "
    "that prior-year total before it can anchor the safe-harbor prong — this "
    "engine does not net them. Check the prior-year figure against FTB "
    "5805's own instructions before relying on the CA prior-year prong "
    "(`/tax-safe-harbor` step 5).")


# R&TC §19136(c)(2) has TWO prongs and this engine can only test one of them.
# Verbatim: "...the tax imposed under Section 17041 or 17048 and the tax imposed
# under Section 17062 for the PRECEDING taxable year, minus the sum of any
# credits ... OR the tax computed ... upon the ESTIMATED INCOME FOR THE TAXABLE
# YEAR, minus the sum of any credits ..., is less than five hundred dollars".
# `compute_ca_safe_harbor` runs the second prong — current-year tax less
# withholding — and cannot run the first, which needs the PRIOR year's own
# credits and this engine carries only its total tax.
#
# The gap is one-directional and it is the bad direction: a taxpayer the first
# prong clears outright is shown a CA required annual payment, a shortfall and a
# voucher schedule, and may pay all of it. HANDED OVER rather than applied,
# because applying it turns on whether "credits ... provided by Part 10 or this
# part" reaches the §19002 withholding credit, and getting that wrong the other
# way CLEARS a penalty that attaches — under-stating what the taxpayer must pay,
# which is the failure this whole section exists to avoid. Naming the rule costs
# a bullet; deciding it unverified costs the answer.
#
# It fires OUTSIDE the de minimis, where the current-year prong already answered,
# and outside a $0 prior year, which the note above already clears by this same
# subdivision. The federal disclaimer is in it because §6654(e)(1) has no
# prior-year half at all — a reader who knows the federal floor would otherwise
# read the federal section above as evidence about this one.
# The prior-year prong of the SAME subdivision, in the subset where the figure
# this engine already holds settles it. `prior_tax` is Form 540 total tax as
# filed, which is an UPPER BOUND on §19136(c)(2)'s prior-year quantity: other
# taxes only add to the total, and the statute only ever SUBTRACTS credits from
# it. So a total under the floor proves the prong outright — whatever the
# credits were, and without settling whether the §19002 withholding credit is
# among the ones "provided by Part 10 or this part", which is the open question
# that kept the whole rule handed over rather than applied.
#
# The bound is ONE-DIRECTIONAL and that is why the two notes are not symmetric:
# under the floor proves no penalty, at or above it proves nothing either way,
# because the credits could still bring the statutory figure under.
_CA_PRIOR_YEAR_FLOOR_PROVED_NOTE = (
    "- **No CA underpayment penalty can arise for this year — the PRIOR-year "
    "floor is already met.** R&TC §19136(c)(2) imposes none when the preceding "
    "year's CA tax less credits, **or** the covered year's, is under $500 "
    "($250 MFS); FTB 5805 Part I lists both. Last year's Form 540 total tax is "
    "under that floor on its own, and credits only subtract from it, so the "
    "prong holds however those credits fall. The CA payment needed to avoid a "
    "penalty is $0 and the schedule below is not asking for one. This does NOT "
    "touch the CA tax itself, which is still due with the return.")


# The same proof at or above §19136.3, where the required annual payment above
# is 90% of the CURRENT year and a bare "$0 needed" would read as a bug against
# it. So this note explains the pair rather than restating the one: the
# limitation decides the REQUIREMENT, the floor decides the PENALTY, and they
# are different rules in different sections.
_CA_PRIOR_YEAR_FLOOR_AT_HIGH_INCOME_NOTE = (
    "- **No CA underpayment penalty can arise for this year — the PRIOR-year "
    "floor is already met — and the 90% figure above is not asking for one.** "
    "R&TC §19136.3 withdraws the prior-year PRONG at this AGI, which is why "
    "the required annual payment above is 90% of the current year rather than "
    "last year's tax. It reaches only that computation: §19136(c)(2) is a "
    "separate rule barring the addition to tax outright when the preceding "
    "year's CA tax less credits, **or** the covered year's, is under $500 "
    "($250 MFS), and FTB 5805's own instructions keep that order — the "
    "$1,000,000 rule says how to figure the penalty \"if you are subject to\" "
    "it. Last year's Form 540 total tax is under the floor on its own, and "
    "credits only subtract from it, so the prong holds however those credits "
    "fall. The CA payment needed to avoid a penalty is $0 and the schedule "
    "below is not asking for one. This does NOT touch the CA tax itself, "
    "which is still due with the return.")


# The third case of the same subdivision, and the one nothing covered: no
# prior-year CA figure at ALL. The two above read a KNOWN total — under the
# floor it is proved, at or above it the credits might still carry it under —
# and an absent one fell through both into silence. It is deliberately the
# LOUDEST of the family, because it is the only one printed beside a schedule
# that is still asking to be paid.
_CA_PRIOR_YEAR_FLOOR_UNKNOWN_NOTE = (
    "- **The prior-year half of the CA $500 floor is UNTESTED here, and it "
    "alone could zero the schedule below.** R&TC §19136(c)(2) imposes no CA "
    "underpayment penalty when the PRECEDING year's CA tax less credits, "
    "**or** the covered year's, is under $500 ($250 MFS) — FTB 5805 Part I "
    "lists both. This report has no prior-year CA figure at all, so only the "
    "covered-year half above was tested, and it did not clear. Read the TOTAL "
    "TAX line of the prior Form 540 before paying anything below: under the "
    "floor it settles the year on its own, because credits only subtract from "
    "that total and cannot carry it back over. The rule is separate from the "
    "prior-year PRONG and outlives it — R&TC §19136.3 withdraws the prong at "
    "$1,000,000 ($500,000 MFS) of current-year CA AGI and does not reach the "
    "floor — so it is worth asking at every income, including one where the "
    "row above reads 90% of the current year. §6654(e)(1) has no prior-year "
    "half, so the federal section is not evidence either way.")


# The SAME unresolved floor, printed where the schedule is all zeros. The note
# above is written against a live 540-ES column — "could zero the schedule
# below", "before paying anything below" — and both are false here: the annual
# requirement is met and nothing is outstanding. What is unsettled is whether a
# penalty ALREADY accrued on a late instalment, and the floor answers that
# question too, because §19136(c)(2) bars the addition to tax outright rather
# than adjusting an instalment. Two constants rather than one conditional
# sentence, for the reason the $0 pair above gives: a note describing the
# reader's own page gets read, and one describing a schedule they cannot see
# gets skipped — including the sentence in it that was true.
_CA_PRIOR_YEAR_FLOOR_UNKNOWN_TIMING_NOTE = (
    "- **The prior-year half of the CA $500 floor is UNTESTED here, and it "
    "alone could clear a penalty the payment PACE may already have caused.** "
    "The required annual payment above is met, so nothing is outstanding — "
    "but FTB 5805 tests each instalment cumulatively, and an estimate paid "
    "after a deadline does not cure the instalment it was late for. R&TC "
    "§19136(c)(2) imposes no CA underpayment penalty at all when the "
    "PRECEDING year's CA tax less credits, **or** the covered year's, is "
    "under $500 ($250 MFS) — FTB 5805 Part I lists both, and neither test "
    "asks WHEN anything was paid. This report has no prior-year CA figure at "
    "all, so only the covered-year half was tested, and it did not clear. "
    "Read the TOTAL TAX line of the prior Form 540 before working through any "
    "dated cumulative test: under the floor there is nothing left to test, "
    "because credits only subtract from that total and cannot carry it back "
    "over. The rule outlives the prior-year PRONG — R&TC §19136.3 withdraws "
    "the prong at $1,000,000 ($500,000 MFS) of current-year CA AGI and does "
    "not reach the floor — so it is worth asking at every income. "
    "§6654(e)(1) has no prior-year half, so the federal section is not "
    "evidence either way.")


_CA_EITHER_YEAR_FLOOR_NOTE = (
    "- **The CA $500 floor reads on EITHER year, and the prior-year half is "
    "not settled here.** R&TC §19136(c)(2) imposes no CA underpayment penalty "
    "when the PRECEDING year's CA tax less credits, **or** the covered year's, "
    "is under $500 ($250 MFS) — FTB 5805 Part I lists both. This engine tests "
    "the covered-year half (CA tax less withholding, above), and reads the "
    "prior year only as the Form 540 TOTAL, which is above the floor before "
    "any credits come off it. Those credits could still carry it under: if "
    "last year's Form 540 tax less its credits came to under $500, no CA "
    "penalty can arise this year whatever the schedule below says — check it "
    "before paying. §6654(e)(1) has no prior-year half, so the federal section "
    "is not evidence either way here.")


_CA_CURRENT_TAX_UNNETTED_NOTE = (
    "- **Current-year CA tax here is the computed Form 540 total.** The "
    "netting question the federal current-year note raises applies to the CA "
    "figures above too, and this engine does not net them either — so the CA "
    "required payment and shortfall may be OVERSTATED. Check the current-year "
    "figure against FTB 5805's own instructions before paying against it.")


def _de_minimis_margin(v: float) -> str:
    """The de-minimis margin, to the CENT.

    Everything else in these reports is whole dollars (`_money`), which is the
    right resolution for a figure someone acts on. This one is not: both floors
    are strict `<`, so the cents are what decide the cliff, and rounding a
    $0.40 margin to "$0" states the opposite of the fact — that the excuse is
    about to evaporate — in the one sentence written to convey it.
    """
    return f"${v:,.2f}"


@dataclass(frozen=True)
class _DeMinimisFloor:
    """A de-minimis floor's renderable identity, bound to its own figures.

    The status sentence existed twice, once per jurisdiction, differing only in
    the label and the statute — and the CA copy quoted "$500/$250-MFS" as a
    LITERAL where the federal one interpolated its constant, so raising
    `CA_ES_DE_MINIMIS` would have moved the arithmetic and left the sentence
    quoting the old floor. Both halves are built FROM the constants here, for
    the reason `_CatchupNote` below exists: a sentence with two renderers is
    one constant, not two copies kept in step by hand.
    """
    floor: str
    statute: str
    subject: str
    jurisdiction: str


_FEDERAL_FLOOR = _DeMinimisFloor(
    floor=f"${DE_MINIMIS_BALANCE:,}", statute="§6654(e)(1)",
    subject="tax", jurisdiction="federal")
_CA_FLOOR = _DeMinimisFloor(
    floor=f"${CA_ES_DE_MINIMIS:,}/${CA_ES_DE_MINIMIS_MFS:,}-MFS",
    statute="R&TC §19136; FTB 5805 Part I",
    subject="CA tax", jurisdiction="CA")


_CLIFF_NOTHING_ATTACHES = ("Past the floor the required annual payment is "
                           "still met on its own, so nothing attaches when it "
                           "lapses")


# The OTHER reason nothing attaches, and it needs its own sentence because
# `_CLIFF_NOTHING_ATTACHES` names a reason that is false here: the required
# annual payment is NOT met on its own in these runs, a separate exemption
# simply makes that irrelevant. A true tail under a false reason is the shape
# the `withholding < margin` case was already split out to avoid.
#
# Deliberately does not name the statute. This cell is shared with the federal
# floor, and the note that DOES name it is one file away: `_ca_notes` emits one
# of the four §19136(c)(2) prior-year notes — $0 or under-the-floor, crossed
# with below or at the §19136.3 limitation — in every run that reaches here,
# and each carries the citation, the $500/$250 figures and the warning that the
# tax itself is still due. A fifth copy is a fifth thing to drift.
_CLIFF_SECOND_EXEMPTION = (
    "A separate exemption clears the year on its own, independently of this "
    "floor and of the payment pace — see the note naming it below — so "
    "nothing attaches when the floor lapses, however the projection turns out")


def _de_minimis_cell(floor: _DeMinimisFloor, harbor,
                     tax_is_actual: bool) -> str:
    """The de-minimis status cell, shared by both jurisdictions.

    Under either floor no penalty can arise regardless of the payment pace, so
    the penalty target reads covered — with the reason, since the zero-balance
    target may still show tax due and a bare "covered" against a visible
    balance owed reads as a bug in the report rather than as the statute.

    The TAIL must not assert that a penalty attaches once the floor lapses:
    that is false wherever the required annual payment is already met.
    `de_minimis_cliff_more_tax` and `de_minimis_cliff_less_withholding` decide
    it, and 0.0 in both means nothing attaches. They are read SEPARATELY so
    the cell names only the paths its own inputs can take — a single
    worse-of-the-two figure attributes every positive to withholding, and $500
    of tax against $0 withheld then announces "$500 less withholding and it
    attaches" against withholding that cannot fall below zero.

    `tax_is_actual` names WHICH side of the measurement is the uncertain one.
    /tax-project annualizes the tax, so the projection is what may be wrong.
    /tax-withholding reads the tax from result.json — its own Assumptions call
    it "the **actual computed** tax", and only the withholding pace is
    annualized — so telling that reader to watch for "more tax than projected"
    aims them at the one input in the report that cannot move.

    Named for the TAX, never for the report: the two invert. /tax-project is
    all projection and takes `tax_is_actual=False`; a caller reasoning "my
    report is a projection, so False" is right there by accident and wrong on
    a report whose tax is actual, where it selects the branch pointing at the
    number that cannot move.

    The `withholding < margin` case has no lapse at all: `margin` is
    `floor - (tax - withholding)`, so `withholding >= margin` is the same
    statement as `tax >= floor`, and below it no withholding pace reaches the
    floor. The "nothing attaches" tail cannot carry that case either, because
    its REASON — the required annual payment being met on its own — is false
    there.

    And a SECOND exemption outranks every branch here. R&TC §19136(c)(2) reads
    on either year, so a prior-year Form 540 total already under the floor
    clears the year whatever the current-year projection does — the cliff is
    then a warning about a number that decides nothing, aimed at a taxpayer
    who is provably safe. That is the class of gate this repo treats as a
    defect, not as harmless caution: one that fires on correct data is one its
    reader learns to read past, including on the runs where it was right.
    """
    m = _de_minimis_margin(harbor.de_minimis_margin)
    cell = (f"covered — {floor.subject} minus withholding is {m} under the "
            f"{floor.floor} de minimis ({floor.statute}): no "
            f"{floor.jurisdiction} underpayment penalty applies. ")
    # Before any cliff talk: a second exemption makes the whole cliff moot.
    # It is checked first rather than folded into the branches below because
    # every one of them, the `withholding < margin` case included, is a
    # statement about the CURRENT year's test, and none of them is the reason
    # this run is safe.
    if _prior_year_floor_applied(harbor):
        return cell + _CLIFF_SECOND_EXEMPTION
    more_tax = harbor.de_minimis_cliff_more_tax
    less_withholding = harbor.de_minimis_cliff_less_withholding
    if tax_is_actual:
        if harbor.withholding < harbor.de_minimis_margin:
            return cell + (f"The {floor.subject} here is the computed "
                           f"liability and is under the floor on its own, so "
                           f"no withholding pace lapses it")
        if less_withholding <= 0:
            return cell + _CLIFF_NOTHING_ATTACHES
        return cell + (f"That is a cliff on the WITHHOLDING pace — the "
                       f"{floor.subject} here is the computed liability and "
                       f"does not move, but {m} less withholding than the "
                       f"current pace projects and it attaches")
    if more_tax > 0 and less_withholding > 0:
        return cell + (f"That is a cliff on a PROJECTION — {m} more "
                       f"{floor.subject} than projected, or that much less "
                       f"withholding, and it attaches")
    if more_tax > 0:
        return cell + (f"That is a cliff on the {floor.subject} PROJECTION — "
                       f"{m} more {floor.subject} than projected and it "
                       f"attaches; withholding is not the side that moves it")
    if less_withholding > 0:
        return cell + (f"That is a cliff on the WITHHOLDING projection — {m} "
                       f"less withholding than projected and it attaches; the "
                       f"{floor.subject} is not the side that moves it")
    return cell + _CLIFF_NOTHING_ATTACHES


def _federal_status(sh: SafeHarbor, tax_is_actual: bool = False) -> str:
    """Status cell for the federal safe-harbor table."""
    if sh.de_minimis:
        return _de_minimis_cell(_FEDERAL_FLOOR, sh, tax_is_actual)
    return "covered" if sh.covered else "underpaid"


def _ca_status(csh: CaSafeHarbor, tax_is_actual: bool = False) -> str:
    """Status cell for the CA safe-harbor table.

    The prior-year floor needs its own reason for the same argument
    `_de_minimis_cell` opens with: a bare "covered" against a visible gap reads
    as a bug in the report rather than as the statute. That gap only became
    visible when the floor started applying at the §19136.3 limitation — up
    there `required_annual` stays at 90% of the CURRENT year, so the table can
    show $108,000 required against $30,000 paid and call it covered. Below the
    limitation the prong also shrinks the requirement, and there was nothing
    for a reader to reconcile.

    Deferred to the note rather than re-citing §19136(c)(2) here, for the
    reason `_CLIFF_SECOND_EXEMPTION` gives: `_ca_notes` prints one of the four
    prior-year notes in every run that reaches this branch, each with the
    citation and the $500/$250 figures, and a copy here is a copy to drift.
    """
    if csh.de_minimis:
        return _de_minimis_cell(_CA_FLOOR, csh, tax_is_actual)
    if _prior_year_floor_applied(csh):
        return ("covered — a separate exemption clears the year on its own, "
                "whatever the required annual payment above and the payments "
                "against it come to; see the note naming it below")
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
    # A prior-year Form 540 total tax of exactly $0 is a statutory answer, not
    # a missing figure, and saying which rule produced the $0 target is what
    # keeps it from reading as a bug. Two notes, because §19136.3 splits what
    # has to be EXPLAINED and not whether the rule applies: below the
    # limitation the $0 is visible in the required payment itself, at or above
    # it the required payment is 90% of the current year and the $0 penalty
    # target sitting beside it needs the second rule named.
    if csh.prior_tax == 0 and not csh.high_income_90_only:
        notes.append(
            "- **Prior-year CA tax was $0 (R&TC §19136(c)(2)):** no CA "
            "underpayment penalty arises when either year's tax less credits "
            "is under $500 ($250 MFS), and a $0 prior year clears that "
            "outright. This is a California rule in its own right — unlike "
            "§6654(e)(2) it asks nothing about citizenship, residency or the "
            "length of the prior year. The required annual payment below is "
            "$0 for that reason, not for want of a baseline.")
    if csh.prior_tax == 0 and csh.high_income_90_only:
        notes.append(
            "- **A $0 prior-year CA tax is on file, so no CA underpayment "
            "penalty can arise for this year.** §19136.3 withdraws the "
            "prior-year prong at this AGI, which is why the required annual "
            "payment above is 90% of the current year. It reaches only that "
            "computation: R&TC §19136(c)(2) is a separate rule barring the "
            "addition to tax outright when either year's CA tax less credits "
            "is under $500 ($250 MFS), and a $0 prior year clears it however "
            "the credits fall. The CA payment needed to avoid a penalty is $0 "
            "and the schedule below is not asking for one. The CA tax itself "
            "is still due with the return.")
    if csh.prior_year_floor and not csh.high_income_90_only:
        notes.append(_CA_PRIOR_YEAR_FLOOR_PROVED_NOTE)
    elif csh.prior_year_floor:
        notes.append(_CA_PRIOR_YEAR_FLOOR_AT_HIGH_INCOME_NOTE)
    elif csh.prior_year_floor_unknown:
        # WHICH of the two: the note above is written against a live 540-ES
        # column, this one against an all-zero one carrying an accrual the
        # annual figure cannot show. `shortfall` is the same quantity
        # `compute_ca_safe_harbor` branched the flag on, and it is zeroed only
        # under `no_penalty` — false wherever this flag is true, an absent
        # prior tax being neither the floor nor the $0 case — so the two
        # spellings cannot disagree about which reader this is.
        notes.append(_CA_PRIOR_YEAR_FLOOR_UNKNOWN_NOTE if csh.shortfall > 0
                     else _CA_PRIOR_YEAR_FLOOR_UNKNOWN_TIMING_NOTE)
    elif not csh.de_minimis and csh.prior_tax:
        # `csh.prior_tax` truthy, not `!= 0`: None is the degraded case, where
        # there is no prior figure to reason from at all, and it read as a
        # live one under the old comparison.
        notes.append(_CA_EITHER_YEAR_FLOOR_NOTE)
    if csh.degraded:
        notes.append("- **Degraded:** no usable prior-year CA baseline — "
                     "using the 90%-of-current-year test only. Either "
                     "`archive/{year-1}/` is missing, or its `result.json` "
                     "carries no COMPUTED `california_540` section (an "
                     "all-zero template scaffold is not one). That "
                     "archive is the ONLY route to the CA prior-year prong: "
                     "unlike the federal base there is no "
                     "`projection_inputs.json` field for a hand-entered Form "
                     "540 figure, so run `/tax-archive` on the prior year — "
                     "and a prior year computed outside this toolkit cannot "
                     "supply the prong at all. The §19136(c)(2) floor is a "
                     "separate question, and one the same Form 540 still "
                     "answers by hand whatever this report can compute.")
    if csh.current_tax_unnetted:
        notes.append(_CA_CURRENT_TAX_UNNETTED_NOTE)
    if csh.prior_tax_unnetted:
        notes.append(_CA_PRIOR_TAX_UNNETTED_NOTE)
    if csh.prior_agi_missing:
        notes.append(
            "- **Prior-year CA AGI unavailable** — assumed at or below the "
            f"{_PRIOR_AGI_THRESHOLD_TEXT} threshold (100% factor); if prior CA "
            "AGI exceeded it, the 110% harbor applies and the required "
            "payment shown may be UNDERSTATED.")
    notes.append(
        "- Not modeled: farmers/fishermen (FTB 5805F — 66⅔% harbor, single "
        "Jan 15 installment; this 30/40/0/30 schedule does not apply to "
        "them) and the prior-year-harbor precondition of a filed 12-month "
        "prior-year CA return.")
    return notes


def render_markdown(r: ProjectionReport) -> str:
    f = r.federal
    sh = r.safe_harbor
    lines = [f"# Mid-Year Tax Projection — TY{r.year} (as of {r.as_of})", ""]
    if r.stale_as_of_note:
        lines += [r.stale_as_of_note, ""]

    lines += ["## Assumptions", ""]
    lines.append(f"- Elapsed fraction of year: {r.proj.elapsed_fraction:.0%}")
    lines.append(f"- Pay frequency: {r.pay_frequency} ({r.proj.paychecks_remaining} paychecks remaining)")
    lines.append("- Steady wages and withholding are annualized from YTD (assumes the rest of the "
                 "year continues at the YTD rate); bonuses, vesting, and capital gains are taken "
                 "as the full-year amounts you entered.")
    lines += _prior_baseline_lines(
        r.baseline_source, r.year,
        ca_prior_resolved=(r.ca_safe_harbor is not None
                           and r.ca_safe_harbor.prior_based is not None),
        degraded=r.degraded)
    # Independent of `degraded`: an explicit `prior_year_form_2210_tax_base`
    # can be supplied with no `archive/{year-1}/` at all, clearing `degraded`
    # while `recurring_from_prior` (interest/dividends, populated by the
    # SKILL from that same archive) is still silently defaulted to 0.
    if not r.has_prior_archive:
        lines.append(
            "- **No prior-year archive:** `recurring_from_prior` "
            "(interest/dividends) was not pre-filled from a prior return and "
            "defaults to $0 unless entered directly in "
            "`projection_inputs.json` — real recurring income left at $0 "
            "understates the projection.")
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
              f"| Deduction ({'itemized' if f.itemized else 'standard'}) | {_money(f.deduction)} |"]
    # The other two deductions between AGI and taxable income, where they
    # apply, so the table shows every one (taxable income still floors at 0).
    if f.schedule_1a_deduction:
        lines.append(f"| Schedule 1-A deduction | {_money(f.schedule_1a_deduction)} |")
    if f.qbi_deduction:
        lines.append(f"| QBI deduction | {_money(f.qbi_deduction)} |")
    lines += [f"| Taxable income | {_money(f.taxable_income)} |",
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
    if r.mfs_deduction_note:
        lines += [f"> **⚠ {r.mfs_deduction_note}**", ""]

    lines += ["## Safe harbor analysis", "",
              "| Test | Amount |", "|---|---:|",
              f"| 90% of projected current-year tax | {_money(sh.pct90_current)} |"]
    if not sh.degraded:
        lines.append(f"| {sh.prior_factor:.0%} of prior-year tax | {_money(sh.prior_based)} |")
    lines += [f"| **Required annual payment** | {_money(sh.required_annual)} |",
              f"| Withholding + estimates paid | {_money(sh.withholding + sh.estimates_paid)} |",
              f"| **Shortfall** | {_money(sh.shortfall)} |",
              f"| Status | {_federal_status(sh)} |", ""]
    if sh.current_tax_unnetted:
        lines += [_CURRENT_TAX_UNNETTED_NOTE, ""]
    if sh.prior_agi_missing:
        lines += ["- **Prior-year AGI unavailable** — assumed at or below the "
                  f"{_PRIOR_AGI_THRESHOLD_TEXT} threshold (100% factor); if "
                  "prior AGI exceeded it, the 110% harbor applies and the "
                  "required payment shown may be UNDERSTATED.", ""]
    if _prior_prong_precondition_applies(sh):
        lines += [_PRIOR_PRONG_PRECONDITION_NOTE, ""]

    # The lead names the lever the year still HAS — `_w4_lever_note`. It is
    # here rather than below the schedule because a 4(c) bump cures an
    # ALREADY-missed instalment where a voucher only stops further accrual, so
    # the catch-up note's "pay catch-up amounts NOW" would otherwise steer the
    # reader to the weaker remedy at the one moment the stronger one is on
    # screen. Where there is no payroll left it says the opposite, which is
    # exactly as load-bearing.
    lines += ["## Recommended actions", ""]
    if r.w4_lever_note:
        lines += [r.w4_lever_note, ""]
    lines += ["### 1040-ES voucher schedule", "",
              "| Quarter | Due | Amount |", "|---|---|---:|"]
    for v in r.vouchers:
        lines.append(f"| Q{v.quarter} | {v.due_date} | {_money(v.amount)} |")
    lines.append("")
    # DATES before AMOUNTS: the closed-accrual note contradicts the catch-up
    # note's "keeps accruing until it is paid" and says so explicitly, which
    # only works if the reader meets it first.
    if r.voucher_dates_note:
        lines += [r.voucher_dates_note, ""]
    if r.voucher_catchup_note:
        lines += [r.voucher_catchup_note, ""]
    lines += ["### W-4 line 4(c) per-paycheck extra withholding",
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
        # The TABLE carries the bracket-estimate qualification too, not only
        # the W-4 lede. Qualifying one surface and not the other is worse than
        # qualifying neither: the lede says "see the state section below" and
        # sends the reader from the hedged sentence to a bolded, unhedged
        # restatement of the same figure, which reads as the authoritative one.
        # Only California is computed in depth (AGENTS.md: "other states are
        # bracket-estimate only"); the Caveats line about scheduling qualifies
        # the SCHEDULE, never the amount.
        lines += ["| Item | Amount |", "|---|---:|",
                  f"| State ({s.code}) deduction ({'itemized' if s.itemized else 'standard'}) | {_money(s.deduction)} |",
                  f"| State ({s.code}) taxable income | {_money(s.taxable_income)} |",
                  f"| State tax | {_money(s.tax)} |",
                  f"| State withholding | {_money(-s.withholding)} |",
                  f"| **State balance** | {_money(s.balance)} "
                  f"{'(refund)' if s.balance < 0 else '(owed)'} |"]
        if r.state_bracket_estimate_note:
            lines += ["", f"> **⚠ {r.state_bracket_estimate_note}**"]
        if r.state_mfs_deduction_note:
            lines += ["", f"> **⚠ {r.state_mfs_deduction_note}**"]
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
        lines += ["", _CA_ES_HEADING, "",
                  _CA_ES_WEIGHTING + _CA_ES_SHARE_COLUMN_CAVEAT, "",
                  "| Installment | Due | Share of remaining | Amount |",
                  "|---|---|---:|---:|"]
        # Share of the REMAINING shortfall, derived from the AMOUNTS beside it
        # rather than recomputed from the statutory weights. Printing a weight
        # next to an amount allocated on some other rule misleads, and it does
        # not matter whether the mismatch comes from renormalization (the first
        # version of this, "30%" beside 100% of the shortfall after Jun 15) or
        # from the schedule taking a different path entirely: before the first
        # deadline `compute_ca_estimated_vouchers` allocates against the
        # cumulative 30/70/70/100 checkpoints NET of estimates already paid, so
        # a $700 shortfall with $300 already in prints 0/57/0/43 while the
        # renormalized weights read 30/40/0/30 — a table contradicting itself
        # on its own two columns. Reading the amounts cannot drift from them by
        # construction, and on the `_split_remainder_last` path it reproduces
        # the renormalized weight to the rounding of the column it is summing.
        # Share of the REMAINING shortfall, derived from the AMOUNTS beside it
        # rather than recomputed from the statutory weights — a weight printed
        # next to an amount allocated on some other rule misleads, and reading
        # the amounts cannot drift from the column it sits next to.
        for v, share in zip(r.ca_vouchers,
                            _percent_shares([v.amount for v in r.ca_vouchers])):
            lines.append(f"| Q{v.quarter} | {v.due_date} | {share}% | "
                         f"{_money(v.amount)} |")
        lines.append("")
        lines += _ca_split_note(r.ca_vouchers, csh.estimates_paid)
        if r.ca_voucher_dates_note:
            lines += [r.ca_voucher_dates_note, ""]
        if r.ca_voucher_catchup_note:
            lines.append(r.ca_voucher_catchup_note)
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
    if r.itemized_deduction_note:
        lines.append(f"- **Declared itemized deductions:** {r.itemized_deduction_note}")
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
