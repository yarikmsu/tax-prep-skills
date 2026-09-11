#!/usr/bin/env python3
"""Retirement contribution dashboard for /tax-retirement (backlog #11).

Read-only mid-year planning. Reads YTD contribution figures from
projection_inputs.json + age flags from taxpayer_profile.json + statutory
limits from tax_law_<year>.json, and reports remaining capacity per account
type plus marginal tax savings per remaining dollar.

Account types modeled (MVP):
- 401(k) employee deferral (pre-tax + Roth aggregate against same limit)
- Traditional IRA (deductible / phased / non-deductible per MAGI + plan coverage)
- Roth IRA (allowed / phased / disallowed per MAGI)
- HSA (self-only / family + age-55 catch-up; CA/NJ non-conformity flagged)
- Mega Backdoor (after-tax 401(k) space = total_annual_limit - non-catch-up
  employee deferral (§414(v) catch-up excluded from the §415(c) cap) -
  employer match; INDEPENDENT of after-tax YTD. Emitted when
  plan_allows_after_tax_401k OR after_tax_ytd > 0 — the latter surfaces an
  over-contribution against a plan that doesn't allow after-tax dollars.)

Marginal-saved semantics:
- pre_tax: federal + state marginal × remaining
- hsa_conforming: federal + state marginal × remaining
- hsa_non_conforming: federal marginal × remaining (CA/NJ state-taxable)
- roth: $0 current-year savings (post-tax dollars in)
- after_tax: $0 current-year savings (tax-free growth not modeled in v1)

Architectural lineage:
- pure-compute lib (no result.json mutation) modeled on projection.py /
  withholding.py
- reuses rollover._validate_subpath + _validate_year_arg for boundary
  checks (same pattern as every write-derived skill)
- strict parse-boundary helpers (no `or default` short-circuit ahead of
  isinstance) — see lessons from PR #19's review rounds
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_year_arg,
)
from agents.lib.capabilities import _USPS_STATES  # noqa: E402
from agents.lib.secure_fs import redact, year_or_shape  # noqa: E402
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    resolve_under_workspace,
    resolve_workspace,
)


class RetirementError(Exception):
    """Raised when /tax-retirement cannot safely proceed."""


# States that do NOT conform to federal HSA treatment (HSA contributions
# remain state-taxable). Each entry is per-state-citation-verified — adding
# or removing a state requires citing the specific tax law / DOR guidance.
#
#   CA — does NOT conform. R&TC §17215.4 limits HSA contribution deduction
#        to itemized-deduction context only; CA does not allow the above-
#        the-line deduction available federally. Contributions and earnings
#        are state-taxable.
#   NJ — does NOT conform. NJ has not adopted IRC §223; HSA contributions
#        and earnings are state-taxable.
#
# Previously included AL on the basis of general guidance, but Ala. Code
# §40-18-15.6(b) (effective 2018-01-01) allows AL residents to deduct HSA
# contributions aligned with federal §223 limits. AL DOES conform —
# removed per Codex review.
_HSA_NON_CONFORMING_STATES = {"CA", "NJ"}

# State retirement-contribution conformity is PER-ACCOUNT: 401(k)/403(b)
# elective-deferral treatment and Traditional IRA deduction treatment differ
# by state, so they need SEPARATE sets (a single set conflated them — Codex
# PR #20).
#
# States that tax pre-tax 401(k)/403(b) ELECTIVE DEFERRALS (no state
# deduction → $0 state savings on the 401(k) row). PA is the established
# case: the PA PIT taxes 401(k)/403(b) deferrals when contributed (PA DOR —
# "not excludable from the employee's Pennsylvania income") and exempts
# qualified distributions (contribution-taxed, not deduction-taxed — the
# inverse of federal).
_RETIREMENT_DEFERRAL_NONCONFORMING_STATES = {"PA"}
# States that do NOT allow a state deduction for TRADITIONAL IRA
# contributions (no state savings on the Traditional IRA row). PA (taxes all
# retirement contributions), NJ (no IRA deduction — tracks basis so already-
# taxed contributions aren't re-taxed at distribution), MA (no IRA
# deduction — same basis tracking). NJ/MA allow the 401(k) deferral
# exclusion, so they are NOT in the 401(k) set above — the per-account
# distinction is the whole point. v1 models these three; other states'
# narrower nuances are caveated, since the dashboard's state savings is a
# simplified federal-piggyback estimate.
_IRA_DEDUCTION_NONCONFORMING_STATES = {"PA", "NJ", "MA"}

# NEW YORK IS IN NONE OF THE THREE SETS, AND THAT IS NOT A CONFORMITY FINDING.
# Absence from these sets makes the dashboard CLAIM state savings, which is the
# over-stating-a-BENEFIT direction AGENTS.md §4 calls a hard error — so it is
# not a safe default the way an absent figure usually is. New York's treatment
# of HSA contributions, 401(k)/403(b) elective deferrals and the Traditional
# IRA deduction has NOT been researched here; the sources to read are Form
# IT-225-I's addition codes and the IT-201 instructions for lines 20-24. The
# Z-epic spec carries this as an open row rather than a guess.
#
# One New York item IS established, from the state module's own fetched block,
# and it is not one of these rows: PUBLIC-EMPLOYEE 414(h) retirement
# contributions and IRC §125 cafeteria-plan amounts for NYS and NYC public
# employees are ADDED BACK on IT-201 (line 21 and an IT-225 code). For those
# taxpayers a contribution that is pre-tax federally does NOT reduce New York
# tax. The 414(h) pension pick-up is not a row this dashboard models at all —
# it is neither an elective deferral nor an IRA — so nothing here over-states
# it today; it is recorded so the next person adding a New York row knows the
# add-back exists before they price one.

# MFS IRA phase-out window per IRS Pub 590-A. Statutory constant —
# unchanged since 1997 (Roth IRA introduction), not inflation-indexed.
# Applied to:
#   - Traditional IRA deduction when MFS + primary covered by employer plan
#   - Roth IRA contribution when MFS + lived with spouse during the year
# An MFS taxpayer who lived APART from their spouse all year uses the
# Single phase-out window for Roth (and is still limited to MFS-covered
# $0-$10k for Traditional if covered).
_MFS_IRA_PHASEOUT_START = 0
_MFS_IRA_PHASEOUT_END = 10_000

# SECURE 2.0 §603 (codified at IRC §414(v)(7)(A)): participants whose
# prior-year FICA wages exceed this threshold MUST make 401(k) catch-up
# contributions as designated Roth (no current-year deduction). Statute
# specifies $145,000 indexed to 2023; projected to $150,000 for TY2026
# per IRS guidance. Effective TY2026 per Notice 2023-62 (originally
# 2024, delayed). v1 uses a fixed default; tax_law can override via
# federal.retirement_401k.secure_2_0_high_earner_threshold for future
# years' indexed values.
_SECURE_2_0_HIGH_EARNER_THRESHOLD_DEFAULT = 150_000.0
# §603 mandatory-Roth-catchup designation for high earners — effective
# TY2026 (delayed from 2024 by Notice 2023-62).
_SECURE_2_0_EFFECTIVE_YEAR = 2026
# DISTINCT effective year: the SECURE 2.0 §109 age-60-63 "super" catch-up
# AMOUNT applies to taxable years beginning after Dec 31, 2024 (TY2025+),
# NOT delayed like §603. Before TY2025 a 60-63 participant gets only the
# standard 50+ catch-up, and the catch_up_60_63 field is absent from a
# pre-2025 tax-law cache — so the super amount must be year-gated to avoid
# a hard-error (or overstatement) on older-year dashboards (Codex PR #20).
_SUPER_CATCHUP_EFFECTIVE_YEAR = 2025

# Canonical filing statuses recognized throughout the dashboard (stored
# upper-cased for case-insensitive membership). The CLI reads
# profile.filing_status verbatim from JSON, so a hand-entered value like
# "Married Filing Jointly" or "" must be REJECTED — otherwise it silently
# falls through to the Single-status phase-out windows AND skips the
# load-bearing MFS lived-apart hard-error. None still defaults to Single
# (the documented benign case).
_VALID_FILING_STATUSES = frozenset({"SINGLE", "MFJ", "MFS", "HOH", "QSS"})

AccountKind = Literal[
    "pre_tax", "roth", "hsa_conforming", "hsa_non_conforming", "after_tax"
]


# ---------------------------------------------------------------------------
# Strict parse-boundary helpers (PR #19 lessons)
# ---------------------------------------------------------------------------

def _strict_bool(d: dict, key: str, *, default: bool = False,
                 label: str = "retirement_ytd") -> bool:
    """Optional boolean — strict; truthy strings rejected.

    `label` is the dotted-prefix used in error messages so the helper can
    serve both `retirement_ytd.<key>` (default) and `flags.<key>` callers.
    """
    if key not in d or d[key] is None:
        return default
    v = d[key]
    if not isinstance(v, bool):
        raise RetirementError(
            f"{label}.{key} must be a JSON boolean, got {v!r}"
        )
    return v


def _strict_num(d: dict, key: str, *, default: float = 0.0,
                allow_negative: bool = False) -> float:
    """Optional numeric — strict; bool rejected (Python quirk)."""
    if key not in d or d[key] is None:
        return default
    v = d[key]
    if isinstance(v, bool):
        raise RetirementError(
            f"retirement_ytd.{key} must be a number, got bool {v!r}"
        )
    if not isinstance(v, (int, float)):
        raise RetirementError(
            f"retirement_ytd.{key} must be a number, got {type(v).__name__}"
        )
    if not math.isfinite(v):
        # json.loads accepts NaN/Infinity by default; they pass the
        # isinstance check and slip past `< 0` (NaN/Inf comparisons are
        # False), then poison capacity/savings math. Reject at the boundary.
        raise RetirementError(
            f"retirement_ytd.{key} must be a finite number, got {v}"
        )
    if not allow_negative and v < 0:
        raise RetirementError(
            f"retirement_ytd.{key} must be non-negative, got {v}"
        )
    return float(v)


def _optional_non_negative_num(d: dict, key: str) -> "float | None":
    """Optional retirement_ytd numeric that distinguishes ABSENT (None) from
    a provided value, rejecting bool / non-numeric / negative inputs.

    Differs from `_strict_num` (which folds absent → a numeric default): the
    callers here branch on whether the field was supplied at all — e.g.
    missing `taxable_compensation` triggers a warning note, while a provided
    $0 caps IRA capacity at $0.
    """
    if key not in d or d[key] is None:
        return None
    v = d[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise RetirementError(
            f"retirement_ytd.{key} must be a non-negative number or null, "
            f"got {type(v).__name__}"
        )
    if not math.isfinite(v):
        raise RetirementError(
            f"retirement_ytd.{key} must be a finite number, got {v}"
        )
    if v < 0:
        raise RetirementError(
            f"retirement_ytd.{key} must be non-negative, got {v}"
        )
    return float(v)


def _strict_str(d: dict, key: str, *, allowed: "tuple[str, ...] | None" = None,
                default: str = "") -> str:
    if key not in d or d[key] is None:
        return default
    v = d[key]
    if not isinstance(v, str):
        raise RetirementError(
            f"retirement_ytd.{key} must be a string, got {type(v).__name__}"
        )
    if allowed is not None and v not in allowed:
        raise RetirementError(
            f"retirement_ytd.{key} must be one of {list(allowed)}, got {v!r}"
        )
    return v


def _require_tax_law_amount(law: dict, *path: str) -> float:
    """Dig into a tax_law dict, raise RetirementError if any segment is
    missing or None — half-fetched tax_law would otherwise silently
    report $0 limits."""
    cur = law
    for seg in path:
        if not isinstance(cur, dict) or seg not in cur or cur[seg] is None:
            raise RetirementError(
                f"tax_law missing required value at {'.'.join(path)}"
            )
        cur = cur[seg]
    if not isinstance(cur, (int, float)) or isinstance(cur, bool):
        raise RetirementError(
            f"tax_law value at {'.'.join(path)} must be numeric, "
            f"got {type(cur).__name__}"
        )
    if not math.isfinite(cur):
        raise RetirementError(
            f"tax_law value at {'.'.join(path)} must be a finite number, "
            f"got {cur}"
        )
    return float(cur)


# ---------------------------------------------------------------------------
# Limit computations
# ---------------------------------------------------------------------------

def compute_401k_employee_limit(tax_law: dict, *, age_50_plus: bool,
                                age_60_63: bool) -> float:
    """401(k) employee elective deferral limit including any applicable
    catch-up. SECURE 2.0 60-63 super catch-up SUPERSEDES (does not stack
    with) the standard 50+ catch-up when both apply."""
    base = _require_tax_law_amount(
        tax_law, "federal", "retirement_401k", "elective_deferral"
    )
    return base + compute_401k_catchup_amount(
        tax_law, age_50_plus=age_50_plus, age_60_63=age_60_63
    )


def _get_secure_2_0_high_earner_threshold(
    tax_law: dict, *, effective_year: "int | None" = None
) -> float:
    """SECURE 2.0 §603 mandatory-Roth-catchup threshold. The built-in
    default ($150k) is the TY2026 indexed value (projected from IRC
    §414(v)(7)(A)'s $145k indexed-to-2023 base). The threshold INDEXES
    annually, so the default is valid ONLY for TY2026 — later years must
    supply the year-specific indexed amount via
    federal.retirement_401k.secure_2_0_high_earner_threshold. For a
    post-2026 year without that override the cache is stale/incomplete:
    falling back to the TY2026 $150k would wrongly classify earners between
    $150k and the real (higher) indexed threshold as high earners and split
    them into a mandatory-Roth catch-up row (Codex PR #20). Pre-2026 years
    don't use the threshold (the §603 split is gated off), so the default is
    harmless there."""
    federal = tax_law.get("federal") if isinstance(tax_law, dict) else None
    retirement = (
        federal.get("retirement_401k") if isinstance(federal, dict) else None
    )
    override = (
        retirement.get("secure_2_0_high_earner_threshold")
        if isinstance(retirement, dict) else None
    )
    if override is None:
        if effective_year is not None and effective_year > _SECURE_2_0_EFFECTIVE_YEAR:
            raise RetirementError(
                "tax_law.federal.retirement_401k."
                "secure_2_0_high_earner_threshold is required for "
                f"TY{effective_year}: the built-in ${_SECURE_2_0_HIGH_EARNER_THRESHOLD_DEFAULT:,.0f} "
                "default is the TY2026 value and the §603 threshold indexes "
                "annually — re-fetch the year's law via /tax-setup so "
                "$150k-and-up earners aren't misclassified into mandatory-Roth "
                "catch-up."
            )
        return _SECURE_2_0_HIGH_EARNER_THRESHOLD_DEFAULT
    if isinstance(override, bool) or not isinstance(override, (int, float)):
        raise RetirementError(
            f"tax_law.federal.retirement_401k.secure_2_0_high_earner_threshold "
            f"must be numeric, got {type(override).__name__}"
        )
    return float(override)


def compute_401k_catchup_amount(tax_law: dict, *, age_50_plus: bool,
                                age_60_63: bool) -> float:
    """Just the CATCH-UP portion of the 401(k) elective deferral limit
    (i.e. the limit ABOVE the base elective deferral). Returns 0 when
    not age-eligible. SECURE 2.0 60-63 super catch-up SUPERSEDES (does
    not stack with) the standard 50+ catch-up. Separated from the
    combined-limit helper to support row-splitting for SECURE 2.0 §603
    mandatory Roth catch-up (high-earner case, PR #20 round-13)."""
    if age_60_63:
        return _require_tax_law_amount(
            tax_law, "federal", "retirement_401k", "catch_up_60_63"
        )
    if age_50_plus:
        return _require_tax_law_amount(
            tax_law, "federal", "retirement_401k", "catch_up_50_plus"
        )
    return 0.0


def compute_ira_limit(tax_law: dict, *, age_50_plus: bool) -> float:
    """IRA contribution limit (Traditional + Roth aggregate against this
    limit) including 50+ catch-up if applicable."""
    base = _require_tax_law_amount(
        tax_law, "federal", "ira_limits", "contribution_limit"
    )
    if age_50_plus:
        catchup = _require_tax_law_amount(
            tax_law, "federal", "ira_limits", "catch_up_50_plus"
        )
        return base + catchup
    return base


def compute_hsa_limit(tax_law: dict, *, coverage: str,
                      age_55_plus: bool) -> float:
    """HSA contribution limit per coverage tier. Returns 0 for no
    HSA-eligible HDHP coverage."""
    if coverage == "none":
        return 0.0
    if coverage not in ("self_only", "family"):
        raise RetirementError(
            f"hsa_coverage must be 'none' | 'self_only' | 'family', "
            f"got {coverage!r}"
        )
    base = _require_tax_law_amount(
        tax_law, "federal", "hsa_limits", coverage
    )
    if age_55_plus:
        catchup = _require_tax_law_amount(
            tax_law, "federal", "hsa_limits", "catch_up_55_plus"
        )
        return base + catchup
    return base


# ---------------------------------------------------------------------------
# Phase-out computations
# ---------------------------------------------------------------------------

def _binary_status_key(filing_status: str) -> str:
    return "mfj" if (filing_status or "").upper() in {"MFJ", "QSS"} else "single"


def _phaseout_fraction(magi: float, start: float, end: float) -> float:
    """Standard IRS phase-out — returns the FRACTION of full benefit
    allowed at the given MAGI (1.0 fully allowed → 0.0 fully phased
    out, linear in between). Filing-status-agnostic: this helper only
    does the linear interpolation. The MFS window selection (the
    $0-$10,000 MFS-with-spouse window vs the Single-equivalent window
    for MFS-lived-apart) is chosen by the callers
    (compute_ira_traditional_deductible_fraction /
    compute_roth_contribution_fraction) before calling this.

    Returns the fraction at FULL precision — do NOT round here. IRS Pub
    590-A rounds the reduced DOLLAR limit (ceil to $10, $200 floor) AFTER
    multiplying the limit by this fraction, via _apply_irs_phaseout_rounding.
    Rounding the fraction first injects error that, combined with the
    ceil-to-$10 step, can shift the dollar cap by a whole $10 increment —
    e.g. a $7,000 Single Roth limit at MAGI $150,064 is $6,970.13 → $6,980
    exact, but a 4-dp fraction (0.9957) gives $6,969.90 → $6,970, a $10
    understatement that can falsely flag a small contribution as excess
    (Codex PR #20)."""
    if magi <= start:
        return 1.0
    if magi >= end:
        return 0.0
    return (end - magi) / (end - start)


def _apply_irs_phaseout_rounding(raw_dollar_limit: float) -> float:
    """Pub 590-A Worksheet 1-2 steps 5-6: discretize a phased IRA dollar
    limit per IRS rules. Used for both the Traditional IRA deductible
    cap and the Roth IRA direct-contribution cap.

    Step 5: round the reduced amount UP to the next multiple of $10.
    Step 6: if the result is GREATER THAN $0 AND LESS THAN $200, set
            it to $200 (statutory floor for partial-eligibility cases).

    A raw amount of 0 (taxpayer at or above the phase-out upper bound)
    is left at 0 — the $200 floor is for partial phase-out, not full
    phase-out. Without this rounding, a taxpayer 1 dollar inside a
    $10,000 phase-out window sees their cap as $0.70 instead of the
    statutory $200 — a 285× understatement (Codex round-10 finding)."""
    if raw_dollar_limit <= 0:
        return 0.0
    rounded = math.ceil(raw_dollar_limit / 10.0) * 10
    if rounded < 200:
        return 200.0
    return float(rounded)


def compute_ira_traditional_deductible_fraction(
    tax_law: dict, *, modified_agi: float, filing_status: str,
    covered_by_employer_plan: bool,
    spouse_covered_by_employer_plan: bool = False,
    lived_apart_from_spouse_all_year: bool = False,
) -> float:
    """Fraction of Traditional IRA contribution that's deductible.

    Per IRS Pub 590-A Tables 1-2 and 1-3, the case split is SEVEN-way:
    1. Primary covered + MFS-with-spouse → $0-$10k phase-out
    2. Primary covered + MFS-lived-apart-all-year → treated as Single
       (Pub 590-A footnote: "If you file separately and didn't live with
       your spouse at any time during the year, your IRA deduction is
       determined under the Single filing status.")
    3. Primary covered + MFJ/QSS → mfj_covered
    4. Primary covered + Single/HoH → single_covered
    5. Primary NOT covered + MFJ (NOT QSS) + spouse covered →
       mfj_spouse_not_covered (wider window). QSS is excluded: a
       qualifying surviving spouse has a DECEASED spouse who cannot be
       covered by a current-year employer plan, so a stale
       has_employer_retirement_plan_spouse flag must not route them here
       (they fall through to case 7). Mirrors §219(c)'s MFJ-only gating.
    6. Primary NOT covered + MFS-with-spouse + spouse covered → $0-$10k
       phase-out (Pub 590-A Table 1-3 row: "You are not covered, your
       spouse is covered" + "MFS lived with spouse"). Same window as
       case 1, different table — the spouse's coverage is what triggers
       the workplace-plan rules for an MFS-with-spouse taxpayer.
    7. Neither covered (any filing status, including MFS lived apart with
       spouse covered → treated as Single → no phase-out) → unlimited (1.0)
    """
    status_key = (filing_status or "").upper()
    is_mfj = status_key in {"MFJ", "QSS"}
    is_mfs = status_key == "MFS"
    if covered_by_employer_plan:
        if is_mfs and not lived_apart_from_spouse_all_year:
            # Case 1: MFS + lived with spouse → $0-$10k window
            return _phaseout_fraction(
                modified_agi, _MFS_IRA_PHASEOUT_START, _MFS_IRA_PHASEOUT_END
            )
        if is_mfs and lived_apart_from_spouse_all_year:
            # Case 2: MFS + lived apart all year → Single
            key = "single_covered"
        else:
            # Cases 3 & 4: MFJ → mfj_covered; Single/HoH → single_covered
            key = "mfj_covered" if is_mfj else "single_covered"
    elif status_key == "MFJ" and spouse_covered_by_employer_plan:
        # Case 5: spouse-only covered → wider phase-out window. MFJ ONLY
        # (NOT the MFJ/QSS `is_mfj` helper): QSS has a deceased spouse who
        # cannot be covered by a current-year employer plan, so a stale
        # has_employer_retirement_plan_spouse flag must NOT route a QSS
        # into this phase-out — they fall through to case 7 (unlimited).
        # The COVERED branch above keeps QSS on mfj_covered per Pub 590-A's
        # "MFJ or QSS" grouping (that's the taxpayer's OWN window); this is
        # a spouse-property branch, which requires a LIVING spouse = MFJ
        # (mirrors the §219(c) round-18 gating). Codex PR #20.
        key = "mfj_spouse_not_covered"
    elif (
        is_mfs
        and spouse_covered_by_employer_plan
        and not lived_apart_from_spouse_all_year
    ):
        # Case 6: MFS + lived with spouse + ONLY spouse covered → same
        # $0-$10k window as case 1 (Pub 590-A Table 1-3). The spouse's
        # coverage triggers the workplace-plan rules for the MFS-with-
        # spouse taxpayer; lived-apart MFS falls through to case 7.
        return _phaseout_fraction(
            modified_agi, _MFS_IRA_PHASEOUT_START, _MFS_IRA_PHASEOUT_END
        )
    else:
        # Case 7: neither covered (any status) → unlimited
        return 1.0
    # Walk the full path through _require_tax_law_amount so a half-fetched
    # cache (missing the ira_limits block, the phase-out map, or the per-
    # status sub-key) raises a clean RetirementError instead of a raw
    # KeyError/TypeError that escapes the CLI's error handler. Consistent
    # with every other tax_law read in this module.
    start = _require_tax_law_amount(
        tax_law, "federal", "ira_limits",
        "traditional_deduction_phaseout", key, "start")
    end = _require_tax_law_amount(
        tax_law, "federal", "ira_limits",
        "traditional_deduction_phaseout", key, "end")
    return _phaseout_fraction(modified_agi, start, end)


def compute_roth_contribution_fraction(
    tax_law: dict, *, modified_agi: float, filing_status: str,
    lived_apart_from_spouse_all_year: bool = False,
) -> float:
    """Fraction of Roth IRA contribution allowed per MAGI.

    Per IRS Pub 590-A Table 2-1, the case split is:
    1. MFS + lived with spouse during year → $0-$10,000 phase-out
    2. MFS + lived apart from spouse all year → use Single phase-out
       (Pub 590-A footnote)
    3. MFJ / QSS → mfj phase-out
    4. Single / HoH → single phase-out

    Above the phase-out end, direct Roth is disallowed (the user can
    still do a backdoor Roth — handled by /tax-roth-convert).
    """
    status_key = (filing_status or "").upper()
    if status_key == "MFS" and not lived_apart_from_spouse_all_year:
        # Case 1: MFS-with-spouse → $0-$10k window (statutory constant)
        return _phaseout_fraction(
            modified_agi, _MFS_IRA_PHASEOUT_START, _MFS_IRA_PHASEOUT_END
        )
    # Cases 2-4: MFS-lived-apart uses Single; MFJ uses mfj; Single/HoH
    # uses single. (_binary_status_key maps MFS→single, which IS correct
    # for the lived-apart subcase.)
    key = _binary_status_key(filing_status)
    # Full-path guard (see compute_ira_traditional_deductible_fraction):
    # a missing roth_income_phaseout map or sub-key raises a clean
    # RetirementError, not a raw KeyError.
    start = _require_tax_law_amount(
        tax_law, "federal", "ira_limits",
        "roth_income_phaseout", key, "start")
    end = _require_tax_law_amount(
        tax_law, "federal", "ira_limits",
        "roth_income_phaseout", key, "end")
    return _phaseout_fraction(modified_agi, start, end)


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AccountStatus:
    """One row in the dashboard.

    `shared_pool_id` tags accounts that share a single regulatory limit
    (e.g. Traditional + Roth IRA share the IRS annual IRA limit). When
    set, `pool_remaining` is the budget available to the WHOLE pool and
    per-account `remaining` is capped by it. `DashboardReport` totals
    dedupe by pool so a $0/$0 IRA case doesn't double-count.

    `marginal_eligible_dollars` overrides `remaining` for the marginal
    tax savings calc. Required for Traditional IRA in phase-out: the
    IRS rule (Pub 590-A Worksheet 1-2) caps the DEDUCTIBLE DOLLARS at
    `contribution_limit × phaseout_fraction`, so the savings on each
    next dollar is at the full marginal rate (not a scaled rate) until
    the cap is reached, then $0. When None, falls back to `remaining`.

    `pool_over_contributed` is the pool-level excess (sum of pool YTD
    minus pool cap) shared by all members of the pool. Distinct from
    `over_contributed` which is bucket-vs-bucket-cap. For the Traditional
    IRA bucket the bucket cap IS the pool cap, so its bucket excess
    coincides with the pool excess; for the Roth bucket the cap is the
    MAGI-reduced limit (can sit below the pool cap), so its bucket excess
    differs. The pool excess is what flags a corrective-distribution
    requirement when both buckets together exceed the shared limit.
    """
    name: str
    ytd_contributed: float
    annual_limit: float
    marginal_fed_rate: float
    marginal_state_rate: float
    kind: AccountKind
    note: str = ""
    shared_pool_id: "str | None" = None
    pool_remaining: "float | None" = None
    marginal_eligible_dollars: "float | None" = None
    pool_over_contributed: float = 0.0

    @property
    def remaining(self) -> float:
        bucket_remaining = max(0.0, self.annual_limit - self.ytd_contributed)
        if self.pool_remaining is not None:
            return round(min(bucket_remaining, self.pool_remaining), 2)
        return round(bucket_remaining, 2)

    @property
    def over_contributed(self) -> float:
        return round(max(0.0, self.ytd_contributed - self.annual_limit), 2)

    @property
    def pct_used(self) -> float:
        if self.annual_limit <= 0:
            return 0.0
        return round(min(1.0, self.ytd_contributed / self.annual_limit), 4)

    @property
    def _saved_basis(self) -> float:
        if self.marginal_eligible_dollars is not None:
            return max(0.0, self.marginal_eligible_dollars)
        return self.remaining

    @property
    def marginal_fed_saved(self) -> float:
        if self.kind in ("roth", "after_tax"):
            return 0.0
        return round(self._saved_basis * self.marginal_fed_rate, 2)

    @property
    def marginal_state_saved(self) -> float:
        if self.kind in ("roth", "after_tax", "hsa_non_conforming"):
            return 0.0
        return round(self._saved_basis * self.marginal_state_rate, 2)


@dataclass(frozen=True)
class DashboardReport:
    year: int
    filing_status: str
    state: str
    marginal_fed_rate: float
    marginal_state_rate: float
    modified_agi: float
    accounts: "list[AccountStatus]"

    def _dedupe_by_pool(self, attr: str) -> float:
        """Sum `attr` across accounts, counting each shared pool ONCE.

        For shared-pool accounts, take the MAX `attr` across the pool's
        members — this represents the user's best path (e.g. put all
        remaining IRA capacity into the bucket with the highest deductible
        marginal saved). Non-pooled accounts add normally.
        """
        seen_pools: dict[str, float] = {}
        total = 0.0
        for a in self.accounts:
            v = getattr(a, attr)
            if a.shared_pool_id is None:
                total += v
            else:
                prev = seen_pools.get(a.shared_pool_id, 0.0)
                if v > prev:
                    seen_pools[a.shared_pool_id] = v
        total += sum(seen_pools.values())
        return round(total, 2)

    @property
    def total_remaining(self) -> float:
        return self._dedupe_by_pool("remaining")

    @property
    def total_potential_fed_saved(self) -> float:
        return self._dedupe_by_pool("marginal_fed_saved")

    @property
    def total_potential_state_saved(self) -> float:
        return self._dedupe_by_pool("marginal_state_saved")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _join_note(note: str, clause: str) -> str:
    """Append `clause` to `note`, space-separated, with no leading space when
    `note` is empty. No-op when `clause` is empty."""
    if not clause:
        return note
    return (note + " " + clause).strip() if note else clause


def _validate_rate(rate: float, *, label: str) -> float:
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise RetirementError(
            f"{label} must be a number, got {type(rate).__name__}"
        )
    if not math.isfinite(rate):
        # NaN slips past the [0,1] range check (NaN comparisons are False).
        raise RetirementError(
            f"{label} must be a finite number, got {rate}"
        )
    if rate < 0 or rate > 1:
        raise RetirementError(
            f"{label} must be in [0.0, 1.0], got {rate}"
        )
    return float(rate)


def build_dashboard(profile: dict, projection_inputs: dict, tax_law: dict,
                    *, marginal_fed_rate: float, marginal_state_rate: float,
                    modified_agi: float,
                    tax_year: "int | None" = None) -> DashboardReport:
    """Assemble the dashboard from a profile + projection_inputs + tax_law.

    All numeric inputs validated strict at the parse boundary. Missing
    optional blocks degrade to "feature off" (e.g. no HSA when
    hsa_coverage='none'); malformed blocks hard-error.
    """
    if not isinstance(profile, dict):
        raise RetirementError(
            f"taxpayer_profile must be a JSON object, "
            f"got {type(profile).__name__}"
        )
    if not isinstance(projection_inputs, dict):
        raise RetirementError(
            f"projection_inputs must be a JSON object, "
            f"got {type(projection_inputs).__name__}"
        )
    if not isinstance(tax_law, dict):
        raise RetirementError(
            f"tax_law must be a JSON object, got {type(tax_law).__name__}"
        )

    # Both year comparisons below describe the SHAPE of a value that is not a
    # plain integer year rather than printing it. Both files are hand-edited,
    # and a misplaced name or nested object reaches these messages as readily
    # as a wrong year does; `redact` masks SSN and credential shapes only
    # (AGENTS.md #1). A genuine wrong year still prints, because which year the
    # file is for is the whole content of the message.
    profile_year = profile.get("tax_year")
    if tax_year is not None and profile_year not in (None, tax_year):
        raise RetirementError(
            f"taxpayer_profile.tax_year is {year_or_shape(profile_year)}, "
            f"expected TY{tax_year}"
        )
    inputs_year = projection_inputs.get("tax_year")
    if tax_year is not None and inputs_year not in (None, tax_year):
        raise RetirementError(
            f"projection_inputs.tax_year is {year_or_shape(inputs_year)}, "
            f"expected TY{tax_year}. Combining stale YTD figures with the "
            f"requested year's limits would produce a cross-year dashboard."
        )
    # Same generalization rule applied to the third input source.
    # Real cache writer (agents/lib/tax_law_fetcher.py) persists year at
    # BOTH top-level `tax_year` (create_empty_tax_law) AND `_meta.tax_year`
    # (prepare_tax_law_for_save). Read either; both should agree. If
    # they disagree, hard-error — a drifted writer is itself a bug.
    tax_law_meta = tax_law.get("_meta")
    if tax_law_meta is not None and not isinstance(tax_law_meta, dict):
        raise RetirementError(
            f"tax_law._meta must be a JSON object or absent, got "
            f"{type(tax_law_meta).__name__}"
        )
    law_year_top = tax_law.get("tax_year")
    law_year_meta = (tax_law_meta or {}).get("tax_year")
    if (law_year_top is not None and law_year_meta is not None
            and law_year_top != law_year_meta):
        raise RetirementError(
            f"tax_law has conflicting year fields: tax_year={law_year_top} "
            f"vs _meta.tax_year={law_year_meta}. Re-fetch via /tax-setup."
        )
    law_year = law_year_top if law_year_top is not None else law_year_meta
    if tax_year is not None and law_year not in (None, tax_year):
        raise RetirementError(
            f"tax_law year is {law_year}, expected tax_year={tax_year}. "
            f"Stale tax_law would silently price every limit and phase-out "
            f"with the wrong year's law."
        )

    fed_rate = _validate_rate(marginal_fed_rate, label="marginal_fed_rate")
    state_rate = _validate_rate(marginal_state_rate, label="marginal_state_rate")
    if isinstance(modified_agi, bool) or not isinstance(modified_agi, (int, float)):
        raise RetirementError(
            f"modified_agi must be a number, got {type(modified_agi).__name__}"
        )
    if not math.isfinite(modified_agi):
        raise RetirementError(
            f"modified_agi must be a finite number, got {modified_agi}"
        )
    if modified_agi < 0:
        raise RetirementError(f"modified_agi must be non-negative, got {modified_agi}")
    magi = float(modified_agi)

    # NOTE: do NOT use `or {}` here — that would collapse falsy non-dict
    # values like `[]`, `""`, `0` to `{}` BEFORE the isinstance check
    # fires, silently treating a malformed workspace file as if no flags
    # were set (PR #20 round-11 finding 2). Only `None` (missing or
    # explicit null) should map to the empty-dict default.
    flags = profile.get("flags")
    if flags is None:
        flags = {}
    elif not isinstance(flags, dict):
        raise RetirementError(
            f"profile.flags must be a JSON object or null, "
            f"got {type(flags).__name__}"
        )
    address = profile.get("address")
    if address is None:
        address = {}
    elif not isinstance(address, dict):
        raise RetirementError(
            f"profile.address must be a JSON object or null, "
            f"got {type(address).__name__}"
        )

    age_50 = _strict_bool(flags, "age_50_plus_primary", label="flags")
    age_55 = _strict_bool(flags, "age_55_plus_primary", label="flags")
    age_55_spouse = _strict_bool(flags, "age_55_plus_spouse", label="flags")
    age_60_63 = _strict_bool(flags, "age_60_63_primary", label="flags")
    age_60_63_spouse = _strict_bool(flags, "age_60_63_spouse", label="flags")
    # Age windows are NESTED: 60-63 ⊂ 55+ ⊂ 50+. A taxpayer who sets only
    # the narrowest applicable flag (e.g. age_60_63_primary, leaving the
    # broader flags at the template's False default) is STILL entitled to
    # the broader catch-ups. Propagate the implication now so every
    # per-account computation sees consistent flags — otherwise the 401(k)
    # section (which checks age_60_63 first) grants the super catch-up while
    # the IRA (age_50) and HSA (age_55) sections silently omit theirs,
    # understating capacity (Codex PR #20). Only narrows→broadens; never
    # infers the narrow window from a broad flag, and the 401(k)
    # supersession is preserved (compute_401k_catchup_amount checks
    # age_60_63 before age_50).
    age_55 = age_55 or age_60_63
    age_50 = age_50 or age_55
    # Spouse side: the template defines age_60_63_spouse (symmetric with
    # age_60_63_primary), but the only spouse-driven computation here is the
    # HSA spouse catch-up, which keys on age_55_spouse. The HSA catch-up is
    # a FLAT $1,000 at age 55+ (no 60-63 enhancement — the super catch-up is
    # a 401(k)-only feature), so a 60-63 spouse is simply 55+ for HSA. Without
    # this a profile that sets only age_60_63_spouse would silently drop the
    # spouse's $1,000 HSA catch-up (Codex PR #20). There is no age_50_plus_
    # spouse / spouse IRA-401(k) row, so only this one propagation is needed.
    age_55_spouse = age_55_spouse or age_60_63_spouse
    # Strict typing — same generalization-gap rule as profile.flags /
    # profile.address / projection_inputs.retirement_ytd above. The
    # benign `or "Single"` shorthand silently coerces falsy non-string
    # values (`0`, `[]`, `""`) to "Single", which would silently mis-
    # bracket the taxpayer — driving wrong IRA phase-out windows, wrong
    # tax brackets, wrong 401(k) SECURE-2.0 catch-up handling. None /
    # missing key correctly defaults to Single (the original benign
    # reason for the shorthand).
    filing_status = profile.get("filing_status")
    if filing_status is None:
        filing_status = "Single"
    elif not isinstance(filing_status, str):
        raise RetirementError(
            f"profile.filing_status must be a string or null, "
            f"got {type(filing_status).__name__}"
        )
    elif filing_status.strip().upper() not in _VALID_FILING_STATUSES:
        # Type-valid but not a recognized status. Downstream comparisons
        # only match MFJ/MFS/QSS and _binary_status_key() falls back to
        # Single, so a value like "Married Filing Jointly" or "" would
        # silently apply the wrong phase-out windows and skip the MFS
        # lived-apart requirement. Reject rather than guess (Codex PR #20).
        raise RetirementError(
            f"profile.filing_status must be one of Single / MFJ / MFS / "
            f"HoH / QSS (case-insensitive) or null, got {filing_status!r}. "
            f"A non-canonical value would silently apply the Single "
            f"phase-out windows and skip the MFS lived-apart requirement."
        )
    else:
        # Normalize surrounding whitespace so downstream `.upper()`
        # comparisons (which do not strip) match the canonical tokens.
        filing_status = filing_status.strip()

    # MFS lived-apart flag is LOAD-BEARING for any MFS filer's IRA
    # computations: lived-with-spouse triggers the $0-$10K phase-out
    # window; lived-apart-all-year triggers Single treatment per
    # Pub 590-A footnote ("If you file separately and didn't live
    # with your spouse at any time during the year, your IRA
    # deduction is determined under the Single filing status").
    # Silent default-False (lived-together) would apply the MORE
    # RESTRICTIVE window to actually-lived-apart taxpayers — wrong
    # numbers, wrong phase-out, potentially wrong refund. Hard-error
    # rather than guess (PR #20 round-16). For non-MFS, the flag is
    # unused, so the default is fine.
    if filing_status.upper() == "MFS":
        if (
            "lived_apart_from_spouse_all_year" not in flags
            or flags.get("lived_apart_from_spouse_all_year") is None
        ):
            raise RetirementError(
                "profile.flags.lived_apart_from_spouse_all_year is REQUIRED "
                "for MFS filers — it determines whether IRA computations use "
                "the MFS-with-spouse $0-$10K phase-out window or Single "
                "treatment (Pub 590-A footnote). Set to TRUE if you lived "
                "apart from your spouse at any time during ALL of the tax "
                "year, FALSE otherwise. Cannot guess this — silent default "
                "would silently apply the wrong phase-out."
            )
    lived_apart = _strict_bool(
        flags, "lived_apart_from_spouse_all_year", label="flags"
    )

    # Same strict-typing rule as profile.flags / profile.address above:
    # `or {}` silently coerces falsy non-dicts (e.g. `retirement_ytd: 0`,
    # `retirement_ytd: []`) and would zero all YTD contributions —
    # producing phantom full-capacity recommendations for a taxpayer who
    # has actually maxed out (PR #20 round-11 finding 2).
    ytd = projection_inputs.get("retirement_ytd")
    if ytd is None:
        ytd = {}
    elif not isinstance(ytd, dict):
        raise RetirementError(
            f"projection_inputs.retirement_ytd must be a JSON object or null, "
            f"got {type(ytd).__name__}"
        )

    ytd_401k = _strict_num(ytd, "employee_401k")
    ytd_match = _strict_num(ytd, "employer_match")
    ytd_ira_trad = _strict_num(ytd, "ira_traditional")
    ytd_ira_roth = _strict_num(ytd, "ira_roth")
    ytd_hsa_employee = _strict_num(ytd, "hsa_employee")
    ytd_hsa_employer = _strict_num(ytd, "hsa_employer")
    hsa_coverage = _strict_str(
        ytd, "hsa_coverage",
        allowed=("none", "self_only", "family"),
        default="none",
    )
    after_tax_ytd = _strict_num(ytd, "after_tax_401k")
    plan_allows_after_tax = _strict_bool(ytd, "plan_allows_after_tax_401k")
    # Whether the 401(k) plan offers a designated Roth source. Load-bearing
    # ONLY for the §603 high-earner case: if the catch-up MUST be Roth but
    # the plan has no Roth source, affected high earners cannot make catch-up
    # at all. Tri-state: explicit False → catch-up unavailable; True/absent →
    # assume available (absent adds a caveat). Defaults True since most plans
    # that permit catch-ups add a Roth source to comply with §603 (Codex P20).
    roth_source_specified = isinstance(ytd, dict) and "plan_offers_roth_401k" in ytd
    plan_offers_roth_401k = _strict_bool(
        ytd, "plan_offers_roth_401k", default=True
    )
    # SECURE 2.0 §603 (TY2026+): if prior-year FICA wages exceed the
    # high-earner threshold ($150k indexed), 401(k) catch-up MUST be
    # designated Roth (no current-year deduction). Per IRC §414(v)(7)(A)
    # the threshold is keyed to the wages the PLAN uses for its Roth-
    # catch-up determination — generally wages from the sponsoring
    # employer, though the final regs let a plan aggregate wages across
    # related common-law employers in the same §414(b)/(c)/(m)/(o)
    # controlled group. So a taxpayer with $200k total but only $100k from
    # this (unrelated) sponsor is NOT a high earner for this plan, while a
    # controlled-group participant may have wages aggregated. v1 takes a
    # single plan-determined figure (documented in the skill + template);
    # the caveat rides on the catch-up/warning notes and defers to the
    # plan administrator rather than asserting a blanket no-aggregation
    # rule. Optional field — when absent, dashboard warns rather than guess.
    prior_year_fica_wages = _optional_non_negative_num(
        ytd, "prior_year_fica_wages"
    )
    # IRC §219(b)(1)(A) (Traditional) / §408A(c)(2) (Roth): IRA
    # contributions cannot exceed taxable compensation. Optional
    # field — when null, dashboard emits warning. A retiree with $0
    # wages running the dashboard without this field would otherwise
    # see $7K IRA capacity that they cannot legally use, risking the
    # 6%-per-year excess-contribution penalty (PR #20 round-15).
    taxable_compensation = _optional_non_negative_num(ytd, "taxable_compensation")
    spouse_taxable_compensation = _optional_non_negative_num(
        ytd, "spouse_taxable_compensation"
    )
    covered_by_employer_plan = _strict_bool(
        flags, "has_employer_retirement_plan", label="flags"
    )
    spouse_covered_by_employer_plan = _strict_bool(
        flags, "has_employer_retirement_plan_spouse", label="flags"
    )

    # Strict typing — same `or default` generalization-gap rule as
    # profile.flags / profile.address / projection_inputs.retirement_ytd /
    # filing_status. `address.state: 0` or `address.state: []` would
    # silently coerce to "" (no state), flipping CA/NJ HSA conformity
    # from "non-conforming" to "conforming" and overstating state
    # savings (PR #20 round-13 finding 2).
    state_code = address.get("state")
    if state_code is None:
        state_code = ""
    elif not isinstance(state_code, str):
        raise RetirementError(
            f"profile.address.state must be a string or null, "
            f"got {type(state_code).__name__}"
        )
    raw_state = state_code
    state_code = state_code.strip().upper()
    # Require a real 2-letter USPS postal code (or "" for no state). A full
    # name like "California" uppercases to "CALIFORNIA", misses
    # _HSA_NON_CONFORMING_STATES, and would silently report CA/NJ HSA
    # contributions as state-deductible — overstating state savings.
    # Reject non-two-letter values rather than guess (Codex PR #20).
    #
    # MEMBERSHIP, not shape, and the paragraph above is why: the harm named
    # there is falling outside a small set, and the SHAPE check let every
    # two-letter miss straight through it. "CN" for CA and "NU" for NJ are
    # the ordinary typos, both accepted, both flipping the HSA row from
    # `hsa_non_conforming` to `hsa_conforming` — a state deduction invented
    # at the taxpayer's own marginal rate, silently, which is the
    # OVER-statement direction AGENTS.md ranks as the hard error. The
    # conformity sets below (PA/NJ/MA for the IRA and 401(k) rates) miss the
    # same way. `_USPS_STATES` is imported rather than re-listed; it is the
    # set capabilities and tax_calendar already validate against.
    if state_code and (not (len(state_code) == 2 and state_code.isalpha())
                       or state_code not in _USPS_STATES):
        raise RetirementError(
            f"profile.address.state must be a 2-letter US postal code "
            f"(e.g. 'CA', 'NJ', 'TX') or null, got {raw_state!r}. A full "
            f"state name, or a two-letter value that is not a real code, "
            f"would silently miss the CA/NJ HSA non-conformity check and "
            f"overstate state savings."
        )
    hsa_kind: AccountKind = (
        "hsa_non_conforming" if state_code in _HSA_NON_CONFORMING_STATES
        else "hsa_conforming"
    )
    # State savings is $0 where the state doesn't allow the deduction — but
    # 401(k) and IRA conformity differ by state, so use SEPARATE rates
    # (Codex PR #20). 401(k)/403(b): PA. Traditional IRA: PA, NJ, MA. Apply
    # retirement_state_rate to the 401(k) rows and ira_state_rate to the
    # Traditional IRA row. Roth/after-tax rows already yield $0 state savings
    # via `kind`; HSA via hsa_kind.
    retirement_deferral_nonconforming = (
        state_code in _RETIREMENT_DEFERRAL_NONCONFORMING_STATES
    )
    retirement_state_rate = (
        0.0 if retirement_deferral_nonconforming else state_rate
    )
    retirement_nonconforming_note = (
        f" State {state_code} taxes pre-tax 401(k)/403(b) contributions "
        f"(no state deduction) — state savings shown as $0; the federal "
        f"benefit still applies."
    ) if retirement_deferral_nonconforming else ""
    ira_deduction_nonconforming = (
        state_code in _IRA_DEDUCTION_NONCONFORMING_STATES
    )
    ira_state_rate = 0.0 if ira_deduction_nonconforming else state_rate
    ira_nonconforming_note = (
        f" State {state_code} does not allow a state deduction for "
        f"Traditional IRA contributions — state savings shown as $0; the "
        f"federal deduction (if any) still applies."
    ) if ira_deduction_nonconforming else ""

    accounts: list[AccountStatus] = []

    # 401(k) employee deferral
    # Per IRC §414(v)(7)(A) (SECURE 2.0 §603, effective TY2026 per
    # IRS Notice 2023-62): catch-up contributions MUST be designated
    # Roth for participants with prior-year FICA wages above the
    # high-earner threshold. When this applies, split the 401(k) row
    # into a pre-tax BASE row and a Roth CATCH-UP row so the
    # marginal-savings calc correctly reports $0 fed/state savings on
    # the catch-up dollars (PR #20 round-13 finding 1).
    base_elective = _require_tax_law_amount(
        tax_law, "federal", "retirement_401k", "elective_deferral"
    )
    # Effective-year context (used for both SECURE 2.0 gates below). When
    # unknown (rare; the skill always passes tax_year), default to applying
    # the post-effective rules — matches the skill's "default to current
    # calendar year" practice.
    effective_year = tax_year if tax_year is not None else profile_year
    # The §109 age-60-63 super catch-up AMOUNT is effective TY2025+. Before
    # that a 60-63 participant gets only the standard 50+ catch-up (and the
    # catch_up_60_63 field is absent from a pre-2025 cache). Gate the super
    # AMOUNT by year — but NOT the age-fact normalization above: a 60-63
    # taxpayer is still 55+/50+ for IRA/HSA in any year (Codex PR #20).
    super_catchup_applies = (
        effective_year is None
        or effective_year >= _SUPER_CATCHUP_EFFECTIVE_YEAR
    )
    age_60_63_for_amount = age_60_63 and super_catchup_applies
    catchup_amount = compute_401k_catchup_amount(
        tax_law, age_50_plus=age_50, age_60_63=age_60_63_for_amount
    )
    # §603 mandatory-Roth designation is separately gated on TY2026 per IRS
    # Notice 2023-62 (delayed from 2024). Pre-fix bug: I added
    # _SECURE_2_0_EFFECTIVE_YEAR constant in round 13 but didn't use it —
    # pre-2026 dashboards incorrectly split the row and reported $0 catch-up
    # savings, understating the user's TY2024/TY2025 tax position (round-14).
    secure_2_0_applies = (
        effective_year is None
        or effective_year >= _SECURE_2_0_EFFECTIVE_YEAR
    )
    # The high-earner threshold lookup hard-errors for a post-2026 cache that
    # lacks the indexed value — so DEFER it until catch-up classification is
    # actually needed (catchup_amount > 0 and §603 in effect). An under-50
    # taxpayer (catchup_amount == 0) never uses the threshold, so their
    # otherwise-valid dashboard must not be blocked by a post-2026 high-earner
    # value they can't use (Codex PR #20).
    high_earner_threshold = None
    is_high_earner = False
    if catchup_amount > 0 and secure_2_0_applies:
        high_earner_threshold = _get_secure_2_0_high_earner_threshold(
            tax_law, effective_year=effective_year
        )
        is_high_earner = (
            prior_year_fica_wages is not None
            and prior_year_fica_wages > high_earner_threshold
        )
    # Catch-up is UNAVAILABLE when a §603 high earner's plan has no Roth
    # source (explicit plan_offers_roth_401k=False): the mandatory-Roth
    # catch-up can't be made, so dollars above base are excess deferrals (NOT
    # legal catch-up) — this also makes them consume §415(c) annual-additions
    # space in the Mega Backdoor calc below (Codex PR #20).
    catchup_unavailable = (
        is_high_earner and roth_source_specified and not plan_offers_roth_401k
    )
    if catchup_amount > 0 and catchup_unavailable:
        # High earner whose §603 catch-up MUST be designated Roth, but the
        # plan has NO designated Roth source (plan_offers_roth_401k=False).
        # Affected high earners then cannot make catch-up at all — show the
        # base elective deferral only; the full catch-up would be a phantom,
        # unavailable contribution path (Codex PR #20).
        accounts.append(AccountStatus(
            name="401(k) employee",
            ytd_contributed=ytd_401k,
            annual_limit=base_elective,
            marginal_fed_rate=fed_rate,
            marginal_state_rate=retirement_state_rate,
            kind="pre_tax",
            note=(
                f"Catch-up UNAVAILABLE: SECURE 2.0 §603 requires this high "
                f"earner's catch-up to be designated Roth, but "
                f"`plan_offers_roth_401k` is False — a plan with no "
                f"designated Roth source cannot accept catch-up from "
                f"affected high earners. Only the ${base_elective:,.0f} "
                f"base elective deferral is available; ask the plan "
                f"administrator to add a Roth source to enable the "
                f"${catchup_amount:,.0f} catch-up."
            ) + retirement_nonconforming_note,
        ))
    elif catchup_amount > 0 and is_high_earner:
        # Split into base (pre-tax) + catch-up (Roth) rows. YTD
        # contributions allocate to base first, then overflow to
        # catch-up (matches IRS treatment: catch-up only applies
        # once the base elective limit is filled).
        ytd_base = min(ytd_401k, base_elective)
        ytd_catchup_portion = max(0.0, ytd_401k - base_elective)
        accounts.append(AccountStatus(
            name="401(k) employee (base)",
            ytd_contributed=ytd_base,
            annual_limit=base_elective,
            marginal_fed_rate=fed_rate,
            marginal_state_rate=retirement_state_rate,
            kind="pre_tax",
            note=(
                "Base elective deferral — pre-tax. Catch-up moves to "
                "the Roth row below per SECURE 2.0 §603 (prior-year "
                "FICA wages above the high-earner threshold)."
            ) + retirement_nonconforming_note,
        ))
        accounts.append(AccountStatus(
            name="401(k) catch-up (Roth required)",
            ytd_contributed=ytd_catchup_portion,
            annual_limit=catchup_amount,
            marginal_fed_rate=fed_rate,
            marginal_state_rate=state_rate,
            kind="roth",
            note=(
                f"SECURE 2.0 §603 (effective TY2026 per IRS Notice "
                f"2023-62): prior-year FICA wages "
                f"${prior_year_fica_wages:,.0f} exceed the "
                f"${high_earner_threshold:,.0f} threshold — catch-up "
                f"contributions MUST be designated Roth (no "
                f"current-year deduction). Tax-free growth via "
                f"in-plan / in-service Roth treatment. NOTE: the "
                f"threshold is keyed to the FICA wages your PLAN uses for "
                f"its Roth-catch-up determination (IRC §414(v)(7)(A)) — "
                f"generally the sponsoring employer's, though related "
                f"common-law employers in the same controlled group may be "
                f"aggregated per the final regs. If you changed jobs or had "
                f"unrelated employers, use the sponsoring plan's figure, not "
                f"your total W-2 wages; confirm with the plan administrator. "
                f"This row assumes the FULL catch-up must be Roth, but per "
                f"the final regs (Treas. Reg. §1.414(v)-2(b)(1)) the Roth "
                f"requirement is satisfied only to the extent your prior "
                f"designated-Roth 401(k) deferrals this year are BELOW the "
                f"catch-up limit — if you've already made Roth deferrals at "
                f"least equal to the catch-up amount, additional catch-up "
                f"dollars may be PRE-TAX (state/federal-deductible). v1 "
                f"doesn't track the pre-tax/Roth YTD split, so the $0 "
                f"current-year savings shown here may be UNDERSTATED for "
                f"such savers. This catch-up also REQUIRES the plan to offer "
                f"a designated Roth source — if it doesn't, set "
                f"`plan_offers_roth_401k` to false (affected high earners "
                f"then cannot make catch-up at all)."
            ),
        ))
    else:
        limit_401k = base_elective + catchup_amount
        note_401k = "Pre-tax + Roth aggregate against the same elective limit."
        if (
            secure_2_0_applies
            and catchup_amount > 0
            and prior_year_fica_wages is None
        ):
            # Catch-up eligible but wage data missing — cannot determine
            # high-earner status. Preserve existing behavior + warn.
            # Gate on effective year too (round-14): pre-2026 catch-up
            # is statutorily pre-tax regardless of wages, so the
            # warning would be noise.
            note_401k += (
                f" NOTE: SECURE 2.0 §603 (effective TY2026) requires "
                f"catch-up to be designated Roth (no current-year "
                f"deduction) for participants with prior-year FICA "
                f"wages above ${high_earner_threshold:,.0f}. Provide "
                f"retirement_ytd.prior_year_fica_wages (the wages your PLAN "
                f"uses for its Roth-catch-up determination per IRC "
                f"§414(v)(7)(A) — generally the sponsoring employer's, with "
                f"related controlled-group employers possibly aggregated; "
                f"not your total W-2 across unrelated employers) for accurate "
                f"accounting; the marginal savings shown here may be "
                f"overstated by the catch-up portion × marginal rate."
            )
        note_401k += retirement_nonconforming_note
        accounts.append(AccountStatus(
            name="401(k) employee",
            ytd_contributed=ytd_401k,
            annual_limit=limit_401k,
            marginal_fed_rate=fed_rate,
            marginal_state_rate=retirement_state_rate,
            kind="pre_tax",
            note=note_401k,
        ))

    # Traditional IRA
    # IRC §219(b)(1)(A) (Traditional) / §408A(c)(2) (Roth): IRA
    # contributions are capped by taxable compensation. §219(c) MFJ
    # spousal exception: a non-working spouse can use the working
    # spouse's compensation when filing jointly (MFJ ONLY — does not
    # extend to MFS, Single, HoH, QSS). PR #20 round-15 fix:
    # previously the pool was uncapped, silently letting retirees
    # with $0 wages see $7K capacity they cannot legally use.
    ira_statutory_limit = compute_ira_limit(tax_law, age_50_plus=age_50)
    if taxable_compensation is not None:
        # §219(c) MFJ spousal exception is MFJ-ONLY. Do NOT use
        # `_binary_status_key()` here — that helper collapses MFJ + QSS
        # into "mfj" for capital-gains lookups, but QSS has a DECEASED
        # spouse who cannot have current-year taxable compensation
        # (PR #20 round-18). Stratum-14 recurrence: spouse-property
        # fields must be gated on filing_status with a LIVING spouse,
        # which is MFJ alone (MFS technically has a living spouse but
        # §219(c) is statutorily MFJ-only).
        ira_combines_spouse_comp = (
            (filing_status or "").upper() == "MFJ"
        )
        if ira_combines_spouse_comp and spouse_taxable_compensation is not None:
            available_compensation = (
                taxable_compensation + spouse_taxable_compensation
            )
        else:
            available_compensation = taxable_compensation
        ira_limit = min(ira_statutory_limit, available_compensation)
    else:
        # Compensation data missing — fall back to statutory limit
        # and emit a warning below so the user knows capacity may be
        # overstated for no-wage / low-wage years.
        ira_limit = ira_statutory_limit
    trad_frac = compute_ira_traditional_deductible_fraction(
        tax_law, modified_agi=magi, filing_status=filing_status,
        covered_by_employer_plan=covered_by_employer_plan,
        spouse_covered_by_employer_plan=spouse_covered_by_employer_plan,
        lived_apart_from_spouse_all_year=lived_apart,
    )
    # IRA pool — Traditional and Roth share the annual IRA limit per IRS
    # Pub 590-A. Compute pool_remaining ONCE and pass to both accounts so
    # per-bucket `remaining` caps at the shared budget and
    # `DashboardReport.total_remaining` dedupes the pool.
    ira_pool_remaining = round(
        max(0.0, ira_limit - ytd_ira_trad - ytd_ira_roth), 2
    )
    # Symmetric inverse: the pool can also OVER-contribute even when
    # each bucket is at or under its bucket cap (e.g. $7000 Trad +
    # $7000 Roth = $14k vs $7000 pool cap = $7k excess). IRS excise
    # tax: 6% per year on excess IRA contributions until corrected.
    # Surfaced on each pool member so the user sees the
    # corrective-distribution requirement on either IRA row.
    ira_pool_excess = round(
        max(0.0, ytd_ira_trad + ytd_ira_roth - ira_limit), 2
    )
    # Shared warning text appended to BOTH IRA rows when the pool is over.
    ira_pool_excess_note = (
        f" IRA pool over-contributed by ${ira_pool_excess:,.0f} — "
        f"corrective distribution required before April 15 to avoid "
        f"6% excise tax per year."
    ) if ira_pool_excess > 0 else ""
    # Phase-out reduces the DEDUCTIBLE CAP, not the per-dollar rate
    # (IRS Pub 590-A Worksheet 1-2). Each dollar up to
    # `ira_limit × trad_frac` is fully deductible at the full marginal
    # rate; dollars above are nondeductible. Cap eligible-remaining at
    # the IRA pool budget — a maxed Roth leaves no Trad capacity.
    # Worksheet 1-2 steps 5-6 round the raw cap UP to the next $10 and
    # impose a $200 floor when partially phased out (raw > 0), so a
    # taxpayer 1 dollar inside a phase-out window gets $200 cap not
    # $0.70 cap (Codex round-10 fix).
    trad_deductible_cap = _apply_irs_phaseout_rounding(ira_limit * trad_frac)
    trad_eligible_remaining = round(
        max(0.0, min(
            ira_pool_remaining,
            trad_deductible_cap - ytd_ira_trad,
        )),
        2,
    )
    # Note text: fire on TRAD_FRAC value, not on the primary-coverage
    # flag alone. The spouse-covered MFJ case (round-3 fix) also
    # produces partial / zero deductibility and the user deserves the
    # same explanation.
    is_spouse_covered_case = (
        not covered_by_employer_plan
        and spouse_covered_by_employer_plan
        and _binary_status_key(filing_status) == "mfj"
    )
    trad_note = ""
    if 0 < trad_frac < 1:
        if is_spouse_covered_case:
            trad_note = (
                f"Partial deduction ({int(trad_frac * 100)}%) — your spouse "
                f"is covered by an employer plan and your MAGI is inside the "
                f"MFJ spouse-covered phase-out (per IRS Pub 590-A Table 1-3)."
            )
        else:
            trad_note = (
                f"Partial deduction ({int(trad_frac * 100)}%) — Traditional "
                f"IRA deduction phases out per MAGI."
            )
    elif trad_frac == 0 and (covered_by_employer_plan or is_spouse_covered_case):
        if is_spouse_covered_case:
            trad_note = (
                "Non-deductible — your spouse is covered and your MAGI "
                "exceeds the MFJ spouse-covered phase-out (consider "
                "backdoor Roth)."
            )
        else:
            trad_note = (
                "Non-deductible — direct deduction phased out "
                "(consider backdoor Roth)."
            )
    if trad_note:
        trad_note += " "
    trad_note += "Shares the IRS annual IRA limit with Roth IRA."
    trad_note += ira_pool_excess_note
    if taxable_compensation is None and ira_pool_remaining > 0:
        # PR #20 round-15: warn when compensation data is missing AND
        # the pool has nonzero remaining capacity. Without comp data,
        # the dashboard shows statutory capacity that a no-wage
        # retiree cannot legally use — providing the field
        # protects against 6%-per-year excess-contribution penalty.
        trad_note += (
            " NOTE: IRA contributions are capped by taxable "
            "compensation per IRC §219(b)(1)(A). Provide "
            "retirement_ytd.taxable_compensation (and "
            "spouse_taxable_compensation if MFJ for the §219(c) "
            "spousal exception) for accurate accounting — without "
            "it, capacity may be overstated for retirees / no-wage "
            "years, and a contribution above the compensation cap "
            "triggers the IRS 6%-per-year contribution-limit "
            "excise tax."
        )
    elif (
        (filing_status or "").upper() == "MFJ"
        and spouse_taxable_compensation is None
        and taxable_compensation < ira_statutory_limit
    ):
        # MFJ with the PRIMARY's own compensation supplied but the spouse's
        # omitted (template default null). The primary-only compensation
        # constrains the cap below the statutory limit — but §219(c) lets a
        # non-working / lower-earning spouse use the WORKING spouse's
        # compensation, which could raise the cap. A single-earner couple
        # whose dashboard subject is the $0-wage spouse would otherwise see a
        # CONFIDENT $0 capacity. Warn rather than silently understate — but
        # keep the conservative cap (don't lift to statutory) so we never
        # OVERSTATE and induce the 6% excise the round-15 guard prevents
        # (Codex PR #20).
        trad_note += (
            " NOTE: capacity is capped at the provided "
            f"taxable_compensation (${taxable_compensation:,.0f}), but under "
            "the §219(c) MFJ spousal exception you may use the working "
            "spouse's compensation — provide "
            "retirement_ytd.spouse_taxable_compensation for an accurate cap; "
            "without it, IRA capacity may be UNDERSTATED here."
        )
    trad_note += ira_nonconforming_note
    accounts.append(AccountStatus(
        name="Traditional IRA",
        ytd_contributed=ytd_ira_trad,
        annual_limit=ira_limit,
        marginal_fed_rate=fed_rate,
        marginal_state_rate=ira_state_rate,
        kind="pre_tax",
        note=trad_note,
        shared_pool_id="ira",
        pool_remaining=ira_pool_remaining,
        marginal_eligible_dollars=trad_eligible_remaining,
        pool_over_contributed=ira_pool_excess,
    ))

    # Roth IRA
    roth_frac = compute_roth_contribution_fraction(
        tax_law, modified_agi=magi, filing_status=filing_status,
        lived_apart_from_spouse_all_year=lived_apart,
    )
    # Bucket cap: shared IRA limit × MAGI-allowed Roth fraction. Pool
    # remaining (set above) further caps `remaining` so a maxed Traditional
    # correctly shows Roth additional capacity as $0. The Pub 590-A Roth
    # reduced-contribution worksheet (number shifts across publication
    # years; confirm against the targeted edition) uses the same ceil-to-$10
    # + $200 floor rounding the Trad cap above applies.
    roth_effective_limit = _apply_irs_phaseout_rounding(ira_limit * roth_frac)
    roth_note = ""
    if roth_frac == 0:
        roth_note = "Direct Roth fully phased out — backdoor Roth available."
    elif roth_frac < 1:
        roth_note = (
            f"Partial Roth ({int(roth_frac * 100)}%) — direct Roth phases "
            f"out per MAGI."
        )
    if roth_note:
        roth_note += " "
    roth_note += "Shares the IRS annual IRA limit with Traditional IRA."
    roth_note += ira_pool_excess_note
    accounts.append(AccountStatus(
        name="Roth IRA",
        ytd_contributed=ytd_ira_roth,
        annual_limit=roth_effective_limit,
        marginal_fed_rate=fed_rate,
        marginal_state_rate=state_rate,
        kind="roth",
        note=roth_note,
        shared_pool_id="ira",
        pool_remaining=ira_pool_remaining,
        pool_over_contributed=ira_pool_excess,
    ))

    # HSA
    # Emit the HSA row when EITHER coverage is set OR YTD > 0. A user who
    # contributed without HDHP eligibility (coverage="none" + ytd > 0) has
    # all-excess contributions — silently skipping the row hides the
    # corrective-action signal this dashboard is meant to surface.
    hsa_ytd_total = ytd_hsa_employee + ytd_hsa_employer
    if hsa_coverage in ("self_only", "family") or hsa_ytd_total > 0:
        if hsa_coverage in ("self_only", "family"):
            hsa_limit = compute_hsa_limit(
                tax_law, coverage=hsa_coverage, age_55_plus=age_55
            )
        else:
            # Contradiction: YTD > 0 but no HDHP coverage declared.
            # Every dollar contributed is excess (no allowed contribution).
            hsa_limit = 0.0
        # Per IRS Pub 969: HSA catch-up is per-individual, not per-HDHP.
        # For FAMILY coverage + spouse also 55+, the spouse can make a
        # separate $1,000 catch-up contribution into their own HSA. The
        # dashboard tracks the household-combined limit (separation
        # across two HSAs is operational, not a dashboard concern).
        # For SELF-ONLY coverage the spouse has no HDHP, so spouse
        # catch-up is irrelevant — skip.
        hsa_extra_note = ""
        # Spouse HSA catch-up is added only on a JOINT return (MFJ).
        # `age_55_plus_spouse` is an entity-property flag (about the
        # spouse), and can persist in a profile stale from a prior MFJ
        # year after divorce or hand-edit. Single/HoH have no spouse by
        # definition; QSS (Qualifying Surviving Spouse) has a DECEASED
        # spouse who cannot make HSA contributions (PR #20 round-12
        # finding 2). MFS DOES have a living spouse, but each MFS spouse
        # files a SEPARATE return — the spouse's $1k catch-up belongs on
        # the spouse's own return (their own dashboard run), so it must
        # NOT inflate the MFS primary's own contribution capacity. Only
        # MFJ's single household return legitimately combines both
        # spouses' catch-ups.
        spouse_catchup_on_this_return = (filing_status or "").upper() == "MFJ"
        if (
            hsa_coverage == "family"
            and age_55_spouse
            and spouse_catchup_on_this_return
        ):
            spouse_catchup = _require_tax_law_amount(
                tax_law, "federal", "hsa_limits", "catch_up_55_plus"
            )
            hsa_limit += spouse_catchup
            hsa_extra_note = (
                "Includes a separate spouse age-55+ catch-up — per IRS "
                "Pub 969 this must go into a SECOND HSA in the spouse's "
                "name (cannot be combined with primary's account)."
            )
        hsa_ytd = hsa_ytd_total
        hsa_note = ""
        if hsa_coverage == "none" and hsa_ytd_total > 0:
            # Data-vs-flag contradiction: corrective action required.
            hsa_note = (
                "HSA contributions reported but `hsa_coverage` is 'none' "
                "— every contributed dollar is excess. Verify HDHP "
                "eligibility; if eligible, set `hsa_coverage` to your "
                "tier in projection_inputs.json and re-run. If not "
                "eligible, withdraw the excess before April 15 to avoid "
                "the 6% excise tax per year on the excess."
            )
        if hsa_kind == "hsa_non_conforming":
            hsa_kind_note = (
                f"State {state_code} does not conform to federal HSA "
                f"treatment — HSA contributions remain state-taxable."
            )
            hsa_note = _join_note(hsa_note, hsa_kind_note)
        if hsa_extra_note:
            hsa_note = _join_note(hsa_note, hsa_extra_note)
        if hsa_coverage in ("self_only", "family"):
            # Partial-year eligibility caveat. IRS Pub 969 §223(b)(2)
            # prorates the HSA limit by eligible months (limit ÷ 12 × months
            # HSA-eligible on the 1st) for a mid-year HDHP gain/loss or tier
            # change — UNLESS the §223(b)(8) last-month rule applies (covered
            # on Dec 1 → full-year limit, but a 13-month testing period must
            # be met). v1 shows the full annual limit; proration + the
            # last-month rule's testing period are out of scope, so warn
            # rather than silently show ~2x capacity for a partial year
            # (Codex PR #20).
            hsa_note = _join_note(hsa_note, (
                "Limit assumes FULL-YEAR HDHP coverage. If you gained/lost "
                "HDHP coverage or changed tiers mid-year, IRS Pub 969 "
                "prorates the limit by eligible months (limit ÷ 12 × months "
                "eligible) UNLESS the last-month rule applies (HDHP-covered "
                "on Dec 1 → full-year limit, but you must stay HSA-eligible "
                "through all of the next year or the extra is taxed + 20% "
                "penalty). v1 does not model partial-year proration — if you "
                "weren't covered all year, your actual limit (and any "
                "excess) may be lower than shown."
            ))
        accounts.append(AccountStatus(
            name="HSA",
            ytd_contributed=hsa_ytd,
            annual_limit=hsa_limit,
            marginal_fed_rate=fed_rate,
            marginal_state_rate=state_rate,
            kind=hsa_kind,
            note=hsa_note,
        ))

    # Mega Backdoor Roth (after-tax 401(k) space)
    # Same emission-gate-vs-data-contradiction rule as HSA: emit when
    # EITHER plan_allows_after_tax OR after_tax_ytd > 0. A user who
    # contributed after-tax to a plan that doesn't allow it has all-excess
    # contributions — silently skipping would hide the corrective signal.
    if plan_allows_after_tax or after_tax_ytd > 0:
        if plan_allows_after_tax:
            total_annual = _require_tax_law_amount(
                tax_law, "federal", "retirement_401k", "total_annual_limit"
            )
            # Per IRC §415(c) + §414(v): catch-up contributions are EXCLUDED
            # from the §415(c) annual-additions limit. So when computing
            # after-tax space, only the NON-catch-up portion of YTD 401(k)
            # consumes the §415(c) budget — the catch-up portion sits on top.
            #
            # The §414(v) exclusion applies ONLY to age-eligible catch-up
            # dollars. An under-50 taxpayer who somehow contributed above
            # the base elective limit has EXCESS elective deferral — not
            # catch-up — and that excess IS an annual addition until
            # corrected via distribution. SECURE 2.0: age 60-63 super
            # catch-up SUPERSEDES (does not stack with) the standard 50+
            # catch-up; mirrors the rule in compute_401k_employee_limit.
            # PR #20 round-12 finding 1: the prior `min(ytd_401k,
            # base_elective)` formulation treated EVERY dollar above
            # base as if it were catch-up — overstating Mega Backdoor
            # space precisely in the over-contribution scenario the
            # dashboard exists to surface.
            # `base_elective` and `catchup_amount` were already resolved in
            # the 401(k) section above (same tax_law, same age flags) — reuse
            # them rather than re-fetching. `catchup_amount` is the age-
            # eligible catch-up max per compute_401k_catchup_amount (0 when
            # not catch-up eligible). BUT when catch-up is UNAVAILABLE (a §603
            # high earner whose plan has no Roth source), no dollars above
            # base can be legal catch-up — they're excess deferrals that
            # consume §415(c) annual-additions space, so the eligible catch-up
            # max is 0 and the after-tax room is correctly reduced (Codex P20).
            eligible_catchup_max = 0.0 if catchup_unavailable else catchup_amount
            excess_above_base = max(0.0, ytd_401k - base_elective)
            legal_catchup_used = min(excess_above_base, eligible_catchup_max)
            # The §415(c)-consuming portion is everything EXCEPT the
            # legal catch-up — i.e. base elective + any illegal excess
            # deferral above the eligible catch-up max.
            non_catchup_ytd_401k = ytd_401k - legal_catchup_used
            # The Mega Backdoor cap is the §415(c) space available to
            # after-tax contributions, INDEPENDENT of the after-tax YTD.
            # §415(c) caps annual additions at the LESSER of the dollar
            # limit or 100% of compensation, so a low-comp / partial-year
            # participant has less room than the dollar limit alone implies.
            # Cap by taxable_compensation (the closest available §415(c)
            # comp proxy; §415 comp is per-participant, so NOT the §219(c)
            # spouse-combined figure) when provided; warn when absent so we
            # don't lead a low-comp saver into an excess annual addition
            # (Codex PR #20).
            annual_additions_limit = total_annual
            if taxable_compensation is not None:
                annual_additions_limit = min(total_annual, taxable_compensation)
            after_tax_cap = max(
                0.0, annual_additions_limit - non_catchup_ytd_401k - ytd_match
            )
            mega_note = (
                "After-tax 401(k) — no current-year deduction; tax-free "
                "growth via in-service conversion."
            )
            if taxable_compensation is None:
                mega_note += (
                    " NOTE: §415(c) caps annual additions at the LESSER of "
                    "the dollar limit or 100% of compensation. Without "
                    "retirement_ytd.taxable_compensation this room is the "
                    "dollar limit only and may be OVERSTATED for a low-comp "
                    "or partial-year participant — provide compensation for "
                    "an accurate §415(c) cap."
                )
            elif taxable_compensation < total_annual:
                mega_note += (
                    f" Capped at 100% of compensation "
                    f"(${taxable_compensation:,.0f}) per the §415(c) "
                    f"annual-additions limit (lesser of the dollar limit or "
                    f"compensation)."
                )
        else:
            # Contradiction: after_tax_ytd > 0 but plan_allows=False.
            # Every after-tax dollar is excess against an unauthorized
            # account type — corrective action required.
            after_tax_cap = 0.0
            mega_note = (
                "After-tax 401(k) contributions reported but "
                "`plan_allows_after_tax_401k` is False — every dollar is "
                "excess against an unauthorized account type. Verify with "
                "your plan administrator. If the plan DOES allow it, set "
                "`plan_allows_after_tax_401k` to True and re-run. If it "
                "doesn't, this is an excess §415(c) annual addition — "
                "coordinate a correction with the plan administrator "
                "(typically via EPCRS); the returned after-tax amount plus "
                "allocable earnings is included in income. NOTE: §415(c) "
                "excess annual additions are NOT subject to the §4973 6% "
                "excise tax that applies to excess IRA/HSA contributions, "
                "and there is no individual April-15 deadline — correction "
                "runs through the plan."
            )
        accounts.append(AccountStatus(
            name="Mega Backdoor",
            ytd_contributed=after_tax_ytd,
            annual_limit=round(after_tax_cap, 2),
            marginal_fed_rate=fed_rate,
            marginal_state_rate=state_rate,
            kind="after_tax",
            note=mega_note,
        ))

    return DashboardReport(
        year=tax_year or profile_year or 0,
        filing_status=filing_status,
        state=state_code or "",
        marginal_fed_rate=fed_rate,
        marginal_state_rate=state_rate,
        modified_agi=magi,
        accounts=accounts,
    )


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def _money(n: float) -> str:
    return f"${n:,.0f}"


def _pct(f: float) -> str:
    return f"{f * 100:.0f}%"


def render_markdown(report: DashboardReport) -> str:
    lines = [
        f"# Retirement Contribution Dashboard — TY{report.year}",
        "",
        f"- Filing status: {report.filing_status}",
        f"- State: {report.state or '(unspecified)'}",
        f"- Modified AGI: {_money(report.modified_agi)}",
        f"- Marginal rates: federal {_pct(report.marginal_fed_rate)} / "
        f"state {_pct(report.marginal_state_rate)}",
        "",
        "## Per-account status",
        "",
        "| Account | YTD | Limit | Remaining | Used | Fed $ saved | State $ saved | Note |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for a in report.accounts:
        lines.append(
            f"| {a.name} | {_money(a.ytd_contributed)} | {_money(a.annual_limit)} "
            f"| {_money(a.remaining)} | {_pct(a.pct_used)} "
            f"| {_money(a.marginal_fed_saved)} | {_money(a.marginal_state_saved)} "
            f"| {a.note} |"
        )
    lines += [
        "",
        "## Total potential savings",
        "",
        f"- Total remaining capacity: **{_money(report.total_remaining)}**",
        f"- Total potential federal savings (if maxed): "
        f"**{_money(report.total_potential_fed_saved)}**",
        f"- Total potential state savings (if maxed): "
        f"**{_money(report.total_potential_state_saved)}**",
    ]
    # Surface pool over-contributions as a top-level warning since they
    # require corrective action (6% excise tax per year until withdrawn).
    seen_pool_excess = set()
    for a in report.accounts:
        if (a.shared_pool_id is not None and a.pool_over_contributed > 0
                and a.shared_pool_id not in seen_pool_excess):
            seen_pool_excess.add(a.shared_pool_id)
            lines += [
                "",
                f"## ⚠ {a.shared_pool_id.upper()} pool over-contributed",
                "",
                f"- Excess: **{_money(a.pool_over_contributed)}**",
                "- Corrective distribution required before April 15 (or "
                "October 15 with extension) to avoid the 6% excise tax",
                "  per year on the excess.",
            ]
    # Surface per-account over-contributions too — over_contributed is
    # otherwise invisible in the table (an over-funded account renders as
    # Remaining $0 / Used 100%, indistinguishable from being exactly maxed),
    # hiding the corrective signal the skill promises (Codex PR #20).
    for a in report.accounts:
        if a.shared_pool_id is None:
            # Non-pooled (401(k) / HSA / Mega Backdoor): the bucket cap is
            # the only cap, so any over_contributed is a real excess.
            excess = a.over_contributed
        else:
            # Pooled (IRA): the pool banner above already covers the pool
            # excess. Surface a bucket-specific excess only when it EXCEEDS
            # the pool excess. For Traditional the bucket cap IS the pool cap
            # so over_contributed <= pool_over_contributed and this never
            # fires (no double-report). For Roth the MAGI-reduced cap can sit
            # BELOW the pool cap, so a direct Roth above it is a distinct
            # excess contribution even when the combined pool is under the
            # annual limit (e.g. high MAGI → $0 Roth limit, $1k Roth YTD,
            # $0 Traditional → pool $1k < $7k cap but Roth is $1k over).
            excess = (
                a.over_contributed
                if a.over_contributed > a.pool_over_contributed else 0.0
            )
        if excess > 0:
            lines += [
                "",
                f"## ⚠ {a.name} over its limit",
                "",
                f"- Excess: **{_money(excess)}**",
            ]
            if a.shared_pool_id is not None:
                lines.append(
                    "- This exceeds the account's own limit (e.g. the "
                    "MAGI-reduced direct Roth limit) even though the combined "
                    "IRA pool may be within the annual cap. Recharacterize or "
                    "withdraw the excess before the filing deadline to avoid "
                    "the 6% excise tax per year."
                )
            elif a.kind == "after_tax":
                # After-tax 401(k) = §415(c) annual addition, NOT an IRA/HSA
                # §4973 excess — different correction path and no 6% excise.
                lines.append(
                    "- This exceeds the account's annual limit as an excess "
                    "§415(c) annual addition — corrected through the plan "
                    "(typically EPCRS); the returned after-tax amount plus "
                    "earnings is included in income. NOT subject to the §4973 "
                    "6% excise tax (that applies to excess IRA/HSA "
                    "contributions). Confirm with your plan administrator."
                )
            else:
                lines.append(
                    "- This exceeds the account's annual limit. An excess "
                    "401(k) elective deferral must be corrected by April 15 to "
                    "avoid double taxation; an excess HSA contribution incurs "
                    "a 6% excise tax per year until withdrawn. Confirm the "
                    "correction path with your plan administrator."
                )
    lines += [
        "",
        "## Caveats",
        "",
        "- Marginal rates assume the next dollar contributed stays inside the",
        "  current bracket. Large contributions may straddle brackets.",
        "- IRA Traditional deductibility depends on whether you or your spouse",
        "  are covered by an employer retirement plan (set",
        "  `flags.has_employer_retirement_plan` in `taxpayer_profile.json`).",
        "- HSA non-conforming states (CA / NJ) tax the contribution at",
        "  the state level — federal benefit still applies.",
        "- Roth contributions yield $0 current-year deduction (the benefit",
        "  is tax-free growth + withdrawal, not modeled here). Mega Backdoor",
        "  after-tax contributions are similar.",
        "- This dashboard is a planning aid, not a filed return. Cross-check",
        "  account-specific limits against the plan administrator and",
        "  IRS Publications 590-A/B (IRAs) and 969 (HSAs).",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RetirementError(f"{redact(path)} is not valid JSON: {redact(e)}") from e
    except UnicodeDecodeError as e:
        raise RetirementError(f"{redact(path)} is not valid UTF-8 text: {redact(e)}") from e
    except OSError as e:
        raise RetirementError(f"could not read {redact(path)}: {redact(e)}") from e


def _main(argv: "list[str]") -> int:
    parser = argparse.ArgumentParser(prog="agents.lib.retirement_dashboard")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--inputs", required=True,
                        help="path to projection_inputs.json")
    parser.add_argument("--tax-law", required=True,
                        help="path to tax_law_<year>.json")
    add_workspace_argument(parser)
    parser.add_argument("--year", required=True)
    parser.add_argument("--marginal-fed", required=True, type=float,
                        help="federal marginal rate as decimal (e.g. 0.24)")
    parser.add_argument("--marginal-state", required=True, type=float,
                        help="state marginal rate as decimal (e.g. 0.093 for CA)")
    parser.add_argument("--magi", required=True, type=float,
                        help="modified AGI for phase-out computations")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of markdown")
    args = parser.parse_args(argv)

    try:
        year = _validate_year_arg(args.year)
        # `resolve_workspace` refuses a path that is not a usable directory,
        # so the local existence check that stood here is gone with it — one
        # answer to "is this a workspace", in the module that also decides
        # WHICH directory it is.
        workspace = resolve_workspace(args.workspace).resolve()
        try:
            # Relative to the WORKSPACE, not the process CWD: the skills run
            # from the checkout now and `--profile taxpayer_profile.json`
            # means the profile in the taxpayer's directory.
            profile_path = resolve_under_workspace(args.profile, workspace)
            inputs_path = resolve_under_workspace(args.inputs, workspace)
            law_path = resolve_under_workspace(args.tax_law, workspace)
        except RolloverError as e:
            raise RetirementError(str(e)) from e

        profile = _read_json(profile_path)
        inputs = _read_json(inputs_path)
        law = _read_json(law_path)
        report = build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=args.marginal_fed,
            marginal_state_rate=args.marginal_state,
            modified_agi=args.magi,
            tax_year=year,
        )
    except (WorkspaceError, RetirementError, RolloverError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "year": report.year,
            "filing_status": report.filing_status,
            "state": report.state,
            "marginal_fed_rate": report.marginal_fed_rate,
            "marginal_state_rate": report.marginal_state_rate,
            "modified_agi": report.modified_agi,
            "accounts": [
                {
                    "name": a.name,
                    "ytd_contributed": a.ytd_contributed,
                    "annual_limit": a.annual_limit,
                    "remaining": a.remaining,
                    "pct_used": a.pct_used,
                    "marginal_fed_saved": a.marginal_fed_saved,
                    "marginal_state_saved": a.marginal_state_saved,
                    "over_contributed": a.over_contributed,
                    "pool_over_contributed": a.pool_over_contributed,
                    "kind": a.kind,
                    "note": a.note,
                }
                for a in report.accounts
            ],
            "total_remaining": report.total_remaining,
            "total_potential_fed_saved": report.total_potential_fed_saved,
            "total_potential_state_saved": report.total_potential_state_saved,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
