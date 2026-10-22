#!/usr/bin/env python3
"""Capability matrix / scope guard for /tax-compute (C1) + limitations manifest (C2).

Declarative map of taxpayer-profile situations → engine support level. The
pipeline's honesty invariant is "never silently understate": when the interview
records a situation the engine cannot compute (rental Schedule E, Form 8962
reconciliation, part-year residency, residency in a second state, income
sourced to another state, kiddie tax, Schedule H, a PFIC taxable event),
/tax-compute must HARD-STOP with the named form and options rather than produce
a plausible-looking wrong return. Situations the skills handle with manual
worksheets (ISO/AMT, 8863, 2441, 5695/8936, gambling, FEIE-vs-FTC) are
non-blocking limitations that land in result.json meta.limitations (the C2
manifest, printed on page 1 of the PDF by /tax-generate).

Pure compute + markdown renderer + CLI, following projection / yoy_diff.py.
Read-only: never writes any file (the SKILL copies the manifest lines into
result.json).

Blocking items can be ACKNOWLEDGED: the user explicitly accepts the gap, the
skill records that decision in answers.md, and the rule id is added to
taxpayer_profile.json `acknowledged_limitations` (or passed via --acknowledge
for a one-off run) — the run then proceeds with the item carried loudly in the
manifest instead of blocking. An unknown id in the acknowledged list is a HARD
ERROR: a typo must not silently unblock nothing (same orphan-guard rationale as
ptet's rate-override validation).
"""

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib import receipt  # noqa: E402
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_subpath,
)
from agents.lib.secure_fs import redact  # noqa: E402
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    hold_workspace,
    resolve_workspace,
)


class CapabilityError(Exception):
    """Raised when the capability gate cannot safely evaluate a profile."""


UNSUPPORTED = "unsupported"
MANUAL = "manual"

# Validated scope codes, including the inhabited territories for federal gates.
_TERRITORIES = frozenset({"PR", "VI", "GU", "AS", "MP"})
_USPS_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY DC".split()
) | _TERRITORIES


@dataclass(frozen=True)
class Rule:
    rule_id: str
    flag: str  # taxpayer_profile.json flags.<name>, must be a bool when present
    level: str  # UNSUPPORTED (blocks compute) | MANUAL (manifest note only)
    situation: str
    form: str
    note: str
    state: "str | None" = None

    def __post_init__(self):
        if self.level not in (UNSUPPORTED, MANUAL):
            raise CapabilityError(f"invalid rule level {self.level!r}")
        if self.state is not None and self.state not in _USPS_STATES:
            raise CapabilityError(f"invalid rule state {self.state!r}")


@dataclass(frozen=True)
class Finding:
    rule_id: str
    level: str
    situation: str
    form: str
    note: str
    acknowledged: bool


@dataclass(frozen=True)
class CapabilityReport:
    blocking: "tuple[Finding, ...]"  # unsupported, NOT acknowledged → gate fails
    acknowledged: "tuple[Finding, ...]"  # unsupported, user accepted the gap
    manual: "tuple[Finding, ...]"
    state_note: str
    limitations: "tuple[str, ...]"  # C2 manifest lines for result.json meta.limitations


# The registry. Flags must exist in taxpayer_profile_template.json — the
# /tax-setup interview is what sets them, so a rule keyed on a flag the
# interview never asks is dead (the D1 lesson from expected_sources).
RULES: "tuple[Rule, ...]" = (
    Rule("ca_blind_exemption", "ca_has_blind_exemption", MANUAL,
         "CA blind exemption", "Form 540 line 8",
         "Confirm each filer's eligibility and any required doctor's statement. The checked Form 540 builder computes the eligible credit.", state="CA"),
    Rule("ca_other_schedule_ca", "ca_has_other_schedule_ca_adjustments", UNSUPPORTED,
         "Other California/federal income differences", "Schedule CA (540) Part I",
         "Section 1202, basis/depreciation, K-1, moving expenses, IRA basis, "
         "California lottery winnings not on a W-2G, educator expenses and FEIE adjustments are not computed; prepare each applicable line.", state="CA"),
    Rule("ca_state_refund", "has_1099g_state_refund", MANUAL,
         "State refund requiring the federal tax-benefit worksheet", "Schedule 1 line 1 / Schedule CA (540)",
         "Confirm the federal tax-benefit worksheet and its taxable amount, including a worked zero. The checked CA builder subtracts that amount; gross 1099-G refund is not the subtraction.", state="CA"),
    Rule("ca_noniso_amt", "ca_has_noniso_amt_items", MANUAL,
         "CA AMT adjustments requiring source worksheets", "Schedule P (540), Form 540 line 61",
         "Box A/B preferences and adjustments, investment interest, installment-sale income and "
         "mortgage interest on debt not used to buy or improve the home require separate source worksheets. "
         "Record their signed net as ca_preference_adjustments; the checked builder computes Schedule P on every return.", state="CA"),
    Rule("ca_distribution_taxes", "ca_has_special_distribution_tax", UNSUPPORTED,
         "Lump-sum or accumulation distribution tax", "Form 540 line 34 / Schedule G-1 / FTB 5870A",
         "These additional distribution taxes are not computed.", state="CA"),
    Rule("ca_other_taxes", "ca_has_other_taxes", UNSUPPORTED,
         "CA early-distribution and other additional taxes", "Form 540 line 63 / FTB 3805P",
         "California's additional taxes are not copied from federal forms and are not computed.", state="CA"),
    Rule("ca_ptet_credit", "ca_has_ptet_credit", MANUAL,
         "CA electing-entity PTET credit or carryover", "FTB 3804-CR / Form 540 lines 43-45",
         "The K-1 credit and any unused carryover need FTB 3804-CR. This is a nonrefundable "
         "special credit with vintage carryovers, computed for direct individual owners. "
         "Disregarded SMLLC tax-difference limits require separate reviewed support. Never a payment.", state="CA"),
    Rule("ca_amt_credit", "ca_has_amt_credit", MANUAL,
         "CA prior-year AMT credit", "FTB 3510 / Schedule P credit code 188",
         "Reconcile the qualified opening balance; the checked builder computes FTB3510 Part II. Preserve the "
         "opening credit less the credit ALLOWABLE this year plus new ISO credit. An unclaimed "
         "allowable credit requires an amended return, not an inflated carryforward.", state="CA"),
    Rule("ca_other_credit_carryover", "ca_has_other_credit_carryover", UNSUPPORTED,
         "Other CA special-credit carryovers", "Form 540 lines 43-45 / FTB 3540",
         "Existing credit carryovers must be worked separately; omitting them overstates CA tax.", state="CA"),
    Rule("ca_use_tax", "ca_owes_use_tax", UNSUPPORTED,
         "CA use tax due", "Form 540 line 91",
         "Use tax is not computed; leaving it zero understates the amount payable.", state="CA"),
    Rule("ca_coverage_penalty", "ca_has_coverage_penalty", UNSUPPORTED,
         "CA health-coverage penalty", "Form 540 line 92 / FTB 3853",
         "The coverage penalty and exemptions require a separate FTB 3853 worksheet.", state="CA"),
    Rule("ca_renters_credit", "ca_paid_rent", MANUAL,
         "Rent paid for a California residence", "Form 540 line 46, renter's credit worksheet",
         "Check the covered-year income and eligibility worksheet. The renter's credit is not computed; "
         "omitting an eligible credit overstates CA tax.", state="CA"),
    Rule("ca_special_credits", "ca_claims_special_credits", MANUAL,
         "CA special credits claimed from a separate worksheet", "Form 540 lines 43-45 / Schedule P Part III",
         "Prepare each credit's own covered-year worksheet and Schedule P limitation. Carryovers and "
         "owner PTET/AMT credits have their own checked computations and intake facts.", state="CA"),
    Rule("wa_special_gains", "wa_has_special_gain_rules", UNSUPPORTED, "WA special federal gain rules", "RCW 82.87.020 normalization", "Section 1256/QOF and other special rules require a WA gain worksheet not modeled here.", state="WA"),
    Rule("wa_separate_household", "wa_has_separate_household", UNSUPPORTED, "WA separate spouse/RDP household", "RCW 82.87.120 allocation", "One household shares all WA deductions and credits; separate allocations are not modeled.", state="WA"),
    Rule("nc_business_adjustments", "nc_has_business_adjustments", UNSUPPORTED,
         "NC bonus depreciation or section 179 adjustments", "D-400 Schedule S A3/A4",
         "NC business add-backs are not modeled; prepare the year's Schedule S manually.", state="NC"),
    Rule("nc_recovery_deductions", "nc_has_recovery_deductions", UNSUPPORTED,
         "NC prior-year recovery deductions", "D-400 Schedule S B23/B24/B34-B37",
         "Omitting prior-year recovery deductions OVER-states NC tax; they require a manual worksheet.", state="NC"),
    Rule("nc_nol", "nc_has_nol", UNSUPPORTED,
         "NC or federal net operating loss adjustments", "D-400 Schedule S A7/B39 / NC-NOL",
         "Federal NOL add-backs and NC NOL deductions are not modeled.", state="NC"),
    Rule("nc_taxed_pte", "nc_has_taxed_pte", UNSUPPORTED,
         "NC Taxed-PTE owner income or loss", "D-400 Schedule S A14/B38",
         "An NC Taxed-PTE owner's deduction or loss add-back requires a manual return.", state="NC"),
    Rule("nc_other_deductions", "nc_has_other_schedule_s_deductions", UNSUPPORTED,
         "NC Schedule S deductions outside lines 17-21", "D-400 Schedule S B22/B25-B33",
         "Omitting these deductions OVER-states NC tax; record each applicable line and prepare it manually.", state="NC"),
    Rule("nc_other_credits", "nc_has_other_credits", UNSUPPORTED,
         "NC historic rehabilitation, credit carryovers or business credits", "D-400TC Part 2 / NC-478",
         "Only D-400TC Part 1 is computed; omitted credits OVER-state NC tax.", state="NC"),
    # --- UNSUPPORTED: the engine would produce a materially wrong return ---
    Rule("rental_income", "has_rental_income", UNSUPPORTED,
         "Rental real estate income", "Schedule E / Form 8582",
         "Rental income and expenses, depreciation, and the §469 passive-loss "
         "limits are not computed."),
    Rule("marketplace_insurance", "has_marketplace_insurance", UNSUPPORTED,
         "Marketplace (ACA) health insurance", "Form 8962",
         "Premium-tax-credit reconciliation is MANDATORY when advance PTC was "
         "paid — filing without it delays refunds and triggers IRS letters."),
    Rule("part_year_residency", "moved_states_this_year", UNSUPPORTED,
         "Moved states during the tax year", "Part-year state returns",
         "Part-year/nonresident income allocation and the other-state tax "
         "credit are not modeled."),
    Rule("move_abroad", "moved_abroad_this_year", UNSUPPORTED,
         "Moved to or from another country during the tax year",
         "Part-year state returns",
         "A move abroad is not a move between states, so "
         "`part_year_residency` does not see it, and a state with an income "
         "tax would otherwise get a full-year resident return that taxes the "
         "months lived outside the country: an over-statement. Part-year "
         "allocation is not modeled, whichever side of the border the other "
         "home is on. Like `part_year_residency`, it blocks whatever the "
         "state, including one with no income tax, where the state return "
         "is not at risk and the acknowledgement is the way through. A "
         "taxpayer who is not a US citizen can also have a "
         "dual-status federal year (Form 1040-NR), which is not modeled "
         "either."),
    Rule("territory_resident", "bona_fide_territory_resident", UNSUPPORTED,
         "Bona fide resident of a US territory (Puerto Rico, American Samoa, "
         "Guam, the Northern Mariana Islands or the US Virgin Islands)",
         "Puerto Rico §933 / Form 4563 (§931) / Form 8689 (§932) / the "
         "territory's own return (§935)",
         "Territory-source income a Puerto Rico (§933) or American Samoa "
         "(§931) resident excludes has no path into or out of this return, "
         "and modified AGI adds it back: without it the SALT cap's phasedown "
         "and Schedule 1-A's phase-outs read AGI alone, which OVER-states the "
         "deduction. A Guam or Northern Marianas resident files with the "
         "territory (§935) and a Virgin Islands resident under §932; neither "
         "is modeled. `lives_abroad` does not reach any of them — a territory "
         "is not a foreign country — and a territory ADDRESS or "
         "`residency.domicile_state` blocks here whatever the flag says. "
         "Acknowledging it means the excluded income is added back into "
         "modified AGI and provisional income by hand."),
    Rule("statutory_residency_second_state",
         "statutory_resident_of_another_state", UNSUPPORTED,
         "Resident of a second state under its mechanical residency test",
         "That state's RESIDENT return",
         "A state can tax a full-year resident on ALL of their income whatever "
         "their domicile is, on a mechanical day-and-abode test, so a mover "
         "who recorded a Florida Declaration of Domicile and kept the "
         "Manhattan apartment can be a resident of BOTH. Two resident returns "
         "and the credit between them are not modeled. Neither neighbouring "
         "rule catches this: `part_year_residency` fires on a mid-year MOVE, "
         "and `nonresident_source_income` on income SOURCED elsewhere, which "
         "statutory residency does not need. The thresholds are stated once, "
         "with their sources, in docs/contributing/residency-guardrails.md; "
         "the counts behind the answer belong in `residency` and `answers.md`."),
    Rule("nonresident_source_income", "has_nonresident_source_income",
         UNSUPPORTED,
         "Out-of-state source income (W-2, K-1, or rental) while resident "
         "elsewhere",
         "Nonresident state return",
         "Wages, K-1 pass-through income, or rental income sourced to a "
         "state other than the resident state can create a nonresident "
         "filing obligation there — and, with no resident-state income tax "
         "to credit it against (e.g. a no-tax-state resident), that other "
         "state's tax is a pure cost with no offsetting mechanism. This is "
         "DISTINCT from `moved_states_this_year`/`part_year_residency`, "
         "which only fires on a mid-year move — a full-year resident with "
         "out-of-state source income is not otherwise caught. Nonresident "
         "state returns are not prepared; the source state's tax on that "
         "income is unmodeled."),
    Rule("household_employees", "has_household_employees", UNSUPPORTED,
         "Household employees (nanny tax)", "Schedule H",
         "Household-employment payroll taxes are not modeled."),
    Rule("kiddie_tax", "has_dependent_unearned_income", UNSUPPORTED,
         "Dependent with unearned income over the kiddie-tax threshold",
         "Form 8615",
         "Tax at the parents' rate on a child's unearned income is not "
         "modeled (and the dependent may have their own filing requirement)."),
    Rule("pfic_taxable_event", "has_pfic_taxable_event", UNSUPPORTED,
         "Foreign-fund (PFIC) distribution or disposition", "Form 8621 (§1291)",
         "A non-US-domiciled mutual fund or ETF (a PFIC, §1291-1298) had a "
         "distribution OR was sold this year. The default §1291 regime is "
         "PUNITIVE and is NOT computed: an excess distribution or gain on sale "
         "is spread across the holding period, taxed at the top ordinary rate "
         "for each prior year PLUS a compounding interest charge — so routing it "
         "through the normal qualified-dividend / long-term-capital-gain path "
         "would UNDERSTATE the tax. A timely QEF (§1295) or mark-to-market "
         "(§1296) election changes the treatment but is not modeled either, and "
         "for a delisted/sanctioned fund the marketable-stock test for MTM may "
         "fail, leaving §1291 unavoidable. /tax-compute now COMPUTES the §1291 "
         "excess-distribution / disposition tax (compute_pfic_1291: ratable "
         "holding-period allocation, each prior PFIC year at that year's highest "
         "ordinary rate + the §6621 interest charge) — but only from the attested "
         "foreign_holdings_detail facts (distributions by year, holding dates, the "
         "fetched historical top-rate table) AND only when the computation returns "
         "complete=True; a missing rate, an uncovered interest quarter, or absent "
         "distribution history leaves it INCOMPLETE. Because the regime is "
         "punitive and easy to under-state, this stays a hard stop by default: "
         "acknowledge it (recording the computed compute_pfic_1291 result in "
         "answers.md) to proceed, or prepare Form 8621 with a preparer. "
         "(Buy-and-hold with NO distribution or sale is not blocked here — the "
         "reporting-only §1298(f) obligation, with its $25K/$50K-MFJ de-minimis "
         "exception, is carried on the has_foreign_mutual_funds manifest note.)"),
    Rule("crypto_specific_identification", "has_crypto_specific_identification", UNSUPPORTED,
         "Specific identification of digital-asset lots at any broker or wallet",
         "Form 8949 (Regs. 1.1012-1(j), 1.6045-1(d)(2)(ii)(B))",
         "The crypto engine supports FIFO only. Specific identification, including standing "
         "instructions, HIFO or LIFO, can change basis in either direction even when a "
         "noncovered Form 1099-DA reports no basis. Include prior disposals and transfers "
         "whose lot choices affect opening inventory, at every broker and wallet. Prepare "
         "the selected lots and Form 8949 externally. Acknowledging this limitation does "
         "not enable FIFO import pricing. Box 8's acquisition information alone does not "
         "establish the taxpayer's selection method."),
    Rule("digital_asset_option_exercise", "has_digital_asset_option_exercise", UNSUPPORTED,
         "Digital asset sold, exchanged or spent because an option was exercised, "
         "or from a lot an option's exercise bought in any year",
         "Form 8949 (Pub. 550, Options)",
         "An option's premium moves the amount realized on a disposal it caused (up "
         "for a written call, down for a purchased put) or the basis of a lot "
         "it bought (up by a purchased call's cost, down by a written put's "
         "premium), in whatever year the exercise fell. Assets sold, exchanged or "
         "spent from that lot all use that basis, and the crypto import prices "
         "neither adjustment. Form 1099-DA box 3a cannot clear a sale: \"Net "
         "proceeds\" refuses the import, and \"Gross proceeds\" can show on an "
         "ordinary sale and on an option's sale alike, a broker need not net a "
         "premium on an option granted or acquired before 2026 (Regs. 1.6045-1(m)). "
         "Acknowledging this leaves those disposals in the import, priced without "
         "the premium, since no input marks one transaction for hand "
         "preparation (AA47): correct those Form 8949 rows and lots by hand. A "
         "form with \"Net proceeds\" checked is still refused."),
    # --- MANUAL: the skills hand-compute these from worksheets; flag loudly ---
    Rule("iso_exercise", "has_iso_exercise", MANUAL,
         "ISO exercise (incentive stock options)", "Form 6251 + CA Schedule P",
         "Federal Form 6251 AMT (incl. the ISO bargain element and the Form "
         "8801 credit carryforward) AND CA Schedule P (540) AMT (fetched CA rates, "
         "the CA Schedule P AMT credit carryforward) "
         "ARE both computed by /tax-compute from the fetched federal.amt and "
         "state.amt tables. Scope: the ISO-exerciser path — other Form 6251 / "
         "Schedule P preference and adjustment items (depreciation, NOL, "
         "private-activity-bond interest, other CA adjustments) are the "
         "caller's to supply and are otherwise not modeled; verify a complex "
         "AMT return in tax software."),
    Rule("education_credits", "has_education_expenses", MANUAL,
         "Education expenses", "Form 8863",
         "AOTC/LLC amounts and MAGI phase-outs ARE computed by /tax-compute "
         "(compute_education_credits, D5) — but from USER-ASSERTED "
         "eligibility facts (half-time, first-4-years, <4 prior AOTC claims, "
         "no felony drug conviction) and expenses YOU net of tax-free "
         "scholarships/employer assistance/529 earnings per Pub 970 — "
         "verify those inputs; the scholarship-election optimization is "
         "not modeled."),
    Rule("dependent_care", "has_dependent_care", MANUAL,
         "Dependent care expenses", "Form 2441",
         "The §21 credit IS computed by /tax-compute (compute_form_2441 — the "
         "AGI applicable percentage, the $3,000/$6,000 expense cap reduced by "
         "W-2 box 10 employer benefits, the earned-income limit with §21(d)(2) "
         "deemed income for a student/disabled spouse, the MFS disallowance, "
         "and the nonrefundable tax limit) GIVEN the attested eligibility "
         "facts. The GATES stay the caller's step 0: a qualifying person "
         "(§21(b)(1) — under 13 or incapable of self-care, relationship/"
         "residency), the work-related-expense rule (both spouses working / "
         "looking / full-time students), and provider name/TIN due diligence — "
         "collected by the /tax-setup interview into `dependent_care_detail`."),
    # The EIC, and the reason it needed a rule of its own: it had NO rule at
    # all. That is the whole of what was distinctive about it — an earlier
    # draft of this comment claimed it was "the one MANUAL rule over a figure
    # the engine computes nothing of", which is false four lines from where
    # `energy_credits` says the 25C/25D credits are hand-computed, and false
    # again for `ev_credit`, `adoption_credit`, `feie`, `foreign_asset_
    # reporting` and `unreported_allocated_tips`. Every one of those is
    # uncomputed and every one of them already had a rule. The EIC did not.
    #
    # `line_27_eic` is an input that defaults to 0 and flows into line 33
    # (`return_builder._LINE_33_TERMS`), so an eligible filer's refund is short
    # by the whole credit with every gate green — and the tie-out, which is
    # what catches a dropped figure, structurally cannot see this one: a credit
    # produces no source document to ledger against. The direction is the one
    # AGENTS.md §4 says degrades with a loud banner.
    #
    # WHAT THE GAP DID HAVE, stated accurately because an earlier draft
    # undercounted it in the direction that flattered this change: five prose
    # sites said the EIC was uncomputed — `result_template.json`'s
    # `_note_line_27`, the tax-and-credits compute reference,
    # `docs/contributing/tax-gotchas.md`, `return_builder`'s module docstring
    # and `projection`'s. What none of them was, is a manifest line: every one
    # is read by an AGENT and none reaches a taxpayer. New York is the
    # exception worth naming — `state-return-ny.md` already told the agent to
    # put each refundable credit's AMOUNT, IT-215 included, on
    # `meta.limitations` verbatim. That is an INSTRUCTION an agent may skip;
    # this rule is a line the gate emits whether anyone remembers or not.
    #
    # NOT a computation, and deliberately not a step towards one. The credit
    # amounts and the disqualified-income cap are indexed (§32(j)), so
    # AGENTS.md §2 forbids naming them. The childless age band is NOT indexed —
    # it is fixed statutory text in §32(c)(1)(A)(ii)(II) — and is still not
    # written here, because it has moved by statute once already and belongs to
    # the covered year's instructions with the rest. The rule names the
    # worksheet and stops.
    #
    # FOUR federal/New York EIC lines, which took a review to get right. New York's IT-215 credit
    # (IT-201 line 65), the IT-209 noncustodial parent EIC (line 66) and the
    # New York City EIC (line 70) are inputs on exactly the same terms as the
    # federal line and land in the same refundable block. An earlier draft
    # carried 65 and 70 and called the set complete, while
    # `tax_computations/state/ny.py` names the pair that gets missed as the
    # noncustodial parent EIC and the City EIC — it had picked up one of two.
    #
    # NOT registered in `answers.REQUIRED_ANSWERS`, and that is a decision
    # rather than an omission. A registered answer BLOCKS (`answers._main`
    # returns 2 while one is owed), and the interview tells the taxpayer to set
    # this flag TRUE on a MAYBE. Coupling a maybe to a hard stop makes the
    # liberal answer the expensive one and teaches people to answer no, which
    # destroys the screen this rule depends on.
    Rule("earned_income_credit", "may_qualify_for_eic", MANUAL,
         "May qualify for the Earned Income Credit (federal, New York, "
         "New York City, New York's noncustodial parent credit, or California family credits)",
         "EIC worksheet, Form 1040 instructions (Form 1040 line 27); "
         "Form IT-215 (NY IT-201 line 65); Form IT-209 (NY IT-201 line 66); "
         "NYC EIC (NY IT-201 line 70); CalEITC (CA Form 540 line 75); "
         "Young Child Tax Credit (CA Form 540 line 76); Foster Youth Tax Credit (CA Form 540 line 77)",
         "The EIC is NOT COMPUTED anywhere in this toolkit — not federally, "
         "not for New York, New York City or California family credits. All listed lines are INPUTS "
         "that default to 0 and flow into the payments total, so leaving them "
         "0 for an eligible filer UNDERSTATES the refund by the whole of the "
         "credit while every gate passes: the tie-out compares source "
         "documents against the return and a credit produces no source "
         "document, so nothing downstream can notice. EACH LINE HAS ITS OWN "
         "FORM AND ITS OWN ELIGIBILITY TEST, and none of them is a copy of "
         "another: take line 27 from the covered year's EIC worksheet in the "
         "Form 1040 instructions (Pub 596), and each New York line from the "
         "form named above beside it. Work the ones that apply. NEVER guess "
         "one — the amounts and the disqualified-income cap are indexed, the "
         "childless age band is statutory and has moved, and all of them "
         "belong to the covered year. If a worksheet comes out at $0, that is "
         "an answer: record it in answers.md so the next reader knows the line "
         "was worked and not skipped."),
    Rule("energy_credits", "has_energy_credits", MANUAL,
         "Residential energy improvements", "Form 5695",
         "The 25C/25D credits are hand-computed. **OBBBA termination (both "
         "keyed to PLACED-IN-SERVICE date, not the payment date):** the §25D "
         "residential clean-energy credit does NOT apply to property PLACED IN "
         "SERVICE after 2025-12-31, and the §25C energy-efficient-home-"
         "improvement credit ends for property placed in service after "
         "2025-12-31 — so an installation placed in service in 2025 still "
         "qualifies even if the final payment falls in 2026. A TY2026+ hand-"
         "worksheet must verify the in-service date, not just the invoice date."),
    Rule("ev_credit", "has_ev_purchase", MANUAL,
         "Electric-vehicle purchase", "Form 8936",
         "The clean-vehicle credit and its income caps are hand-checked. "
         "**OBBBA termination:** the §30D new-clean-vehicle and §25E used-"
         "clean-vehicle credits do NOT apply to vehicles ACQUIRED after "
         "2025-09-30 — a later acquisition gets $0; confirm the acquisition "
         "date before claiming."),
    Rule("gambling", "has_gambling_income", MANUAL,
         "Gambling income", "W-2G / Schedule A / Schedule C",
         "Winnings are reported as income. Nonbusiness losses require itemizing; "
         "business gamblers need a separately reviewed Schedule C worksheet. "
         "Losses and allowable expenses are hand-computed. **OBBBA §70114 (TY2026+):** the "
         "wagering-loss deduction is limited to 90% of losses (still capped at "
         "winnings) — a TY2026+ hand-worksheet done under the old 100% rule "
         "OVERSTATES the loss deduction and understates tax. TY2025 stays 100%."),
    Rule("unreported_allocated_tips", "has_unreported_or_allocated_tips",
         MANUAL, "Unreported or employer-allocated tips (Form 4137)",
         "Form 4137 / Schedule 2 line 5",
         "Unreported cash/charge tips and employer-ALLOCATED tips (W-2 box 8) "
         "run through Form 4137, which adds them to wages on Form 1040 line 1c "
         "AND computes the employee Social Security/Medicare tax on Schedule 2 "
         "line 5 — v1 does NOT compute Form 4137. These tips ARE §224 qualified "
         "tips (Schedule 1-A Part II has a Form 4137 line), but include them in "
         "the qualified-tips deduction ONLY after hand-computing Form 4137 and "
         "adding the paired income + payroll tax; otherwise the deduction is "
         "claimed while the income and FICA are dropped. Verify in tax "
         "software."),
    Rule("feie", "lives_abroad", MANUAL,
         "Living abroad", "Form 2555 (FEIE)",
         "The foreign-earned-income exclusion is not modeled — only the FTC "
         "path is; the FEIE-vs-FTC election is not evaluated."),
    Rule("pfic", "has_foreign_mutual_funds", MANUAL,
         "Foreign mutual funds / ETFs (PFIC)", "Form 8621",
         "Non-US mutual funds and ETFs held in a foreign brokerage are almost "
         "always PFICs (§1291-1298). Form 8621 is filed PER FUND, but the "
         "obligation is EVENT-DRIVEN — DETERMINE per fund whether a trigger "
         "applies rather than assuming every fund needs a form: an excess "
         "distribution, a disposition/sale, a QEF/MTM election or ongoing "
         "QEF/MTM inclusion, or the §1298(f) annual reporting requirement (which "
         "carries de-minimis exceptions — a small holder under the threshold "
         "with no distribution, sale, or election may be EXCEPTED from annual "
         "filing). When a trigger does apply the default §1291 regime is "
         "PUNITIVE — an excess distribution (or gain on sale) is spread across "
         "the holding period, taxed at the top ordinary rate per year, plus a "
         "compounding interest charge; QEF and mark-to-market elections mitigate "
         "but must be made TIMELY (generally the first year). None of this is "
         "modeled here — the flag surfaces the possible obligation so it is not "
         "silently missed alongside the FBAR/8938 reporting on the same "
         "accounts. Compute in specialized software / with a preparer."),
    Rule("foreign_asset_reporting", "has_foreign_financial_account", MANUAL,
         "Foreign financial account / specified foreign assets", "Form 8938 (§6038D)",
         "Specified foreign financial assets held OUTSIDE a US financial "
         "institution (a foreign broker/custodian, or directly-registered "
         "foreign securities) must be reported on Form 8938 once their value "
         "exceeds the filing-status/residence threshold ($50K single / $100K "
         "MFJ at year-end for US residents; higher any-time-in-year and abroad "
         "thresholds). The valuation and form are NOT computed here — this "
         "complements the FBAR (FinCEN 114) prepared by /tax-fbar (whose $10K "
         "aggregate trigger and max-value basis differ). Assets held in a US "
         "brokerage account are generally EXCLUDED from Form 8938."),
    Rule("sanctioned_blocked_asset", "has_sanctioned_blocked_asset", MANUAL,
         "Sanctioned / OFAC-blocked holding",
         "Manual — blocked-income & §165(g) worthlessness analysis",
         "A holding under OFAC sanctions (frozen/blocked — unable to sell or "
         "receive distributions). Two hand-analyses, NOT modeled: (1) do NOT "
         "auto-claim a §165(g) worthlessness loss — a blocked/illiquid security "
         "is NOT 'worthless' without an identifiable event fixing zero value, so "
         "the loss needs documentation, not mere inability to trade (claiming it "
         "on a merely-frozen asset OVERSTATES the loss and understates tax); "
         "(2) blocked-income constructive-receipt / the Rev. Rul. 74-351 "
         "blocked-income deferral is hand-determined. On the credit side: a "
         "SUSPENDED income-tax treaty removes treaty-reduced withholding, and a "
         "§901(j)-listed country denies the foreign tax credit ENTIRELY — verify "
         "the specific country's current status per tax year. (Example: as of "
         "2026-07 Russia is NOT a §901(j) country, so its taxes remain creditable "
         "under general §901, but the US-Russia treaty is SUSPENDED effective "
         "2024-08-16 — Announcement 2024-26.) Any FX gain/loss on foreign-"
         "currency debt is ordinary §988, computed separately by /tax-compute."),
    Rule("foreign_bond_oid_interest", "has_foreign_bond", MANUAL,
         "Directly-held foreign-currency bond (interest / OID)",
         "Schedule B — foreign interest + §1272 OID accrual",
         "A directly-held foreign-currency bond (debt, NOT a PFIC). Two "
         "income items fire EVERY YEAR THE BOND IS HELD, independent of any "
         "sale, and are NOT auto-computed: (1) coupon INTEREST, translated to "
         "USD at each payment-date spot rate → ordinary interest on Schedule B "
         "(foreign-source); (2) if bought at a discount, ANNUAL ORIGINAL ISSUE "
         "DISCOUNT (§1272) accrues into income each year on a constant-yield "
         "basis EVEN WITH NO SALE — omitting it understates income. A DISPOSITION "
         "additionally splits into ordinary §988 exchange gain/loss + capital "
         "market gain/loss (compute_section_988, /tax-compute). Hand-accrue OID "
         "and translate coupons; verify a complex §988/OID position."),
    # --- H1 life-event rules: interview flags → manifest lines. All MANUAL
    # (hand-worked from worksheets / decree documents), and all read False
    # when absent — legacy profiles predating the life-event interview pass
    # are unaffected (deliberately NOT in _REQUIRED_GATE_FLAGS: a missed
    # manual-worksheet line is a manifest omission, not a materially-wrong
    # return the way an unscreened ISO/NYC/kiddie situation is).
    Rule("marriage_year", "married_this_year", MANUAL,
         "Married during the tax year", "Filing-status validation",
         "The Dec-31 rule is hand-validated: married at year-end → MFJ or "
         "MFS, or HoH only via the §7703(b) considered-unmarried or "
         "nonresident-alien-spouse tests (see the /tax-setup Group 1 HoH "
         "block); name/SSA mismatch and W-4 updates are checklist items; "
         "MFS in a community-property state (incl. CA) requires Form 8958 "
         "income splitting, which is NOT modeled."),
    Rule("divorce_year", "divorced_this_year", MANUAL,
         "Divorced or legally separated during the tax year",
         "Filing-status validation / Form 8332",
         "Unmarried at Dec 31 → Single or HoH only; dependency allocation "
         "(Form 8332 release), alimony treatment by decree date, and QDRO "
         "distributions are hand-checked."),
    Rule("home_sale", "sold_primary_home", MANUAL,
         "Sold primary home", "§121 exclusion / Form 8949",
         "The $250K/$500K-MFJ exclusion (2-of-5-year ownership AND use "
         "tests; once every 2 years per §121(b)(3); the $500K joint amount "
         "needs both spouses to meet use, either ownership, neither a "
         "prior-2-year exclusion), the must-report-if-1099-S-received "
         "rule, and any depreciation recapture from home-office/rental "
         "use are hand-computed onto Schedule D/8949."),
    Rule("inheritance", "received_inheritance", MANUAL,
         "Received an inheritance", "Basis step-up / inherited IRA rules",
         "Bequests themselves are not federal income, but income in respect "
         "of a decedent (§691 — inherited traditional IRA/401(k) "
         "distributions, the decedent's unpaid wages) IS taxable with NO "
         "§1014 step-up; basis documentation (date-of-death FMV), "
         "inherited-IRA distribution rules (SECURE 10-year clock), and any "
         "state inheritance tax are hand-checked."),
    Rule("alimony_pre2019", "has_pre2019_alimony", MANUAL,
         "Alimony under a pre-2019 divorce decree", "Schedule 1 alimony lines",
         "Pre-2019-decree alimony is taxable to the recipient and deductible "
         "by the payer (post-TCJA decrees are neither) — but a pre-2019 "
         "decree MODIFIED after 2018 keeps that treatment ONLY if the "
         "modification does not expressly apply the TCJA repeal; the "
         "Schedule 1 entries and the instrument-by-instrument determination "
         "are hand-entered."),
    Rule("alimony_post2018_state", "has_post2018_alimony", MANUAL,
         "Alimony under a post-2018 decree (state nonconformity)",
         "Schedule CA alimony adjustments",
         "Federally neither income nor deduction (TCJA §11051), but CA does "
         "NOT conform: post-2018-decree alimony remains CA-taxable to the "
         "recipient and CA-deductible by the payer via Schedule CA "
         "adjustments, which are NOT auto-computed. Other states' "
         "conformity varies — check the resident state."),
    Rule("adoption_credit", "has_adoption_expenses", MANUAL,
         "Adoption expenses", "Form 8839",
         "The adoption credit, its MAGI phase-out, and the employer-"
         "assistance exclusion interaction are hand-computed — verify in "
         "tax software."),
)

_RULES_BY_ID = {r.rule_id: r for r in RULES}

# Flags whose ABSENCE must fail closed rather than default to False, because a
# missing answer would wave through exactly the situation the gate exists to
# catch. The original gate-era five (an existing ISO/NYC/household/kiddie
# profile was never asked the question) — plus `has_unreported_or_allocated_
# tips` (added with the OBBBA Schedule 1-A pass): unreported / employer-
# allocated tips run through Form 4137 (Form 1040 line 1c wage income + Schedule
# 2 line 5 employee SS/Medicare tax), which the engine does NOT compute, so
# absence-as-False would silently DROP real income + payroll tax — a materially
# wrong return, not merely an omitted hand-worked credit like the other MANUAL
# rules (this is the same reason `has_iso_exercise`, itself a MANUAL rule, is
# required-present) — plus `has_nonresident_source_income` (Codex round-3 P1,
# state-engine-florida PR: reverses an earlier judgment call for consistency
# with this precedent). A full-year resident of a no-tax state (FL) with
# out-of-state W-2/K-1/rental income has a real nonresident filing obligation
# and NO resident-state credit mechanism to offset it — absence-as-False would
# silently wave that obligation through exactly like an unscreened ISO/NYC/
# household situation, so it belongs in the same load-bearing-unsupported-
# situation bucket, not the "defaults to False" one. Each must be EXPLICITLY
# answered (true/false): absence fails closed as the `unanswered_gate_
# questions` blocker, which is deliberately NOT in the rule registry so it
# cannot be acknowledged away — the remedy is answering the yes/no questions,
# not accepting a gap.
# `nyc_resident` STAYS HERE WITH NO RULE BEHIND IT, and that is the point.
# It was an UNSUPPORTED rule until the New York module took on the City block;
# the rule is gone and the flag is now the CITY-TAX SWITCH. Its absence
# therefore matters MORE than it did, not less: a missing answer used to block
# loudly, and would now silently omit New York City's resident tax from a
# return that owes it — under-stating the tax, which is the direction
# AGENTS.md §4 calls a hard error. A flag that DRIVES a computation belongs in
# this list on exactly the same reasoning that put a flag which blocked one
# here.
#
# `moved_abroad_this_year` (AA17) is required for the reason
# `moved_states_this_year` is: every profile written before it answered only
# the narrower state question, and "no, I did not move states" from a taxpayer
# who emigrated in June is not "no, I did not move abroad". Absence has to ask.
#
# `yonkers_resident` and `yonkers_nonresident_earnings` join it for the same
# reason and the same module: the Yonkers surcharge and the Yonkers
# nonresident earnings tax are two DIFFERENT taxes with two different
# populations — you can owe either without the other — so one flag cannot
# stand for both, and absence-as-False drops a real liability either way.
_REQUIRED_GATE_FLAGS = (
    "has_iso_exercise",
    "moved_states_this_year",
    "moved_abroad_this_year",
    "bona_fide_territory_resident",
    "nyc_resident",
    "yonkers_resident",
    "yonkers_nonresident_earnings",
    "statutory_resident_of_another_state",
    "has_household_employees",
    "has_dependent_unearned_income",
    "has_unreported_or_allocated_tips",
    "has_nonresident_source_income",
)
_MIGRATION_RULE_ID = "unanswered_gate_questions"


def _flag_value(flags: dict, name: str) -> bool:
    """Read a boolean flag strictly. Absent/None → False; any present non-bool
    (e.g. the string "false", which is truthy) is a hard error — the same
    trap class as NaN slipping past range checks."""
    value = flags.get(name)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise CapabilityError(
            f"flags.{name} must be true/false, got {type(value).__name__}: {value!r}"
        )
    return value


def _validated_acknowledged(profile: dict, extra: "set[str] | None") -> "set[str]":
    raw = profile.get("acknowledged_limitations")
    if raw is None:
        raw = []
    if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
        raise CapabilityError(
            "acknowledged_limitations must be a list of rule-id strings"
        )
    acknowledged = {x.strip() for x in raw if x.strip()} | (extra or set())
    unknown = acknowledged - set(_RULES_BY_ID) - _DERIVED_ACKNOWLEDGEABLE
    if unknown:
        raise CapabilityError(
            f"unknown acknowledged_limitations ids: {sorted(unknown)}. "
            f"Valid ids: {sorted(_RULES_BY_ID)}. A typo here would silently "
            "fail to unblock anything, so it is rejected."
        )
    return acknowledged


# Explicit negatives for the legacy free-text residency answer. Anything else
# non-empty fails closed — a recorded move must block, and an ambiguous answer
# is resolved by re-answering the new flag, never by guessing.
_LEGACY_NEGATIVES = {"no", "none", "n/a", "na", "false", "0", ""}


def _legacy_residency_change(profile: dict) -> bool:
    """True when a pre-flag profile's prior_year.state_residency_changes
    records a move (or anything that isn't an explicit negative)."""
    prior = profile.get("prior_year")
    if not isinstance(prior, dict):
        return False
    value = prior.get("state_residency_changes")
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _LEGACY_NEGATIVES
    return bool(value)


# Community-property states whose law splits marital income 50/50 between
# MFS returns (Form 8958). AK/SD/TN are ELECTIVE (opt-in community-property
# agreements/trusts) and are not auto-flagged.
_COMMUNITY_PROPERTY_STATES = frozenset(
    {"AZ", "CA", "ID", "LA", "NV", "NM", "TX", "WA", "WI"})
_COMMUNITY_PROPERTY_RULE_ID = "community_property_mfs"
_NY_HOUSEHOLD_RULE_ID = "ny_city_or_yonkers_household_unmodeled"
# Derived findings a user may acknowledge, beside the registry's rule ids.
_DERIVED_ACKNOWLEDGEABLE = frozenset({_NY_HOUSEHOLD_RULE_ID})


def _ny_household_unmodeled(profile: dict) -> "list[str]":
    """The City and Yonkers households the New York engine refuses (AA30), for
    a New York return only: elsewhere the engine never runs, and a Connecticut
    commuter's Yonkers earnings are `nonresident_source_income`'s. The
    conditions are `ny_household_refusals`', beside the functions that refuse.
    """
    from agents.lib.tax_computations.state.ny_nonresident import ny_it203_plan
    try:
        plan = ny_it203_plan(profile)
    except ValueError as exc:
        raise CapabilityError(f"NY filing plan: {exc}") from None
    if plan:
        return []  # Plan rejects Yonkers/mixed spouses and supplies IT360.1.
    if _profile_state(profile) != "NY":
        return []
    # Imported HERE: the engine imports irs_dates, which imports this module.
    from agents.lib.tax_computations.state.ny import ny_household_refusals
    raw = profile.get("flags")
    flags: dict = raw if isinstance(raw, dict) else {}
    if not any(flags.get(flag) is True for flag in (
            "nyc_resident", "yonkers_resident", "yonkers_nonresident_earnings")):
        return []
    try:
        refusals = ny_household_refusals(profile.get("filing_status"), flags)
    except ValueError as exc:
        raise CapabilityError(f"filing_status: {exc}") from None
    return [why for _flag, why in refusals]


# States with NO individual income tax of any kind — the capability note
# declares this affirmatively instead of the "bracket-based ESTIMATE" text.
# TN's Hall income tax (interest & dividends only) was fully repealed
# effective TY2021 (2016 Pub. Ch. 507), so TN now belongs alongside the
# states with no individual income tax of any kind — but ONLY for TY2021+
# (see _tn_hall_repealed below). A pre-2021 TN profile still had a real
# Hall-tax filing obligation; giving it the affirmative "no state return
# exists" note would silently UNDERSTATE that obligation.
# WA and NH are DELIBERATELY excluded: WA levies the capital-gains excise
# (RCW 82.87), a real state-level tax some residents owe, so a blanket
# "no tax" note would be a false promise there. NH's interest & dividends
# tax was repealed effective TY2025 (RSA 77), but v1 has not confirmed
# there's no residual filing obligation (e.g. a transition-year return) for
# every tax year this tool targets — NH keeps the safer estimate note until
# that's verified and its module lands. tax_law's state.type="none" (FL
# template) is the cache-side twin of this set; both are code-reviewed
# constants.
_NO_INCOME_TAX_STATES = frozenset({"AK", "FL", "NV", "SD", "TN", "TX", "WY"})

# TN's Hall income tax was repealed effective TY2021 — see the set comment
# above. Kept as a named constant (not inlined) so the year is easy to spot
# and cite.
_TN_HALL_REPEAL_YEAR = 2021


def _tn_hall_repealed(profile: dict) -> bool:
    """True only when the profile's tax_year is a valid int >= the TN Hall
    repeal year. A missing/None/non-int tax_year does NOT default to
    'repealed' — asserting 'no state return exists' for an unknown year
    would risk silently understating a real pre-2021 Hall-tax filing
    obligation (the bias-direction rule: an understatement of a real
    obligation must degrade, not guess affirmatively)."""
    year = profile.get("tax_year")
    if isinstance(year, bool) or not isinstance(year, int):
        return False
    return year >= _TN_HALL_REPEAL_YEAR


def _profile_state(profile: dict) -> "str | None":
    """Validated 2-letter USPS state code from address.state, or None when
    the address/state is unset. Malformed values hard-error (same gate
    _state_note applies — this is the shared extractor)."""
    address = profile.get("address")
    if address is None:
        return None
    if not isinstance(address, dict):
        raise CapabilityError("address must be an object")
    state = address.get("state")
    if state is None:
        return None
    if not isinstance(state, str) or len(state.strip()) != 2 or not state.strip().isalpha():
        raise CapabilityError(
            f"address.state must be a 2-letter USPS code, got {state!r}"
        )
    code = state.strip().upper()
    if code not in _USPS_STATES:
        raise CapabilityError(
            f"address.state {code!r} is not a USPS state/territory code — "
            "fix the profile (a typo like 'CN' for 'CA' would otherwise get a "
            "confident estimate note for a nonexistent state)."
        )
    return code


# --- Residency -------------------------------------------------------------
#
# New York's statutory-residency test, and the ONLY state whose test this engine
# applies. The figures are sourced and dated in
# docs/contributing/residency-guardrails.md and pinned by test; two of the three
# are law and the third is NOT — "substantially all of the taxable year" is
# 20 NYCRR 105.20(a)(2), and the number of months it means is Audit Division
# POLICY, which moved from eleven to ten beginning with tax year 2022 while the
# Department's published bulletin still says eleven. Audit policy is what an
# auditor applies, so ten is what this uses, and the pre-2022 figure is kept
# because an amended return for an earlier year is a real thing.
#
# Applied HERE rather than left to the review prose, which is the point of
# collecting counts instead of a "were you there a lot?" boolean: a threshold
# the taxpayer has to apply in their head is one the profile cannot check.
_NY_STATUTORY_DAYS = 184
_NY_ABODE_MONTHS_FROM_2022 = 10
_NY_ABODE_MONTHS_BEFORE_2022 = 11
_NY_TEN_MONTH_RULE_YEAR = 2022

_STATUTORY_RESIDENCY_FLAG = "statutory_resident_of_another_state"
_TERRITORY_FLAG = "bona_fide_territory_resident"

_MAX_DAYS_IN_A_YEAR = 366
_MAX_MONTHS_IN_A_YEAR = 12


def _residency_counts(profile: dict, key: str, ceiling: int) -> "dict[str, float]":
    """One `residency` count map, validated at the parse boundary.

    Every failure mode here is the same one AGENTS.md §8 names: a value that is
    type-valid and out of domain reads as a confident answer. A misspelled key
    makes the counts for the state that matters look ABSENT, which is the
    dangerous direction — the reviewer finds nothing meeting a test and a
    flag-False return sails through, silently wrong by a whole resident tax.
    NaN is rejected explicitly: `json.loads` accepts it, it passes
    `isinstance(x, (int, float))`, and every comparison with it is False.
    """
    residency = profile.get("residency")
    if residency is None:
        return {}
    if not isinstance(residency, dict):
        raise CapabilityError("residency must be an object")
    raw = residency.get(key)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CapabilityError(f"residency.{key} must be an object keyed by state")
    out: "dict[str, float]" = {}
    for state, value in raw.items():
        if not isinstance(state, str) or state.strip().upper() not in _USPS_STATES:
            raise CapabilityError(
                f"residency.{key} key {state!r} is not a USPS state code — a "
                "misspelled key hides the counts for the state that matters"
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CapabilityError(
                f"residency.{key}[{state!r}] must be a number, got {value!r}"
            )
        if not math.isfinite(value) or not 0 <= value <= ceiling:
            raise CapabilityError(
                f"residency.{key}[{state!r}] must be between 0 and {ceiling}, "
                f"got {value!r}"
            )
        out[state.strip().upper()] = float(value)
    return out


def _residency_domicile(profile: dict) -> "str | None":
    residency = profile.get("residency")
    if not isinstance(residency, dict):
        return None
    state = residency.get("domicile_state")
    if state is None:
        return None
    if not isinstance(state, str) or state.strip().upper() not in _USPS_STATES:
        raise CapabilityError(
            f"residency.domicile_state must be a USPS state code, got {state!r}"
        )
    return state.strip().upper()


def _ny_statutory_resident(profile: dict) -> bool:
    """Does the profile's own counts meet New York's test, for a NON-NY filer?

    BOTH prongs, because either alone is not the test: days in hotels is not
    statutory residency and an empty apartment is not either. Scoped to a filer
    whose address is elsewhere, since a New York filer is already computing a
    New York return and this rule is about a SECOND resident state.
    """
    if _profile_state(profile) == "NY":
        return False
    days = _residency_counts(profile, "days_present_by_state",
                             _MAX_DAYS_IN_A_YEAR).get("NY", 0.0)
    months = _residency_counts(profile, "abode_months_by_state",
                               _MAX_MONTHS_IN_A_YEAR).get("NY", 0.0)
    year = profile.get("tax_year")
    threshold = _NY_ABODE_MONTHS_BEFORE_2022
    if isinstance(year, int) and not isinstance(year, bool) \
            and year >= _NY_TEN_MONTH_RULE_YEAR:
        threshold = _NY_ABODE_MONTHS_FROM_2022
    return days >= _NY_STATUTORY_DAYS and months > threshold


def _state_note(profile: dict) -> str:
    code = _profile_state(profile)
    if code is None:
        return "State: none configured — no state return computed."
    if code in _TERRITORIES:
        return (f"Territory ({code}): not a state, and no state return is "
                "computed. The territory's own return and its relationship "
                "to the federal one are out of scope (territory_resident).")
    from agents.lib.tax_computations.state.ny_nonresident import ny_it203_plan
    try:
        ny_plan = ny_it203_plan(profile)
    except ValueError as exc:
        raise CapabilityError(f"NY filing plan: {exc}") from None
    if ny_plan:
        return ("State (NY): Form IT-203 computed in depth for TY2025 from reviewed source allocations, "
                "the all-source tax table/recapture and IT-196 deduction, and the four-decimal income percentage. "
                "NYC part-year IT-360.1 uses resident-period income and prorated deductions. Credit and modification "
                "worksheets are inputs. Other-state returns/credits, mixed spouses, MFS, IT-230 special ratios and IT-203 Yonkers tax remain manual. "
                "Ordinary state projections and IT-203 withholding forecasts are not modeled.")
    if code == "CA":
        return ("State: California Form 540 arithmetic and Schedule P are computed by a checked builder "
                "for the reviewed form year, from separately worked Schedule CA, deduction, credit and tax components. "
                "HSA adjustments, exemption-credit math, FTB3510 PartII and direct-owner PTET credits have engine support; "
                "other situations are gated or marked NOT COMPUTED. FTB5805's regular-method penalty "
                "on line113 is rebuilt from reviewed tax and payment facts; special methods require "
                "a confirmed external worksheet or an explicit eligible FTB-billing election.")
    if code == "NY":
        # THE DEPTH NOTE IS WRITTEN LAST, and the spec says so for a reason:
        # it is the sentence a taxpayer and a reviewer read to decide how far
        # to trust the return, so it may only be written once the depth it
        # claims exists. Every clause below names something that computes.
        #
        # What it does NOT claim is as load-bearing as what it does. IT-196
        # lines 1-45 are an INPUT, line 45's other taxes (the IT-230 lump-sum
        # tax among them) are inputs, each refundable credit's AMOUNT is an input, line
        # 59's sales-or-use tax is asked rather than computed, and IT-203 is
        # modeled only through the declared IT203 variant. Those are on `meta.limitations` where the return
        # itself can carry them; this note names the ones a reader is most
        # likely to assume away — the credits and the part-year return.
        return (
            "State (NY): Form IT-201 computed in depth for a FULL-YEAR "
            "RESIDENT — the §601 rate schedules with the tax-benefit "
            "recapture, the IT-225 modifications and New York AGI, the IT-196"
            " itemized deduction with both §615 reductions, the household "
            "credit, New York City taxable income, the New York City resident"
            " tax on line 47a and the City household credit, the Yonkers "
            "resident surcharge and nonresident earnings tax (lines 55 and 56)"
            ", and the MCTMT. The AMOUNT of each"
            " refundable credit (IT-213, IT-216, IT-215, IT-214, IT-272) is "
            "taken as an input from its own worksheet, not computed; so are "
            "IT-196 lines 1-45 and IT-201 line 45's other taxes. Line 59's "
            "sales or use tax is ASKED, never assumed to be zero. Part-year "
            "and nonresident returns require a separate declared IT-203 plan.")
    if code == "WA":
        return ("State (WA): capital-gains excise computed in depth for TY2025 from confirmed LT classifications and worksheets. "
                "No individual wage income tax. Short-term losses do not reduce the base. Separate spouses/RDPs, special federal gains and midyear domicile changes refuse. "
                "Separate My DOR filing with federal copy; no required quarterly estimates. Federal extension alone does not extend WA filing.")
    if code == "NC":
        return (
            "State (NC): Form D-400 computed in depth for a full-year resident "
            "under the source-verified TY2025 D-401; other form years refuse. "
            "Includes Schedule S lines 1 and 17-21, independent NC Schedule A, "
            "the child deduction and D-400TC Part 1 from supplied inputs. "
            "Use tax is ASKED; D-422 interest and penalties are inputs. "
            "Business adjustments, recovery deductions, NOLs, Taxed-PTE owner "
            "items, other Schedule S deductions and other credits are gated. "
            "Schedule PN part-year/nonresident returns and dependent filers "
            "are not modeled. NC-40 farmer and annualized-income rules and "
            "NC legal-holiday dates need manual confirmation.")
    no_tax = code in _NO_INCOME_TAX_STATES and (
        code != "TN" or _tn_hall_repealed(profile)
    )
    if no_tax:
        # The Schedule A line-5a sales-tax election is only AUTOMATED for FL
        # (compute_sales_tax_deduction + the FL Optional Sales Tax Table
        # lookup, per tax-compute.md step 2). Every other no-tax state
        # (TX/TN/WY/AK/NV/SD) explicitly SKIPS that computation — reusing
        # FL's generic "handled on the federal side" phrase there implied
        # the election was taken care of when it was actually never run,
        # which let a review pass a return with neither a computed amount
        # nor a warning (Codex round-9 P2). Non-FL states get an explicit
        # MANUAL-computation instruction instead, distinct wording from
        # FL's automated note so /tax-review can tell the two apart.
        # The property/use-tax parenthetical named FL's specific forms
        # (DR-405/DR-15MO) for EVERY no-tax state, but this branch is
        # shared by AK/NV/SD/TX/WY/post-repeal-TN too — an ordinary TX or
        # NV taxpayer's manifest note is misleading when it cites Florida
        # forms they have no nexus with (Codex round-17 P2). Scope the
        # named forms to code == "FL"; every other no-tax state gets a
        # state-NEUTRAL caveat instead of an invented per-state form list
        # (that's WA-epic/future-state-module territory, not this fix).
        if code == "FL":
            # The use-tax form differs by dealer registration (round-16 P2:
            # registered FL dealers use DR-15, everyone else DR-15MO) — name
            # both here so this static manifest line never contradicts the
            # registration-tailored florida_state.use_tax_note in the same
            # PDF (Codex round-23 P2).
            property_use_tax_note = (
                "property/use-tax filings such as FL DR-405 / DR-15MO "
                "(or DR-15 for registered dealers) may still apply"
            )
            sales_tax_note = (
                "Verified affirmatively (Fla. Const. Art. VII, §5); the "
                "Schedule A sales-tax election is computed automatically "
                "(florida_state.sales_tax_deduction, FL Optional Sales Tax "
                "Table)."
            )
        else:
            property_use_tax_note = (
                "this state's own property/use-tax filings, if any, are "
                "not modeled here"
            )
            if code == "AK":
                # Codex round-23 P2: Alaska levies NO statewide sales tax
                # but IS on the IRS Optional LOCAL Sales Tax Table list
                # (see the compute_sales_tax_deduction docstring's
                # worksheet citation) — the generic state-table wording
                # would have an AK filer look up a statewide table that
                # doesn't exist and omit the locality amount, understating
                # Schedule A line 5a.
                sales_tax_note = (
                    "Verified affirmatively; Alaska levies no statewide "
                    "sales tax, but many AK localities do — the Schedule A "
                    "sales-tax election must be computed MANUALLY from the "
                    "IRS Optional LOCAL Sales Tax Tables for the taxpayer's "
                    "locality (AK is on the IRS local-table list in the "
                    "Schedule A instructions) — v1 automates the sales-tax "
                    "table lookup for FL only, so nothing computes this "
                    "automatically here."
                )
            elif code == "TN":
                # Codex round-26 P2 (AK's twin): post-repeal TN is ALSO on
                # the IRS Optional LOCAL Sales Tax Table list (see the
                # compute_sales_tax_deduction docstring's worksheet
                # citation) — but unlike AK, TN has a statewide sales tax
                # too, so the worksheet path is state table PLUS local
                # table; the generic state-table-only wording would omit
                # the locality amount and understate line 5a. (Only
                # reachable for TY2021+ — pre-repeal TN takes the
                # Hall-specific note below, before this branch.)
                sales_tax_note = (
                    "Verified affirmatively; the Schedule A sales-tax "
                    "election must be computed MANUALLY for TN from BOTH "
                    "the IRS Optional State Sales Tax Tables AND the IRS "
                    "Optional LOCAL Sales Tax Tables for the taxpayer's "
                    "locality (TN is on the IRS local-table list in the "
                    "Schedule A instructions — a state-table-only "
                    "computation omits the locality amount) — v1 automates "
                    "the sales-tax table lookup for FL only, so nothing "
                    "computes this automatically here."
                )
            else:
                sales_tax_note = (
                    "Verified affirmatively; the Schedule A sales-tax election "
                    "must be computed MANUALLY from the IRS Optional State Sales "
                    "Tax Tables for this state — v1 automates the sales-tax "
                    "table lookup for FL only, so nothing computes this "
                    "automatically here."
                )
        return (f"State ({code}): no individual income tax — no individual "
                f"income-tax return exists or is required ({property_use_tax_note}). "
                f"{sales_tax_note}")
    if code == "TN":
        # Reaching here means _tn_hall_repealed was False: a pre-2021 (or
        # unresolvable) tax_year. The Hall income tax applied ONLY to
        # interest and dividends — the generic "bracket-based ESTIMATE"
        # text below would invite estimating tax on ALL income (wages/SE/
        # capital gains), fabricating a liability the Hall regime never
        # imposed. This is the note-level twin of the round-23 consumer
        # guards (projection.project_state_tax and
        # loss_harvest._state_marginal_rate refuse TN bracket math).
        return ("State (TN): this tax year predates the Hall-tax repeal "
                "(effective TY2021) — the TN Hall income tax applied ONLY "
                "to interest and dividends and is NOT modeled. Do NOT "
                "bracket-estimate TN tax over all income; if taxable "
                "interest/dividend income exceeded the filing threshold, "
                "prepare the pre-2021 TN Hall return (Form INC 250) "
                "manually.")
    return (f"State ({code}): bracket-based ESTIMATE only — state-specific "
            "additions/subtractions, credits, and local taxes are not modeled. "
            "CA and NY are the deeply-supported states; contributions "
            "welcome.")


def evaluate(profile: dict, extra_acknowledged: "set[str] | None" = None) -> CapabilityReport:
    """Evaluate the capability matrix against a taxpayer profile.

    Raises CapabilityError on malformed input (non-dict flags, non-bool flag
    values, unknown acknowledged ids, malformed state) — a corrupted profile
    must not silently pass the gate.
    """
    if not isinstance(profile, dict):
        raise CapabilityError("taxpayer profile must be a JSON object")
    flags = profile.get("flags")
    if flags is None:
        flags = {}
    if not isinstance(flags, dict):
        raise CapabilityError("flags must be an object (not a scalar/list)")

    acknowledged_ids = _validated_acknowledged(profile, extra_acknowledged)

    blocking, acknowledged, manual = [], [], []

    def _place(finding: Finding):
        if finding.level == UNSUPPORTED and not finding.acknowledged:
            blocking.append(finding)
        elif finding.level == UNSUPPORTED:
            acknowledged.append(finding)
        else:
            manual.append(finding)

    # The recorded COUNTS decide New York's test, not the taxpayer's answer to
    # it. Collecting numbers instead of a boolean buys nothing if the threshold
    # is then applied in somebody's head — and the dangerous direction is a
    # taxpayer who answered the counts honestly, got the arithmetic wrong, and
    # left the flag False: the return then computes one resident state and is
    # silently wrong by the whole of New York's. This is the ONLY state whose
    # test is mechanical enough to apply; every other one is a hand-off, which
    # is why the flag stays as the taxpayer's own assertion beside it.
    # Validated for its own sake as well as for the test below: a
    # `domicile_state` nobody reads is still a field a later reader will trust,
    # and "Nu York" has to fail where it is written, not where it is used.
    _residency_domicile(profile)
    # Flags the profile's own facts can set whatever the taxpayer answered.
    # A territory DOMICILE counts as much as a territory address: the address
    # is where the mail goes, and a Puerto Rico resident with a Florida box
    # is the case the flag alone leaves to a legal term of art.
    derived = {_STATUTORY_RESIDENCY_FLAG: _ny_statutory_resident(profile),
               "has_gambling_income": _flag_value(flags, "has_w2g"),
               _TERRITORY_FLAG: bool(
                   {_profile_state(profile), _residency_domicile(profile)}
                   & _TERRITORIES)}

    from agents.lib.tax_computations.state.ny_nonresident import ny_it203_plan
    try:
        ny_plan = ny_it203_plan(profile)
    except ValueError as exc:
        raise CapabilityError(f"NY filing plan: {exc}") from None
    state = "NY" if ny_plan else _profile_state(profile)
    rental_plan = profile.get("rental_return")
    prior = profile.get("prior_year", {})
    if not isinstance(prior, dict):
        raise CapabilityError("prior_year must be an object")
    rental_opening = prior.get("rental_carryforward")
    derived["has_rental_income"] = rental_plan is not None or rental_opening is not None
    rental_computed_scope = False
    if rental_plan is not None:
        from agents.lib.tax_computations.federal.rentals import validate_rental_scope, rental_has_ca_history
        try:
            validate_rental_scope(rental_plan)
            rental_computed_scope = (profile.get("tax_year") == rental_plan["tax_year"]
                                     and not ny_plan and state in _NO_INCOME_TAX_STATES | {"CA", "WA"}
                                     and (state == "CA" or not rental_has_ca_history(rental_opening)))
        except ValueError:
            pass  # The existing named unsupported rule remains, without enabling math.
        properties = rental_plan.get("properties") if isinstance(rental_plan, dict) else None
        if not isinstance(properties, list) or not properties:
            rental_computed_scope = False
        for prop in properties if isinstance(properties, list) else []:
            code = (prop.get("address") or {}).get("state") if isinstance(prop, dict) else None
            if code not in _USPS_STATES - _TERRITORIES:
                raise CapabilityError("rental property source state must be a real U.S. state code")
            if code != state and code not in _NO_INCOME_TAX_STATES | {"WA"}:
                derived["has_nonresident_source_income"] = True
    if state == "CA":
        carried = prior.get("ca_amt_credit_carryforward", 0)
        if (isinstance(carried, bool) or not isinstance(carried, (int, float))
                or not math.isfinite(carried) or carried < 0):
            raise CapabilityError("prior_year.ca_amt_credit_carryforward must be finite and non-negative")
        derived["ca_has_amt_credit"] = carried > 0
    applicable_rules = tuple(rule for rule in RULES if rule.state is None or rule.state == state)
    for rule in applicable_rules:
        if (ny_plan and ny_plan["fully_covered"] and rule.rule_id in
                {"part_year_residency", "nonresident_source_income"}):
            _flag_value(flags, rule.flag)  # Still reject malformed flag values.
            continue
        # _flag_value FIRST, so a non-bool answer still hard-errors.
        if not (_flag_value(flags, rule.flag) or derived.get(rule.flag)):
            continue
        form, note = rule.form, rule.note
        level = rule.level
        if rule.rule_id == "rental_income" and rental_computed_scope:
            level = MANUAL
            note = ("Reviewed TY2025 direct U.S. residential ScheduleE, building/structural GDS depreciation "
                    "and passive-loss limits are computed from property books with separate CA balances. "
                    "Basis/at-risk, other loss limits, QBI, NIIT, federal/CA AMT and income-dependent "
                    "worksheets require input-bound review. Equipment, ADS, bonus/section179, method changes, "
                    "personal/short-term/self-rental, royalties, dispositions and special passive cases remain outside scope.")
        if state == "CA" and rule.rule_id == "dependent_care":
            form += "; California FTB 3506 / Form 540 line 40"
            note += " California FTB 3506 is NOT computed; prepare its separate covered-year eligibility and credit worksheet."
        if state == "CA" and rule.rule_id == "earned_income_credit":
            form = "Form 1040 line 27; California Form 540 lines 75-77 / FTB 3514"
            note = ("Federal EIC, CalEITC, Young Child Tax Credit and Foster Youth Tax Credit "
                    "are NOT computed. Work each covered-year eligibility worksheet separately; "
                    "a federal no does not rule out California's credits, including ITIN filers "
                    "and the young-child credit with zero earned income.")
        if ny_plan and rule.rule_id == "earned_income_credit":
            form = "Form 1040 line 27; IT-203 line 43 / IT-203-ATT refundable EIC and NYC EIC"
            note = ("EIC eligibility and source shares are not computed. Supply reviewed federal, "
                    "NY pre-allocation state EIC and IT-203-ATT refundable-credit worksheets. "
                    "Omitting a credit understates the refund or overstates the tax owed.")
        _place(Finding(rule.rule_id, level, rule.situation, form,
                       note, rule.rule_id in acknowledged_ids))

    # Migration guard (fail closed): the gate-era flags must be explicitly
    # answered true/false. Both an ABSENT key (legacy profile) and a present
    # NULL (partially migrated profile) mean the taxpayer never answered the
    # load-bearing question — neither is 'no'. flags.get() maps both to None.
    missing_flags = [f for f in _REQUIRED_GATE_FLAGS if flags.get(f) is None]
    missing_flags.extend(rule.flag for rule in applicable_rules
                         if rule.state is not None and (rule.level == UNSUPPORTED or rule.rule_id in {"ca_amt_credit", "ca_ptet_credit"})
                         and flags.get(rule.flag) is None)
    if missing_flags:
        _place(Finding(
            _MIGRATION_RULE_ID, UNSUPPORTED,
            "Screening questions unanswered",
            "capability-gate migration",
            "Ask the taxpayer the missing questions and set each flag "
            "explicitly true/false in taxpayer_profile.json flags: "
            f"{', '.join(missing_flags)}. Absence is not treated as 'no' — an "
            "existing ISO/NYC/household-employee/kiddie-tax/move-abroad/"
            "territory situation would "
            "otherwise slip through the gate. This blocker cannot be "
            "acknowledged; answer the questions instead.",
            acknowledged=False))

    # Legacy migration guard (fail closed): profiles created before
    # flags.moved_states_this_year existed carry the interview answer in
    # prior_year.state_residency_changes. It is consulted ONLY when the new
    # flag key is ABSENT — a present bool means the user answered the new
    # question and is authoritative. Any legacy value that isn't an explicit
    # negative blocks as part_year_residency until the user re-answers.
    if "moved_states_this_year" not in flags and _legacy_residency_change(profile):
        rule = _RULES_BY_ID["part_year_residency"]
        _place(Finding(
            rule.rule_id, rule.level,
            rule.situation + " (recorded in legacy prior_year.state_residency_changes "
                             "— re-answer by setting flags.moved_states_this_year "
                             "explicitly true/false)",
            rule.form, rule.note, rule.rule_id in acknowledged_ids))

    # PFIC taxable-event fail-closed (Codex P1): a profile that records foreign
    # funds (has_foreign_mutual_funds) but PREDATES has_pfic_taxable_event reads
    # the new flag as absent → False, so a holder who took a distribution or sold
    # would slip through with only the manual PFIC note, dodging the
    # pfic_taxable_event BLOCK and understating §1291 tax. When the fund flag is
    # true and the event flag is unanswered (absent OR null), require the answer.
    # A NEW profile carries has_pfic_taxable_event=false (template), so this fires
    # only for pre-flag profiles — backward-compat is preserved for buy-and-hold.
    # Not acknowledgeable: the remedy is answering the yes/no, not accepting a gap.
    if _flag_value(flags, "has_foreign_mutual_funds") and \
            flags.get("has_pfic_taxable_event") is None:
        _place(Finding(
            "unanswered_pfic_event", UNSUPPORTED,
            "Holds foreign funds but the PFIC taxable-event question is unanswered",
            "Form 8621 (§1291) screening",
            "This profile records foreign mutual funds/ETFs "
            "(has_foreign_mutual_funds) but predates has_pfic_taxable_event. Set "
            "it explicitly true/false: did any foreign-domiciled fund pay a "
            "distribution OR get sold this year? Absence is not treated as 'no' — "
            "a PFIC taxable event would otherwise slip through with only the "
            "manual note and understate the punitive §1291 tax. This blocker "
            "cannot be acknowledged; answer the question.",
            acknowledged=False))

    # The same fail-closed answer for a crypto profile whose option question
    # is unanswered (AA59): one that predates the flag, or one built from the
    # template, which ships it null because /tax-parse-crypto sets has_crypto
    # after the interview. The import no longer refuses box 3a's "Gross", so
    # the question is all that catches an option's sale.
    if _flag_value(flags, "has_crypto") and \
            flags.get("has_digital_asset_option_exercise") is None:
        _place(Finding(
            "unanswered_digital_asset_option", UNSUPPORTED,
            "Has crypto but the option-exercise question is unanswered",
            "Form 8949 screening",
            "This profile records digital assets (has_crypto) but leaves "
            "has_digital_asset_option_exercise unanswered (absent or null). Set "
            "it explicitly true/false: was "
            "any digital asset sold, exchanged or spent this year because an option, held or "
            "written, was exercised, or from a lot such an exercise bought in "
            "any year? Absence is not treated as 'no' — the "
            "premium would otherwise be left out of the row. This blocker "
            "cannot be acknowledged; answer the question.",
            acknowledged=False))

    if _flag_value(flags, "has_crypto") and flags.get("has_crypto_specific_identification") is None:
        _place(Finding(
            "unanswered_crypto_lot_selection", UNSUPPORTED,
            "Has crypto but the lot-selection question is unanswered",
            "Form 8949 lot selection",
            "has_crypto_specific_identification is unanswered (absent or null). Ask about "
            "specific identification at every broker and wallet, including standing instructions "
            "and relevant prior history. Record true or false; never infer FIFO from box 8 "
            "or an absent broker basis. This question cannot be acknowledged away.",
            acknowledged=False))

    unmodeled = _ny_household_unmodeled(profile)
    if unmodeled:
        _place(Finding(
            _NY_HOUSEHOLD_RULE_ID, UNSUPPORTED,
            "A New York City or Yonkers household the engine does not compute",
            "IT-360.1 / IT-201 lines 50, 51, 57 / Form Y-203",
            "; ".join(unmodeled) + ". /tax-compute refuses the City or "
            "Yonkers tax for each of these, so the gate says so before the "
            "work. The tax is still owed: acknowledging this means it is "
            "prepared by hand on the form named, recorded in answers.md, and "
            "the tie-out stops requiring the engine's line for that household. "
            "Never answer a residency question untruthfully to get past it: "
            "a whole year or a second resident spouse OVER-states the tax, and "
            "dropping the flag drops it.",
            _NY_HOUSEHOLD_RULE_ID in acknowledged_ids))

    # Form 8958 community-property income splitting applies to EVERY MFS
    # filer domiciled in a community-property state — not just marriage-year
    # filers (Codex P2 on PR #36). Status+state-driven, no flag: both inputs
    # are already interviewed, so keying this on married_this_year alone
    # missed every long-married MFS couple.
    if (str(profile.get("filing_status") or "").strip().upper() == "MFS"
            and _profile_state(profile) in _COMMUNITY_PROPERTY_STATES):
        _place(Finding(
            _COMMUNITY_PROPERTY_RULE_ID, MANUAL,
            "MFS in a community-property state",
            "Form 8958 income splitting",
            "Community income (wages and most income from property acquired "
            "during the marriage) must be allocated between the spouses' "
            "separate returns per state law — the Form 8958 allocation is "
            "NOT modeled; hand-prepare it and adjust BOTH returns. (AK/SD/TN "
            "community-property elections are opt-in and not auto-flagged.)",
            acknowledged=False))

    state_note = _state_note(profile)

    limitations = [state_note]
    for f in acknowledged:
        limitations.append(
            f"NOT COMPUTED (user acknowledged): {f.situation} — {f.form}. {f.note}"
        )
    for f in manual:
        limitations.append(f"MANUAL WORKSHEET: {f.situation} — {f.form}. {f.note}")

    return CapabilityReport(
        blocking=tuple(blocking), acknowledged=tuple(acknowledged),
        manual=tuple(manual), state_note=state_note,
        limitations=tuple(limitations),
    )


def render_markdown(report: CapabilityReport) -> str:
    lines = ["# Capability gate", ""]
    if report.blocking:
        lines += ["## ⛔ BLOCKED — situations this toolkit cannot compute", ""]
        for f in report.blocking:
            lines.append(f"- **{f.situation}** ({f.form}, id `{f.rule_id}`): {f.note}")
        lines += [
            "",
            "Producing a return that silently omits these would understate tax "
            "or drop mandatory forms. Options:",
            "",
            "1. Prepare this return in tax software / with a professional, and "
            "use this toolkit as a checker (`/tax-verify`) and planner.",
            "2. Proceed WITHOUT these items, understanding the return is "
            "incomplete: record your explicit acceptance in answers.md and add "
            "the rule id(s) to `acknowledged_limitations` in "
            "taxpayer_profile.json — they will be carried on the return's "
            "limitations manifest.",
            "",
        ]
    else:
        lines += ["No blocking situations — compute may proceed.", ""]
    if report.acknowledged:
        lines += ["## ⚠ Acknowledged gaps (proceeding at user request)", ""]
        lines += [f"- {f.situation} — {f.form}" for f in report.acknowledged]
        lines.append("")
    if report.manual:
        lines += ["## Manual-worksheet items (hand-computed by the skills)", ""]
        lines += [f"- {f.situation} — {f.form}" for f in report.manual]
        lines.append("")
    lines += ["## State scope", "", f"- {report.state_note}", ""]
    lines += ["## Limitations manifest (→ result.json meta.limitations)", ""]
    lines += [f"- {line}" for line in report.limitations]
    lines.append("")
    return "\n".join(lines)


def _report_to_dict(report: CapabilityReport) -> dict:
    return {
        "blocking": [vars(f) for f in report.blocking],
        "acknowledged": [vars(f) for f in report.acknowledged],
        "manual": [vars(f) for f in report.manual],
        "state_note": report.state_note,
        "limitations": list(report.limitations),
    }


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.capabilities")
    add_workspace_argument(parser)
    parser.add_argument("--acknowledge", default=None,
                        help="comma-separated rule ids to acknowledge for this run "
                             "(in addition to profile acknowledged_limitations)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args(argv)

    try:
        workspace = resolve_workspace(args.workspace).resolve()
        # This gate WRITES the validation receipt (`record_after_verdict`
        # below), and `receipt.record` MERGES: it reads the file, adds its
        # entry and writes the whole thing back. Two gates recording
        # concurrently lose one of the two entries, and a lost entry makes
        # `verify` report that gate as never having run. The direction is safe
        # — a workspace reads as less validated than it is — but the
        # declaration in `workspace.LOCKED_CLIS` is about who writes SHARED
        # state, and this writes it.
        hold_workspace(workspace, "capabilities")
        profile_path = _validate_subpath(
            (workspace / "taxpayer_profile.json").resolve(), workspace)
        if not profile_path.exists():
            raise CapabilityError(
                f"{redact(profile_path)} does not exist — run /tax-setup first.")
        try:
            # Read ONCE, and record the digest of these bytes: re-reading the
            # file to hash it evaluated one profile and recorded another, so a
            # flag flipped in that window produced a PASS receipt for a
            # situation this gate would have blocked.
            raw = profile_path.read_bytes()
            profile = json.loads(raw)
        except json.JSONDecodeError as e:
            raise CapabilityError(f"taxpayer_profile.json is malformed: {redact(e)}")
        # Captured before the evaluation, for the reason receipt.record's
        # docstring gives: the verdict is bound to the profile this gate read.
        extra = None
        if args.acknowledge:
            extra = {s.strip() for s in args.acknowledge.split(",") if s.strip()}
        gate_inputs = receipt.gate_inputs(
            workspace, "capabilities", contents={"taxpayer_profile.json": raw})
        report = evaluate(profile, extra_acknowledged=extra)
    except (WorkspaceError, CapabilityError, RolloverError, receipt.ReceiptError, OSError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2))
    else:
        print(render_markdown(report))

    exit_code = 2 if report.blocking else 0
    # The receipt is written AFTER the verdict is printed and cannot lower its
    # exit code: this gate's job is to block an out-of-scope return, and a
    # permissions fault must not be able to mask that. A BLOCKED gate is
    # recorded as blocked — /tax-generate refuses a filing package on it either
    # way, and the reason it prints is the difference between a gate that failed
    # and one that never ran (V8).
    return receipt.record_after_verdict(
        workspace, "capabilities",
        receipt.PASS if not report.blocking else "BLOCKED",
        exit_code, inputs=gate_inputs)


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
