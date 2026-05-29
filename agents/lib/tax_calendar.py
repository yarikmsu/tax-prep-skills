#!/usr/bin/env python3
"""Quarterly reminder calendar for /tax-setup (Backlog #10).

Generates an RFC 5545 iCalendar (.ics) file with year-specific tax deadlines:
1040-ES quarterly estimated-tax payments, FBAR (with auto-extension), required
minimum distributions for taxpayers flagged `subject_to_rmd_*` (RMD start age
is birth-year-cohort-dependent under SECURE 2.0; the user asserts it rather
than the calendar deriving it from age), and state PTET windows for CA/NY/NJ
K-1 recipients (driven by `flags.k1_states`, which lists each K-1 entity's
state, NOT the taxpayer's resident state).

Read-only with respect to result.json / taxpayer_profile.json — this module only
WRITES a derived `tax_calendar_{year}.ics` sibling. It mirrors the
"pure compute lib + CLI + reused workspace boundary check" pattern of
projection.py / withholding.py / scenario.py.

v1 caveats:
- Statutory dates only. The IRS sometimes rolls a deadline forward when 4/15
  lands on a weekend or DC Emancipation Day; we surface the statutory date and
  let the taxpayer cross-check IRS.gov.
- PTET windows for CA / NY / NJ only — other states have varying or no PTET
  election. Emitted only when the K-1 flag is set, since pass-through filers
  are the only ones for whom the SALT-cap workaround applies.
- RMD uses Dec 31 as a single annual reminder. The IRS lets you defer the
  *first* RMD to April 1 of the year following your required-beginning-date
  year, but most taxpayers don't want to bunch two RMDs into one year — Dec 31
  is the safe default we surface.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_subpath,
    _validate_year_arg,
)


class CalendarError(Exception):
    """Raised when calendar generation cannot safely proceed."""


# PRODID identifies this tool to calendar clients; the trailing "v1" tracks
# event-shape compatibility — bump when adding new categories or breaking the
# UID slug format so calendar apps treat the file as a new feed.
_PRODID = "-//tax-prep-skills//tax-calendar v1//EN"
_UID_NAMESPACE = "tax-prep-skills"
_PTET_STATES = {"CA", "NY", "NJ"}
# Per-state effective tax-year windows for the PTE elective tax regime.
# (first_valid_ty, last_valid_ty or None for "no sunset to date"). When a
# tax_year falls outside its state's window, events_ptet emits an info note
# instead of actual events. Sources:
#   CA: AB 150 (2021-2025) + SB 132 (2026-2030) — sunset after TY2030
#   NY: NY Tax Law §860 — created TY2021, no sunset
#   NJ: P.L. 2019 c.320 — created TY2020, no sunset
_PTET_WINDOWS = {
    "CA": (2021, 2030),
    "NY": (2021, None),
    "NJ": (2020, None),
}
_VALID_CATEGORIES = frozenset({"federal_estimated_tax", "fbar", "rmd", "ptet", "note"})
_MIN_YEAR = 2000
_MAX_YEAR = 2099

# Untrusted strings from taxpayer_profile.json can flow into RFC 5545 fields
# (UID/SUMMARY/DESCRIPTION). Validate shape at the parse boundary AND escape
# at the serialization boundary — defense in depth.
_STATE_CODE_RE = re.compile(r"^[A-Z]{2}$")
# RFC 5545 §3.8.4.7: UIDs are TEXT but line-folding rules mean CR/LF/NUL
# embedded in a UID would tear the format apart. We also forbid any other
# C0 control character (\x00-\x1f) plus DEL (\x7f) as defensive hygiene.
_UID_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

Category = Literal["federal_estimated_tax", "fbar", "rmd", "ptet", "note"]


@dataclass(frozen=True)
class CalendarEvent:
    """A single all-day reminder.

    Invariants enforced at construction:
    - `category` must be one of the known categories
    - `quarter_label` is non-empty iff `category == "federal_estimated_tax"`
    """
    uid: str
    summary: str
    date: datetime.date
    description: str
    category: Category
    quarter_label: str = ""

    def __post_init__(self) -> None:
        if self.category not in _VALID_CATEGORIES:
            raise CalendarError(
                f"unknown category {self.category!r}; "
                f"expected one of {sorted(_VALID_CATEGORIES)}"
            )
        # Defense in depth: a UID with embedded CR/LF/NUL would let an
        # untrusted upstream string (e.g. flags.k1_states) tear the
        # iCalendar format apart.
        if _UID_CONTROL_RE.search(self.uid):
            raise CalendarError(
                f"UID must not contain control characters (CR/LF/NUL/etc.); "
                f"got {self.uid!r}"
            )
        is_estimated = self.category == "federal_estimated_tax"
        has_label = bool(self.quarter_label)
        if is_estimated and not has_label:
            raise CalendarError("1040-ES events require a quarter_label (Q1..Q4)")
        if not is_estimated and has_label:
            raise CalendarError(
                f"quarter_label only valid on federal_estimated_tax events, "
                f"got category={self.category!r} with quarter_label={self.quarter_label!r}"
            )


def _validate_tax_year(value, *, label: str = "tax_year") -> int:
    """Strict year validator — rejects bool, non-int, and out-of-range."""
    if isinstance(value, bool):
        raise CalendarError(
            f"{label} must be an int (not a bool), got {value!r}"
        )
    if not isinstance(value, int):
        raise CalendarError(f"{label} must be an int, got {value!r}")
    if not (_MIN_YEAR <= value <= _MAX_YEAR):
        raise CalendarError(
            f"{label} must be in [{_MIN_YEAR}, {_MAX_YEAR}], got {value}"
        )
    return value


def _strict_state_list(d: dict, key: str) -> list[str]:
    """k1_states is the FIRST place untrusted strings can flow into UID /
    SUMMARY / DESCRIPTION fields. Layer the generic string-list parse with
    a strict shape check — exactly 2 ASCII uppercase letters — so CRLF
    injection, full state names, accented characters, etc. are all
    rejected at the input boundary (the strongest defense — bad input
    never enters the model).
    """
    raw = _strict_str_list(d, key)
    for i, code in enumerate(raw):
        if not _STATE_CODE_RE.match(code):
            raise CalendarError(
                f"flags.{key}[{i}] must be a 2-letter US state code "
                f"(e.g. 'CA', 'NY'), got {code!r}"
            )
    # Dedupe while preserving first-occurrence order — multiple K-1s in
    # the same state share state-level PTET deadlines, so emitting one
    # set of reminders per unique state is correct. Without this dedupe,
    # `k1_states=["CA","CA"]` would build duplicate-UID PTET events and
    # the integrity check at the end of build_events would reject the
    # entire calendar.
    return list(dict.fromkeys(raw))


def _strict_str_list(d: dict, key: str) -> list[str]:
    """Optional list-of-strings field, strict.

    Returns [] when the key is missing or explicitly null. Raises
    CalendarError for any other shape (string, dict, int, list containing
    non-strings). Strips and uppercases each entry — the calendar's PTET
    set is uppercase-keyed, but a profile may carry mixed case.
    """
    if key not in d or d[key] is None:
        return []
    raw = d[key]
    if not isinstance(raw, list):
        raise CalendarError(
            f"flags.{key} must be a JSON array of state codes, "
            f"got {type(raw).__name__}"
        )
    out: list[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, str):
            raise CalendarError(
                f"flags.{key}[{i}] must be a string, got {type(item).__name__}"
            )
        cleaned = item.strip().upper()
        if not cleaned:
            raise CalendarError(
                f"flags.{key}[{i}] must be a non-empty state code"
            )
        out.append(cleaned)
    return out


def _strict_bool(d: dict, key: str, *, default: bool = False) -> bool:
    """Optional boolean field, strict.

    Rejects truthy strings ('yes', 'no', 'true', 'false', '0', '1') — those
    must NOT silently coerce to True/False because the flag value drives
    whether a trusted reminder fires. A retiree whose profile says
    `"subject_to_rmd_primary": "no"` should hard-error, not get a phantom RMD.
    Reuses the strict pattern established in loss_harvest._optional_bool.
    """
    if key not in d or d[key] is None:
        return default
    v = d[key]
    if not isinstance(v, bool):
        raise CalendarError(
            f"flags.{key} must be a JSON boolean (true/false), got {v!r}"
        )
    return v


# ---------------------------------------------------------------------------
# Per-category event builders
# ---------------------------------------------------------------------------

# Statutory 1040-ES dates; IRS weekend/holiday rollover NOT applied (see v1 caveats).
_ES_DUE = [
    ("Q1", (4, 15), 0),
    ("Q2", (6, 15), 0),
    ("Q3", (9, 15), 0),
    ("Q4", (1, 15), 1),
]


def events_1040es(tax_year: int) -> list[CalendarEvent]:
    """Always-included quarterly estimated-tax reminders."""
    _validate_tax_year(tax_year)
    out: list[CalendarEvent] = []
    for label, (m, d), year_offset in _ES_DUE:
        date = datetime.date(tax_year + year_offset, m, d)
        out.append(CalendarEvent(
            uid=f"1040es-{label.lower()}-{tax_year}@{_UID_NAMESPACE}",
            summary=f"1040-ES {label} estimated tax due (TY{tax_year})",
            date=date,
            description=(
                f"Quarterly federal estimated tax payment for tax year {tax_year}. "
                f"Pay via IRS Direct Pay, EFTPS, or Form 1040-ES voucher. Even if "
                f"you rely on W-2 withholding, use these checkpoints to confirm "
                f"you remain inside the safe harbor (90% of current year or "
                f"100%/110% of prior year)."
            ),
            category="federal_estimated_tax",
            quarter_label=label,
        ))
    return out


def events_fbar(tax_year: int) -> list[CalendarEvent]:
    """FBAR (FinCEN 114) reminders if taxpayer has foreign accounts.

    The FBAR for calendar year {tax_year} is filed in {tax_year + 1}. The
    statutory deadline is April 15 with an automatic 6-month extension to
    October 15 (no form needed).
    """
    _validate_tax_year(tax_year)
    filed_year = tax_year + 1
    apr = datetime.date(filed_year, 4, 15)
    octo = datetime.date(filed_year, 10, 15)
    return [
        CalendarEvent(
            uid=f"fbar-statutory-{tax_year}@{_UID_NAMESPACE}",
            summary=f"FBAR statutory deadline (TY{tax_year})",
            date=apr,
            description=(
                f"FinCEN Form 114 (FBAR) reporting foreign financial accounts "
                f"held during {tax_year}. Statutory due date — automatic "
                f"6-month extension applies; no extension form needed."
            ),
            category="fbar",
        ),
        CalendarEvent(
            uid=f"fbar-extended-{tax_year}@{_UID_NAMESPACE}",
            summary=f"FBAR final deadline — auto-extension (TY{tax_year})",
            date=octo,
            description=(
                f"Final deadline for FinCEN Form 114 (FBAR) for {tax_year} "
                f"under the automatic 6-month extension. Late filing exposes "
                f"the taxpayer to civil (and potentially criminal) penalties "
                f"under 31 U.S.C. §5321 — amounts are inflation-adjusted "
                f"annually and the willful vs non-willful regimes differ "
                f"substantially (per Bittner v. United States, 598 U.S. 274 "
                f"(2023), non-willful is per-form, not per-account). See "
                f"IRS.gov/fbar or consult a tax professional before filing "
                f"late."
            ),
            category="fbar",
        ),
    ]


def events_rmd(tax_year: int, *, primary_subject_to_rmd: bool,
               spouse_subject_to_rmd: bool) -> list[CalendarEvent]:
    """Required minimum distribution reminders.

    `subject_to_rmd_*` is a user-asserted boolean rather than a derived
    age check because RMD start age is birth-year-cohort-dependent under
    SECURE 2.0 (generally age 73 for those born 1951–1959; age 75 for
    those born 1960 or later — see CRS IF12750) and the calendar has no
    access to birth year. Inherited IRAs also have RMD obligations at
    any age. The taxpayer (advised by their preparer when needed)
    asserts whether RMDs are currently required.

    Single Dec 31 event per affected spouse. The first-year April-1
    deferral exists but is rarely advantageous (bunches two RMDs into
    one year); we don't model it in v1.
    """
    _validate_tax_year(tax_year)
    out: list[CalendarEvent] = []
    dec31 = datetime.date(tax_year, 12, 31)
    if primary_subject_to_rmd:
        out.append(CalendarEvent(
            uid=f"rmd-primary-{tax_year}@{_UID_NAMESPACE}",
            summary=f"RMD deadline — primary taxpayer (TY{tax_year})",
            date=dec31,
            description=(
                f"Required minimum distribution must be withdrawn from each "
                f"traditional IRA / 401(k) by Dec 31, {tax_year}. Failure to "
                f"take the RMD triggers a 25% excise tax (reduced to 10% if "
                f"corrected within 2 years)."
            ),
            category="rmd",
        ))
    if spouse_subject_to_rmd:
        out.append(CalendarEvent(
            uid=f"rmd-spouse-{tax_year}@{_UID_NAMESPACE}",
            summary=f"RMD deadline — spouse (TY{tax_year})",
            date=dec31,
            description=(
                f"Required minimum distribution for spouse's traditional "
                f"retirement accounts. Each individual computes their own "
                f"RMD; spouses cannot aggregate. Due Dec 31, {tax_year}."
            ),
            category="rmd",
        ))
    return out


def events_ptet(tax_year: int, *, state: str | None,
                has_k1_income: bool) -> list[CalendarEvent]:
    """State pass-through entity tax election reminders.

    Only CA, NY, NJ have non-trivial elective PTET regimes; per-state
    details are encoded inline below. `state` is validated as a string
    (or None) with whitespace stripped. On the production path through
    `build_events`, `state` has already been validated by
    `_strict_state_list` against `^[A-Z]{2}$`, so the inline strip + isinstance
    check here is belt-and-suspenders for direct callers and tests only.
    """
    _validate_tax_year(tax_year)
    if not has_k1_income or state is None:
        return []
    if not isinstance(state, str):
        raise CalendarError(
            f"address.state must be a string, got {type(state).__name__}"
        )
    code = state.strip().upper()
    if code not in _PTET_STATES:
        return []
    # Year-window gating: each state's PTE regime has an effective range.
    # Emitting reminders for a tax year outside the window would mislead
    # the user about an expired (or not-yet-enacted) regime — surface a
    # single info note instead.
    first_ty, last_ty = _PTET_WINDOWS[code]
    if tax_year < first_ty or (last_ty is not None and tax_year > last_ty):
        return [_ptet_year_out_of_range_note(tax_year, code, first_ty, last_ty)]
    out: list[CalendarEvent] = []
    if code == "CA":
        out.append(CalendarEvent(
            uid=f"ptet-ca-prepay-{tax_year}@{_UID_NAMESPACE}",
            summary=f"CA PTET June prepayment due (TY{tax_year})",
            date=datetime.date(tax_year, 6, 15),
            description=(
                "California pass-through entity elective tax prepayment "
                "(R&TC §19904(a)). Pay the GREATER of $1,000 or 50% of the "
                "prior-year PTE elective tax. Underpayment consequences: "
                "for TY2022–2025, missing the payment forfeits the PTE "
                "election entirely for the year; for TY2026–2030, the "
                "election still stands but the qualified PTE credit is "
                "reduced by 12.5% of the underpaid amount — either way a "
                "high-cost SALT-cap workaround loss."
            ),
            category="ptet",
        ))
        out.append(CalendarEvent(
            uid=f"ptet-ca-balance-{tax_year}@{_UID_NAMESPACE}",
            summary=f"CA PTET balance + election (TY{tax_year})",
            date=datetime.date(tax_year + 1, 3, 15),
            description=(
                "California PTE balance due and Form 3804 election filing "
                f"for tax year {tax_year}. PTE election is made annually on "
                "the timely-filed pass-through return."
            ),
            category="ptet",
        ))
    elif code == "NY":
        # NY PTET: election deadline AND Q1 estimated payment both fall on
        # March 15 of the tax year. Q2/Q3/Q4 estimates follow on June 15,
        # September 15, December 15 — note Q4 is DEC 15, not the federal
        # Jan-15-of-next-year date. Per NY Tax Department, each quarterly
        # installment must equal at least 25% of the required annual
        # payment.
        out.append(CalendarEvent(
            uid=f"ptet-ny-election-q1-{tax_year}@{_UID_NAMESPACE}",
            summary=f"NY PTET election + Q1 estimated payment (TY{tax_year})",
            date=datetime.date(tax_year, 3, 15),
            description=(
                f"New York pass-through entity tax election for tax year "
                f"{tax_year} AND Q1 estimated payment. Election must be "
                f"made by the original return due date; quarterly estimates "
                f"follow on June 15, September 15, and December 15. Each "
                f"installment must be at least 25% of the required annual "
                f"PTET. See tax.ny.gov/bus/ptet/ for current procedures."
            ),
            category="ptet",
        ))
        for quarter, month in (("Q2", 6), ("Q3", 9), ("Q4", 12)):
            out.append(CalendarEvent(
                uid=f"ptet-ny-{quarter.lower()}-{tax_year}@{_UID_NAMESPACE}",
                summary=f"NY PTET {quarter} estimated payment (TY{tax_year})",
                date=datetime.date(tax_year, month, 15),
                description=(
                    f"New York PTET {quarter} estimated payment for tax "
                    f"year {tax_year}. At least 25% of the required annual "
                    f"PTET — pay via the Tax Department's PTET Web File "
                    f"application by ACH debit."
                ),
                category="ptet",
            ))
        # Annual return + final payment: per NY Tax Department, the PTET
        # annual return is due March 15 after the close of the PTET
        # taxable year, and any unpaid PTET must be paid by then.
        out.append(CalendarEvent(
            uid=f"ptet-ny-annual-return-{tax_year}@{_UID_NAMESPACE}",
            summary=f"NY PTET annual return + final payment (TY{tax_year})",
            date=datetime.date(tax_year + 1, 3, 15),
            description=(
                f"New York PTET annual return for tax year {tax_year} is "
                f"due March 15, {tax_year + 1}. Any unpaid PTET must be "
                f"paid by this date — an extension of time to file is NOT "
                f"an extension of time to pay; penalties for unpaid tax "
                f"apply after the original due date."
            ),
            category="ptet",
        ))
    elif code == "NJ":
        # NJ BAIT: PTE-150 quarterly estimated payments follow the federal
        # individual Q1-Q4 schedule (Apr 15, Jun 15, Sep 15 of the tax year
        # + Jan 15 of the following year — Q4 lands in tax_year+1). The
        # ELECTION is filed with the PTE-100, due March 15 of the year
        # after the tax year (calendar-year entities). This is
        # statutorily different from NY, where the election is in-year by
        # March 15 of the tax year itself — do not consolidate the two.
        _NJ_ESTIMATES = (
            ("Q1", (4, 15), 0),
            ("Q2", (6, 15), 0),
            ("Q3", (9, 15), 0),
            ("Q4", (1, 15), 1),
        )
        for quarter, (month, day), year_offset in _NJ_ESTIMATES:
            out.append(CalendarEvent(
                uid=f"ptet-nj-{quarter.lower()}-{tax_year}@{_UID_NAMESPACE}",
                summary=f"NJ BAIT {quarter} estimated payment (TY{tax_year})",
                date=datetime.date(tax_year + year_offset, month, day),
                description=(
                    f"New Jersey BAIT {quarter} estimated payment for tax "
                    f"year {tax_year}. PTE-150 quarterly installment — at "
                    f"least 25% of the required annual BAIT. Pay "
                    f"electronically via the NJ PTE/BAIT payment system."
                ),
                category="ptet",
            ))
        out.append(CalendarEvent(
            uid=f"ptet-nj-election-{tax_year}@{_UID_NAMESPACE}",
            summary=f"NJ BAIT election deadline (TY{tax_year})",
            date=datetime.date(tax_year + 1, 3, 15),
            description=(
                f"New Jersey Business Alternative Income Tax (BAIT) election "
                f"for tax year {tax_year}. Election is made on a timely-filed "
                f"Form PTE-100, due March 15, {tax_year + 1} for a calendar-"
                f"year entity (no extension of time to elect — extensions "
                f"file via PTE-200-T but do not move the election deadline)."
            ),
            category="ptet",
        ))
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _info_note(tax_year: int, *, uid_slug: str, summary: str,
               description: str) -> CalendarEvent:
    """Shared constructor for calendar info-events.

    All notes land on Jan 1 of the tax year by convention so they sort
    to the top of the calendar's reading order. UID slug must already
    encode any disambiguator (state code, kind); this helper appends
    the tax_year and the project's UID namespace.
    """
    return CalendarEvent(
        uid=f"{uid_slug}-{tax_year}@{_UID_NAMESPACE}",
        summary=f"{summary} (TY{tax_year})",
        date=datetime.date(tax_year, 1, 1),
        description=description,
        category="note",
    )


def _ptet_not_modeled_note(tax_year: int, state: str) -> CalendarEvent:
    """Visible note when a K-1 filer's state is outside the modeled set.

    Silent `return []` is indistinguishable from "no K-1" once the .ics
    reaches the calendar app, so we surface a single info-event the user
    can see in their calendar.
    """
    code = state.upper()
    return _info_note(
        tax_year,
        uid_slug=f"note-ptet-not-modeled-{code}",
        summary=f"PTET reminders not modeled for state {code}",
        description=(
            f"You reported K-1 income with state {code}. v1 of the "
            f"calendar only models CA / NY / NJ pass-through entity "
            f"tax election windows. If your state has an elective PTE "
            f"regime, verify deadlines with the state tax authority "
            f"and add manual reminders."
        ),
    )


def _ptet_year_out_of_range_note(tax_year: int, state: str,
                                  first_ty: int,
                                  last_ty: "int | None") -> CalendarEvent:
    """Visible note when a tax year falls outside a state's PTE regime window.

    Per the state-specific window in `_PTET_WINDOWS`, emit a single info
    note rather than reminders for a regime that didn't exist yet or has
    expired. Future legislation may extend the window — the note tells
    the user to verify current law for their tax year.
    """
    if last_ty is None:
        window = f"valid for tax years {first_ty}+"
        reason = (
            f"Tax year {tax_year} is before {state}'s PTE regime was "
            f"enacted ({first_ty})."
        )
    elif tax_year > last_ty:
        window = f"valid TY{first_ty}–TY{last_ty}"
        reason = (
            f"Tax year {tax_year} is after {state}'s PTE regime sunset "
            f"({last_ty}). Future legislation may extend the regime — "
            f"verify current law before relying on these reminders."
        )
    else:
        window = f"valid TY{first_ty}–TY{last_ty}"
        reason = (
            f"Tax year {tax_year} is before {state}'s PTE regime was "
            f"enacted ({first_ty})."
        )
    return _info_note(
        tax_year,
        uid_slug=f"note-ptet-out-of-window-{state}",
        summary=f"PTET regime not applicable for {state}",
        description=(
            f"{reason} {state}'s elective PTE tax is {window}. "
            f"Calendar reminders skipped to avoid surfacing deadlines "
            f"for a regime that does not apply."
        ),
    )


def _k1_states_missing_note(tax_year: int) -> CalendarEvent:
    """Visible note when has_k1_income=True but flags.k1_states is empty.

    Without the entity state(s), the calendar cannot determine PTET
    deadlines (PTET is entity-state-specific, NOT taxpayer-resident-state-
    specific). Surface guidance rather than silently omitting all PTET
    reminders.
    """
    return _info_note(
        tax_year,
        uid_slug="note-k1-states-missing",
        summary="K-1 entity states not specified",
        description=(
            f"You reported K-1 income but `flags.k1_states` is empty. "
            f"PTET is an entity-level state tax — the partnership / "
            f"S-corp files in the state where it operates, NOT where "
            f"the partner resides — so the calendar needs the entity "
            f"state(s) to surface PTET deadlines. List every state "
            f"where your K-1 income originates in taxpayer_profile.json "
            f"under flags.k1_states (e.g. [\"CA\", \"NY\"]), then "
            f"re-run /tax-calendar."
        ),
    )


def build_events(profile: dict, tax_year: int) -> list[CalendarEvent]:
    """Assemble the full calendar from a taxpayer profile dict.

    Reads only the flags relevant to deadline emission; missing blocks degrade
    to "feature off" so a partially-filled profile still produces a useful
    1040-ES skeleton. Malformed blocks (wrong JSON type) hard-error rather
    than silently degrading.

    Returns events sorted by `(date, uid)` with UID uniqueness enforced — both
    are load-bearing invariants for `to_ics` (byte-stable output, RFC 5545
    UID-as-identity-key) and for any downstream diffing.
    """
    _validate_tax_year(tax_year)

    if not isinstance(profile, dict):
        raise CalendarError(
            f"taxpayer_profile must be a JSON object, got "
            f"{type(profile).__name__}"
        )

    profile_year = profile.get("tax_year")
    if profile_year is not None and profile_year != tax_year:
        raise CalendarError(
            f"taxpayer_profile.json is for TY{profile_year}, not TY{tax_year}."
        )

    # Each "missing-OK" block: split absence (key missing OR explicit null)
    # from type errors. `or {}` short-circuits on ANY falsy value, including
    # `[]` / `""` / `0` / `False`, which silently substitutes {} BEFORE the
    # isinstance check sees the corruption and drops conditional events
    # (FBAR, RMD, PTET) without warning.
    flags = profile.get("flags")
    if flags is None:
        flags = {}
    elif not isinstance(flags, dict):
        raise CalendarError(
            f"profile.flags must be a JSON object or null, "
            f"got {type(flags).__name__}"
        )
    address = profile.get("address")
    if address is None:
        address = {}
    elif not isinstance(address, dict):
        raise CalendarError(
            f"profile.address must be a JSON object or null, "
            f"got {type(address).__name__}"
        )
    # NOTE: address.state is NOT used for PTET emission. PTET is an
    # entity-level state tax (paid by the partnership / S-corp at filing
    # time), so PTET deadlines are entity-state-specific, not
    # taxpayer-resident-state-specific — a TX resident with a CA K-1 needs
    # CA PTET reminders, not TX. The K-1 entity state(s) live in
    # `flags.k1_states`.
    events: list[CalendarEvent] = []
    events.extend(events_1040es(tax_year))

    if _strict_bool(flags, "has_foreign_accounts"):
        events.extend(events_fbar(tax_year))

    events.extend(events_rmd(
        tax_year,
        primary_subject_to_rmd=_strict_bool(flags, "subject_to_rmd_primary"),
        spouse_subject_to_rmd=_strict_bool(flags, "subject_to_rmd_spouse"),
    ))

    has_k1 = _strict_bool(flags, "has_k1_income")

    # has_k1_income gates both EMISSION and VALIDATION of k1_states: a
    # stale or hand-edited k1_states value (e.g. left behind after the
    # taxpayer sold the partnership and corrected the K-1 flag without
    # clearing the state list) must not block the 1040-ES calendar when
    # K-1 income is off. Defer parsing until inside the gate.
    if has_k1:
        k1_states = _strict_state_list(flags, "k1_states")
        if not k1_states:
            # K-1 income reported but no entity states listed — cannot
            # determine PTET deadlines, so emit a visible note rather
            # than silently skipping (silence is indistinguishable from
            # "no K-1" once the .ics reaches the calendar app).
            events.append(_k1_states_missing_note(tax_year))
        else:
            for k1_state in k1_states:
                ptet = events_ptet(
                    tax_year, state=k1_state, has_k1_income=True
                )
                if ptet:
                    events.extend(ptet)
                else:
                    events.append(_ptet_not_modeled_note(tax_year, k1_state))

    events.sort(key=lambda e: (e.date, e.uid))

    # Defensive: UID collisions silently merge events on import (RFC 5545
    # identity key). Cheap invariant catches future-builder mistakes.
    uids = [e.uid for e in events]
    if len(set(uids)) != len(uids):
        from collections import Counter
        dups = sorted(u for u, c in Counter(uids).items() if c > 1)
        raise CalendarError(f"duplicate UIDs in generated events: {dups}")
    return events


# ---------------------------------------------------------------------------
# RFC 5545 serialization
# ---------------------------------------------------------------------------

def _escape_text(text: str) -> str:
    """Backslash-escape RFC 5545 §3.3.11 TEXT specials.

    Order matters: backslash MUST be escaped first; otherwise the `\\;`/`\\,`
    introduced by the later replacements would themselves get double-escaped.
    Both CR and LF map to the same `\\n` literal — RFC 5545 says line breaks
    in TEXT encode as `\\n` regardless of whether the source used CR, LF,
    or CRLF, and collapsing CRLF first prevents a duplicate `\\n\\n`.
    """
    return (text
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r\n", "\\n")
            .replace("\r", "\\n")
            .replace("\n", "\\n"))


def _fold_line(line: str) -> str:
    """RFC 5545 §3.1 line folding — splits at UTF-8 codepoint boundaries.

    Spec: max 75 octets per physical line, continuation lines begin with a
    single SP. Splitting at raw byte offsets risks tearing multi-byte
    characters; we walk codepoints and break before the codepoint that would
    overflow the current physical line's octet budget.
    """
    if len(line.encode("utf-8")) <= 75:
        return line

    parts: list[str] = []
    buf = ""
    buf_bytes = 0
    limit = 75  # first physical line has no leading SP

    for ch in line:
        ch_bytes = len(ch.encode("utf-8"))
        if buf_bytes + ch_bytes > limit:
            parts.append(buf)
            buf = ch
            buf_bytes = ch_bytes
            limit = 74  # continuation lines reserve one octet for the leading SP
        else:
            buf += ch
            buf_bytes += ch_bytes
    if buf:
        parts.append(buf)

    return parts[0] + "".join("\r\n " + p for p in parts[1:])


def to_ics(events: list[CalendarEvent]) -> str:
    """Serialize events to RFC 5545 VCALENDAR string with CRLF line endings.

    No METHOD property is emitted — the file is consumed as a one-shot import,
    not as a PUBLISH/REQUEST feed, so dropping METHOD lets the frozen DTSTAMP
    stay spec-clean (METHOD:PUBLISH would require DTSTAMP be the actual
    creation time per §3.7.2).
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
    ]
    # Stable DTSTAMP derived from the earliest event year so reruns with the
    # same inputs produce byte-identical output AND a TY2050 calendar isn't
    # stamped with a 2026 date 24 years in the past. Empty event list falls
    # back to a fixed sentinel.
    if events:
        stamp_year = min(e.date.year for e in events)
    else:
        stamp_year = 2026
    dtstamp = f"{stamp_year:04d}0101T000000Z"
    for e in events:
        date_str = e.date.strftime("%Y%m%d")
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{e.uid}")
        lines.append(f"DTSTAMP:{dtstamp}")
        lines.append(f"DTSTART;VALUE=DATE:{date_str}")
        lines.append(f"SUMMARY:{_escape_text(e.summary)}")
        lines.append(f"DESCRIPTION:{_escape_text(e.description)}")
        lines.append(f"CATEGORIES:{e.category.upper()}")
        # 7-day-prior alarm so the taxpayer has time to act.
        lines.append("BEGIN:VALARM")
        lines.append("ACTION:DISPLAY")
        lines.append(f"DESCRIPTION:{_escape_text(e.summary)} in 7 days")
        lines.append("TRIGGER:-P7D")
        lines.append("END:VALARM")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold_line(line) for line in lines) + "\r\n"


# ---------------------------------------------------------------------------
# File writer (workspace-bound)
# ---------------------------------------------------------------------------

def write_calendar(profile_path: Path, workspace: Path, *, tax_year: int) -> Path:
    """Read profile, build events, write tax_calendar_{year}.ics under workspace.

    Refuses to read a profile or write the .ics outside the workspace boundary
    (reuses rollover._validate_subpath, the same gate used by every other
    derived-artifact writer in this toolkit). Writes atomically via a sibling
    `.tmp` + os.replace so a disk-full mid-write cannot leave a truncated
    `.ics` that calendar apps would import leniently.
    """
    _validate_tax_year(tax_year)
    workspace = workspace.resolve()
    try:
        profile_path = _validate_subpath(profile_path.resolve(), workspace)
    except RolloverError as e:
        raise CalendarError(str(e)) from e
    if not profile_path.is_file():
        raise CalendarError(f"taxpayer_profile not found at {profile_path}")
    try:
        profile = json.loads(profile_path.read_text())
    except json.JSONDecodeError as e:
        raise CalendarError(f"taxpayer_profile is not valid JSON: {e}") from e
    except UnicodeDecodeError as e:
        raise CalendarError(
            f"taxpayer_profile is not valid UTF-8 text: {e}"
        ) from e
    except OSError as e:  # PermissionError, IsADirectoryError, generic OSError
        raise CalendarError(f"could not read {profile_path}: {e}") from e

    events = build_events(profile, tax_year)
    ics = to_ics(events)
    try:
        out = _validate_subpath(workspace / f"tax_calendar_{tax_year}.ics", workspace)
    except RolloverError as e:
        raise CalendarError(str(e)) from e
    # Notify on overwrite so a user re-running after a flag change knows the
    # previous .ics they may have imported into a calendar app is being
    # replaced (calendar apps that already imported the old file will retain
    # orphan events whose UIDs aren't in the new file).
    if out.exists():
        print(f"Replacing existing {out.name}", file=sys.stderr)
    tmp = out.with_suffix(out.suffix + ".tmp")
    try:
        # Write bytes, not text, so the precise CRLF line endings produced
        # by to_ics() are preserved verbatim. Path.write_text() uses
        # text-mode I/O and translates `\n` to os.linesep on write, which
        # on Windows would turn our existing `\r\n` into `\r\r\n` —
        # malformed iCalendar. This bug is invisible on platforms where
        # `os.linesep == '\n'` (Linux, macOS), so cross-platform tests
        # assert against bytes-on-disk rather than text.
        tmp.write_bytes(ics.encode("utf-8"))
        os.chmod(tmp, 0o600)
        os.replace(tmp, out)  # atomic on POSIX
    except OSError as e:
        # Best-effort cleanup of the temp file before surfacing the error.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise CalendarError(f"failed to write {out}: {e}") from e
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agents.lib.tax_calendar")
    parser.add_argument("--profile", required=True, help="path to taxpayer_profile.json")
    parser.add_argument("--workspace", required=True, help="current workspace directory")
    parser.add_argument("--year", required=True, help="tax year (YYYY)")
    args = parser.parse_args(argv)

    try:
        year = _validate_year_arg(args.year)
        if not args.workspace.strip():
            raise CalendarError("--workspace must be a non-empty path")
        workspace = Path(args.workspace).resolve()
        if not workspace.is_dir():
            raise CalendarError(f"workspace does not exist: {workspace}")
        out = write_calendar(Path(args.profile), workspace, tax_year=year)
    except (CalendarError, RolloverError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
