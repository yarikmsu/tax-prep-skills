#!/usr/bin/env python3
"""Capability matrix / scope guard for /tax-compute (C1) + limitations manifest (C2).

Declarative map of taxpayer-profile situations → engine support level. The
pipeline's honesty invariant is "never silently understate": when the interview
records a situation the engine cannot compute (ISO/AMT, rental Schedule E,
Form 8962 reconciliation, part-year residency, NYC, kiddie tax, Schedule H),
/tax-compute must HARD-STOP with the named form and options rather than produce
a plausible-looking wrong return. Situations the skills handle with manual
worksheets (8863, 2441, 5695/8936, gambling, FEIE-vs-FTC) are non-blocking
limitations that land in result.json meta.limitations (the C2 manifest,
printed on page 1 of the PDF by /tax-generate).

Pure compute + markdown renderer + CLI, following projection.py / yoy_diff.py.
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
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_subpath,
)


class CapabilityError(Exception):
    """Raised when the capability gate cannot safely evaluate a profile."""


UNSUPPORTED = "unsupported"
MANUAL = "manual"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    flag: str  # taxpayer_profile.json flags.<name>, must be a bool when present
    level: str  # UNSUPPORTED (blocks compute) | MANUAL (manifest note only)
    situation: str
    form: str
    note: str

    def __post_init__(self):
        if self.level not in (UNSUPPORTED, MANUAL):
            raise CapabilityError(f"invalid rule level {self.level!r}")


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
    Rule("nyc_resident", "nyc_resident", UNSUPPORTED,
         "New York City residency", "NYC resident tax (Form IT-201)",
         "NYC's resident income tax (~3.1-3.9%) is not computed."),
    Rule("household_employees", "has_household_employees", UNSUPPORTED,
         "Household employees (nanny tax)", "Schedule H",
         "Household-employment payroll taxes are not modeled."),
    Rule("kiddie_tax", "has_dependent_unearned_income", UNSUPPORTED,
         "Dependent with unearned income over the kiddie-tax threshold",
         "Form 8615",
         "Tax at the parents' rate on a child's unearned income is not "
         "modeled (and the dependent may have their own filing requirement)."),
    # --- MANUAL: the skills hand-compute these from worksheets; flag loudly ---
    Rule("iso_exercise", "has_iso_exercise", MANUAL,
         "ISO exercise (incentive stock options)", "CA Schedule P (CA AMT)",
         "Federal Form 6251 AMT (incl. the ISO bargain element and the Form "
         "8801 credit carryforward) IS computed by /tax-compute — but CA has "
         "its own AMT (Schedule P, 7% flat, different exemption) which is "
         "NOT computed; a CA ISO exerciser must verify the CA side in tax "
         "software."),
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
         "The credit and its earned-income limitation are hand-computed."),
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
         "Gambling income", "W-2G / Schedule A",
         "Winnings are reported as income; losses are deductible only when "
         "itemizing and are hand-computed. **OBBBA §70114 (TY2026+):** the "
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
# required-present). Each must be EXPLICITLY answered (true/false): absence
# fails closed as the `unanswered_gate_questions` blocker, which is deliberately
# NOT in the rule registry so it cannot be acknowledged away — the remedy is
# answering the yes/no questions, not accepting a gap.
_REQUIRED_GATE_FLAGS = (
    "has_iso_exercise",
    "moved_states_this_year",
    "nyc_resident",
    "has_household_employees",
    "has_dependent_unearned_income",
    "has_unreported_or_allocated_tips",
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
    unknown = acknowledged - set(_RULES_BY_ID)
    if unknown:
        raise CapabilityError(
            f"unknown acknowledged_limitations ids: {sorted(unknown)}. "
            f"Valid ids: {sorted(_RULES_BY_ID)}. A typo here would silently "
            "fail to unblock anything, so it is rejected."
        )
    return acknowledged


# All USPS state/territory codes (50 states + DC + the five inhabited
# territories). A 2-letter-shape check alone lets "ZZ" (corrupt) or "CN"
# (typo for CA) through to a confident bracket-estimate note — this gate is
# the last validation before meta.limitations, so membership is enforced.
_USPS_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY DC PR VI GU AS MP".split()
)


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


def _state_note(profile: dict) -> str:
    code = _profile_state(profile)
    if code is None:
        return "State: none configured — no state return computed."
    if code == "CA":
        return "State: California Form 540 computed in depth (Schedule CA, exemption credits, MHST)."
    return (f"State ({code}): bracket-based ESTIMATE only — state-specific "
            "additions/subtractions, credits, and local taxes are not modeled. "
            "CA is the only deeply-supported state; contributions welcome.")


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

    for rule in RULES:
        if not _flag_value(flags, rule.flag):
            continue
        _place(Finding(rule.rule_id, rule.level, rule.situation, rule.form,
                       rule.note, rule.rule_id in acknowledged_ids))

    # Migration guard (fail closed): the gate-era flags must be explicitly
    # answered true/false. Both an ABSENT key (legacy profile) and a present
    # NULL (partially migrated profile) mean the taxpayer never answered the
    # load-bearing question — neither is 'no'. flags.get() maps both to None.
    missing_flags = [f for f in _REQUIRED_GATE_FLAGS if flags.get(f) is None]
    if missing_flags:
        _place(Finding(
            _MIGRATION_RULE_ID, UNSUPPORTED,
            "Profile predates the capability gate — screening questions unanswered",
            "capability-gate migration",
            "Ask the taxpayer the missing questions and set each flag "
            "explicitly true/false in taxpayer_profile.json flags: "
            f"{', '.join(missing_flags)}. Absence is not treated as 'no' — an "
            "existing ISO/NYC/household-employee/kiddie-tax situation would "
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
    parser.add_argument("--workspace", required=True, help="taxpayer workspace directory")
    parser.add_argument("--acknowledge", default=None,
                        help="comma-separated rule ids to acknowledge for this run "
                             "(in addition to profile acknowledged_limitations)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args(argv)

    try:
        workspace = Path(args.workspace).resolve()
        profile_path = _validate_subpath(
            (workspace / "taxpayer_profile.json").resolve(), workspace)
        if not profile_path.exists():
            raise CapabilityError(
                f"{profile_path} does not exist — run /tax-setup first.")
        try:
            profile = json.loads(profile_path.read_text())
        except json.JSONDecodeError as e:
            raise CapabilityError(f"taxpayer_profile.json is malformed: {e}")
        extra = None
        if args.acknowledge:
            extra = {s.strip() for s in args.acknowledge.split(",") if s.strip()}
        report = evaluate(profile, extra_acknowledged=extra)
    except (CapabilityError, RolloverError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2))
    else:
        print(render_markdown(report))
    return 2 if report.blocking else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
