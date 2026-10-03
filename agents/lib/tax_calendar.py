#!/usr/bin/env python3
"""Quarterly reminder calendar for /tax-setup (Backlog #10).

Generates an RFC 5545 iCalendar (.ics) file with year-specific tax deadlines:
1040-ES quarterly estimated-tax payments, CA 540-ES installments for CA
residents (30%/40%/0%/30% weighting — NO September payment; E7), NY IT-2105
installments for NY residents (four EQUAL quarters, one voucher covering the
State tax, the City tax, the Yonkers surcharge and the MCTMT), extension
deadlines when `flags.filed_extension` is set by /tax-extend (Form 4868
April pay-by + October extended filing, F2), FBAR (with
auto-extension), required minimum distributions for taxpayers flagged
`subject_to_rmd_*` (RMD start age is birth-year-cohort-dependent under
SECURE 2.0; the user asserts it rather than the calendar deriving it from
age), and state PTET windows for CA/NY/NJ K-1 recipients (driven by
`flags.k1_states`, which lists each K-1 entity's state, NOT the taxpayer's
resident state — the 540-ES and IT-2105 events, by contrast, follow
`address.state`).

Read-only with respect to result.json / taxpayer_profile.json — this module only
WRITES a derived `tax_calendar_{year}.ics` sibling. It mirrors the
"pure compute lib + CLI + reused workspace boundary check" pattern of
projection / withholding.py / scenario.py.

Caveats:
- §7503 next-business-day rollover IS applied (L1). FEDERAL deadlines
  (1040-ES, extension, FBAR) roll past weekends AND DC legal holidays (incl.
  DC Emancipation Day) via irs_dates.next_business_day; the `.ics` DTSTART is
  the observed date and the description notes the statutory-vs-observed shift.
  STATE deadlines (CA 540-ES, NY IT-2105, CA/NY/NJ PTET) roll under THAT state's own
  holiday set via irs_dates.next_business_day_state. California's INCLUDES the
  federal set — R&TC §18410 folds it in by reference and FTB applies it by name
  in the 540-ES instructions — so a CA date rolls past DC Emancipation Day and
  MLK Day exactly as a federal one does. CA's own Gov Code §6700 holidays are
  the half not modeled, and no date emitted here can reach one (asserted in
  tests/test_irs_dates.py, not assumed). NY and NJ roll past weekends only,
  as a labelled DEGRADE rather than a claim about their law (their statutes
  have not been read here), and only THEY carry the "state-specific legal
  holidays are not modeled" caveat in the description. RMD Dec 31 does NOT
  roll (a distribution deadline, not a §7503 file/pay act), and FL county
  property dates deliberately keep the statutory date (see
  _fl_county_observed_note).
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
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.capabilities import _USPS_STATES  # noqa: E402
from agents.lib.irs_dates import (  # noqa: E402
    next_business_day,
    next_business_day_state,
    state_conforms_to_federal_holidays as _state_conforms_to_federal_holidays,
)
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_subpath,
    _validate_year_arg,
)
from agents.lib.tax_computations.state.registry import (  # noqa: E402
    PTET_STATES,
    PTET_WINDOWS,
    ptet_window_active,
)
from agents.lib.secure_fs import (  # noqa: E402
    atomic_write,
    redact, year_or_shape,
)
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    resolve_under_workspace,
    resolve_workspace,
)


class CalendarError(Exception):
    """Raised when calendar generation cannot safely proceed."""


# ---------------------------------------------------------------------------
# §7503 next-business-day rollover (L1)
# ---------------------------------------------------------------------------
# A deadline that lands on a weekend or legal holiday is timely on the next
# business day (§7503). FEDERAL deadlines use the shared next_business_day
# helper (weekends + DC legal holidays, incl. DC Emancipation Day). STATE
# deadlines use next_business_day_state, which resolves the holiday set from
# the state code it REQUIRES. This block used to give every state the
# weekend-only roll, on the premise that a state tax authority ignores the DC
# holiday set entirely. For the one state modeled in depth that is false — R&TC
# §18410 folds the federal set into California's, which is why CA dates were
# being published up to three days EARLY. A state whose statute is unread
# here (NY, NJ) keeps the weekend-only roll as a labelled degrade. RMD Dec 31
# does NOT roll (a distribution deadline, not a §7503 file/pay act). The `.ics` DTSTART carries the OBSERVED date so the reminder
# fires when the taxpayer can actually act; descriptions keep the statutory
# date and note the observed shift when they differ.

def _fed_due(year: int, month: int, day: int) -> datetime.date:
    """Statutory federal date rolled to the next business day (§7503)."""
    return next_business_day(datetime.date(year, month, day))


def _state_due(year: int, month: int, day: int, state: str) -> datetime.date:
    """A state deadline rolled under THAT state's holiday rule.

    `state` is required and has no default. Every date this module emits
    belongs to a named jurisdiction, and the rule differs between them:
    California adopts the federal legal-holiday set by statute (R&TC §18410,
    which FTB's own 540-ES instructions apply by name), while NY and NJ have
    not been checked here and keep the weekend-only degrade. Passing no state
    is how one rule came to stand for all three and publish CA dates up to
    three days early.

    Delegates to `irs_dates.next_business_day_state`, which is where the rule
    lives now that /tax-project's CA 540-ES vouchers need it too.
    """
    return next_business_day_state(datetime.date(year, month, day), state)


def _observed_note(statutory: datetime.date, observed: datetime.date,
                   *, state: "str | None" = None) -> str:
    """A description suffix naming the observed shift, empty when none.

    `state` is the jurisdiction's code, or None for a federal deadline. It
    used to be a BOOLEAN, and the caveat it gated — "state-specific legal
    holidays are not modeled" — was appended to every state event alike. It is
    an instruction to go and confirm the date, and for California there is
    nothing left to confirm: R&TC §18410 folds the federal legal-holiday set
    into CA's, and every CA date emitted here matches FTB's own published one.
    A caveat telling a Californian to check a date this engine already gets
    right is the lying caveat this repo would rather have missing, and it
    teaches the reader to discount the same sentence where it is still live —
    on NY and NJ, whose statutes have not been read here.

    Suppressed on REACHABILITY, not on CA having no holidays of its own. It
    has four the federal calendar does not (§6700), unmodeled here; no date
    this module emits can land on or roll across one, which
    `test_irs_dates.py` re-derives from the call sites, in
    `test_no_california_deadline_can_reach_a_state_only_holiday`. The day a
    CA deadline CAN reach one, that
    test fails and this suppression stops being true — which is the point of
    asserting it somewhere rather than believing it here.
    """
    if observed == statutory:
        return ""
    note = (f" Note: the statutory {statutory.strftime('%B')} {statutory.day} "
            f"date is a {statutory.strftime('%A')}; the deadline is observed "
            f"{observed.strftime('%B')} {observed.day}, {observed.year} (next "
            f"business day, §7503).")
    if state and not _state_conforms_to_federal_holidays(state):
        note += (" State-specific legal holidays are not modeled — confirm the "
                 "exact date with the state tax authority.")
    return note


def _fl_county_observed_note(statutory: datetime.date) -> str:
    """A description suffix for FL COUNTY property deadlines (DR-501/DR-405)
    whose statutory date falls on a weekend.

    Unlike _observed_note, this does NOT cite §7503 — that IRC section
    governs FEDERAL filing/paying acts, not county property-appraiser
    deadlines, and inventing an analogous Florida statutory citation for a
    weekend-roll convention would be an unverified procedural claim (a lying
    caveat is worse than a missing one). And unlike every OTHER state event
    in this module, the event DATE is deliberately KEPT on the statutory
    weekend date rather than rolled to Monday (Codex round-19 P2): no
    governing statewide Florida rollover rule has been verified for county
    property filings — only the observation that "most" county offices
    accept the next business day — and an alert moved to Monday would have
    a taxpayer in a county that does NOT grant the informal rollover file a
    day late. Rolling a deadline on unverified authority extends it in the
    harmful direction; keeping the statutory date is at worst one day
    early. Scoped to events_fl_property only — federal events and the
    CA/NY/NJ PTET events keep using _observed_note."""
    if statutory.weekday() < 5:
        return ""
    next_bd = statutory + datetime.timedelta(days=1)
    while next_bd.weekday() >= 5:
        next_bd += datetime.timedelta(days=1)
    return (
        f" Note: the statutory {statutory.strftime('%B')} {statutory.day} "
        f"date is a {statutory.strftime('%A')}. This reminder is "
        f"deliberately kept ON the statutory date: most Florida county "
        f"offices observe the next business day "
        f"({next_bd.strftime('%B')} {next_bd.day}, {next_bd.year}) for a "
        f"weekend deadline, but no governing rollover rule has been "
        f"verified, so do not rely on the extra day(s) — confirm with your "
        f"county property appraiser.")


# PRODID identifies this tool to calendar clients; the trailing version tracks
# event-shape compatibility — bump when adding new categories or breaking the
# UID slug format so calendar apps treat the file as a new feed.
# v2: added the state_estimated_tax category (CA 540-ES events, E7).
# v3: added the extension category (/tax-extend, F2).
# v4: §7503 next-business-day rollover applied to event dates (L1) — federal
#     deadlines roll past weekends + DC holidays, state deadlines past weekends.
# v5: added the state_property category (FL homestead DR-501 + DR-405 property
#     deadlines; these county events stay ON their statutory dates even on a
#     weekend — no verified FL rollover rule, see _fl_county_observed_note).
_PRODID = "-//tax-prep-skills//tax-calendar v5//EN"
_UID_NAMESPACE = "tax-prep-skills"
_VALID_CATEGORIES = frozenset({"federal_estimated_tax", "state_estimated_tax",
                               "extension", "fbar", "rmd", "ptet",
                               "state_property", "note"})
_MIN_YEAR = 2000
_MAX_YEAR = 2099
# The DR-405 $25,000 tangible-personal-property exemption (F.S. 196.183) took
# effect for the 2008 tax year (Fla. Amendment 1, approved Jan 2008) — it is
# NOT a feature of DR-405 going back to _MIN_YEAR (2000). A pre-2008
# events_fl_property description must not promise an exemption that didn't
# exist yet (round-4 finding D); it keeps the filing deadline + penalties.
_FL_TPP_EXEMPTION_YEAR = 2008

# Untrusted strings from taxpayer_profile.json can flow into RFC 5545 fields
# (UID/SUMMARY/DESCRIPTION). Validate shape at the parse boundary AND escape
# at the serialization boundary — defense in depth.
_STATE_CODE_RE = re.compile(r"^[A-Z]{2}$")
# RFC 5545 §3.8.4.7: UIDs are TEXT but line-folding rules mean CR/LF/NUL
# embedded in a UID would tear the format apart. We also forbid any other
# C0 control character (\x00-\x1f) plus DEL (\x7f) as defensive hygiene.
_UID_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

Category = Literal["federal_estimated_tax", "state_estimated_tax",
                   "extension", "fbar", "rmd", "ptet",
                   "state_property", "note"]

# Categories that carry a per-quarter label (Q1..Q4).
_ESTIMATED_TAX_CATEGORIES = frozenset({"federal_estimated_tax",
                                       "state_estimated_tax"})


@dataclass(frozen=True)
class CalendarEvent:
    """A single all-day reminder.

    Invariants enforced at construction:
    - `category` must be one of the known categories
    - `quarter_label` is non-empty iff the category is an estimated-tax one
      (`federal_estimated_tax` / `state_estimated_tax`)
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
        is_estimated = self.category in _ESTIMATED_TAX_CATEGORIES
        has_label = bool(self.quarter_label)
        if is_estimated and not has_label:
            raise CalendarError(
                "estimated-tax events require a quarter_label (Q1..Q4)")
        if not is_estimated and has_label:
            raise CalendarError(
                f"quarter_label only valid on estimated-tax events, "
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
                f"(e.g. 'CA', 'NY'), got {redact(repr(code))}"
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


def _strict_state_str(d: dict, key: str) -> "str | None":
    """Optional single state-code field (address.state), strict.

    Returns None when missing, explicitly null, or an empty/whitespace
    string — all scaffolding shapes an interview may leave behind; an
    incomplete profile still gets the federal calendar. A non-string,
    non-2-letter, or non-USPS value hard-errors: "California" (or a stray
    "ZZ"/Canadian "BC") silently producing a federal-only calendar would
    drop the CA-ES reminders without warning — the same silent-miss failure
    mode retirement_dashboard guards address.state against, and the same
    USPS whitelist capabilities.py uses (two letters alone is not a state).
    """
    if key not in d or d[key] is None:
        return None
    v = d[key]
    if not isinstance(v, str):
        raise CalendarError(
            f"address.{key} must be a string, got {type(v).__name__}"
        )
    code = v.strip().upper()
    if not code:
        return None
    if not _STATE_CODE_RE.match(code) or code not in _USPS_STATES:
        raise CalendarError(
            f"address.{key} must be a 2-letter USPS state/territory code "
            f"(e.g. 'CA', 'NY'), got {redact(repr(v))} — fix "
            f"taxpayer_profile.json "
            f"(the calendar refuses to guess and silently drop state "
            f"reminders)"
        )
    return code


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
            f"flags.{key} must be a JSON boolean (true/false), "
            f"got {redact(repr(v))}"
        )
    return v


# ---------------------------------------------------------------------------
# Per-category event builders
# ---------------------------------------------------------------------------

# Statutory 1040-ES dates; the §7503 next-business-day rollover (federal) is
# applied via _fed_due (L1).
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
        statutory = datetime.date(tax_year + year_offset, m, d)
        date = _fed_due(tax_year + year_offset, m, d)
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
                + _observed_note(statutory, date)
            ),
            category="federal_estimated_tax",
            quarter_label=label,
        ))
    return out


# CA 540-ES installments (R&TC §19136.1; FTB Form 540-ES): 30% / 40% / 0% /
# 30% — there is NO September (Q3) installment; the final 30% is due Jan 15
# of the following year. Weights are statutory-structural (set in statute,
# not indexed), so they're hardcoded like the federal quarter dates above.
_CA_ES_DUE = [
    ("Q1", (4, 15), 0, "30%"),
    ("Q2", (6, 15), 0, "40%"),
    ("Q4", (1, 15), 1, "30%"),
]


# NY IT-2105 installments (Tax Law §685(c); Form IT-2105): FOUR EQUAL
# quarters — April 15, June 15, September 15, and January 15 of the following
# year. The same dates as the federal 1040-ES and NOT California's 30/40/0/30,
# which has no September instalment at all. The shares are statutory-structural
# like the federal quarters above; the SAFE HARBOUR percentages are fetched and
# live in the tax-law cache, so none appears here.
_EQUAL_STATE_ES_DUE = [
    ("Q1", (4, 15), 0),
    ("Q2", (6, 15), 0),
    ("Q3", (9, 15), 0),
    ("Q4", (1, 15), 1),
]


def events_ny_es(tax_year: int) -> list[CalendarEvent]:
    """IT-2105 reminders, emitted for New York residents (address.state).

    ONE VOUCHER COVERS FOUR TAXES — the New York State tax, the New York City
    resident tax, the Yonkers surcharge and the MCTMT — which is the fact a
    reminder has to carry: a City resident who pays the State amount alone on
    these dates has under-paid, and nothing on the voucher would tell them so.

    Four EQUAL quarters. The September instalment is stated explicitly because
    the sibling California events say there is none, and a reader who has seen
    that rule should not carry it across.

    The dates roll WEEKENDS ONLY, because New York is not in
    `irs_dates`'s conforming set — General Construction Law §§24 and 25-a have
    not been read against the Department's published due dates here — so a date
    may be a day or two earlier than the one New York observes. That is the
    safe direction for a payment; `_observed_note` says so on the event.
    """
    _validate_tax_year(tax_year)
    out: list[CalendarEvent] = []
    for label, (m, d), year_offset in _EQUAL_STATE_ES_DUE:
        statutory = datetime.date(tax_year + year_offset, m, d)
        date = _state_due(tax_year + year_offset, m, d, "NY")
        out.append(CalendarEvent(
            uid=f"ny2105-{label.lower()}-{tax_year}@{_UID_NAMESPACE}",
            summary=f"NY IT-2105 {label} estimated tax due — 25% (TY{tax_year})",
            date=date,
            description=(
                f"New York estimated tax installment for tax year "
                f"{tax_year}: 25% of the required annual payment. Four EQUAL "
                f"quarters — April, June, SEPTEMBER and the following "
                f"January — unlike California's 30%/40%/0%/30%. ONE VOUCHER "
                f"COVERS the New York State tax, the New York City resident "
                f"tax, the Yonkers surcharge and the metropolitan commuter "
                f"transportation mobility tax together: paying the State "
                f"amount alone under-pays a City resident. The safe harbour "
                f"is a share of the current year or of the prior year, and "
                f"the higher prior-year share is keyed to NEW YORK adjusted "
                f"gross income, not federal (Tax Law §685(c)) — the "
                f"percentages and thresholds are fetched, so run "
                f"/tax-withholding for this year's figures rather than "
                f"assuming the federal ones."
                + _observed_note(statutory, date, state="NY")
            ),
            category="state_estimated_tax",
            quarter_label=label,
        ))
    return out


def events_nc_es(tax_year: int) -> list[CalendarEvent]:
    """NC-40 equal quarters, G.S. 105-163.15(c)-(d).

    Holiday dates use the conservative state calendar; its caveat remains
    visible until NC's holiday set has been implemented and verified.
    """
    _validate_tax_year(tax_year)
    out: list[CalendarEvent] = []
    for label, (m, d), offset in _EQUAL_STATE_ES_DUE:
        statutory = datetime.date(tax_year + offset, m, d)
        date = _state_due(tax_year + offset, m, d, "NC")
        out.append(CalendarEvent(
            uid=f"nc40-{label.lower()}-{tax_year}@{_UID_NAMESPACE}",
            summary=f"NC-40 {label} estimated tax due — equal quarter (TY{tax_year})",
            date=date,
            description=(
                f"North Carolina Form NC-40 for TY{tax_year}: four equal "
                "quarterly installments under G.S. 105-163.15. Run "
                "/tax-withholding using the year's fetched NC shares and "
                "threshold; federal high-income percentages do not apply. "
                "The January installment may be skipped if the return is filed "
                "and fully paid by January 31. Qualifying farmers/fishermen "
                "use the separate January/March rules; these general reminders "
                "do not determine their installments."
                + _observed_note(statutory, date, state="NC")),
            category="state_estimated_tax", quarter_label=label,
        ))
    return out


def events_ca_es(tax_year: int) -> list[CalendarEvent]:
    """CA 540-ES reminders, emitted for California residents (address.state).

    California's estimated-tax installments are NOT four equal quarters:
    30% is due April 15, 40% June 15, NOTHING in September, and the final
    30% on January 15 of the following year. Only three events are emitted —
    the September gap is called out in each description so a taxpayer acting
    on the federal Q3 reminder doesn't assume a CA payment is due too.
    """
    _validate_tax_year(tax_year)
    out: list[CalendarEvent] = []
    for label, (m, d), year_offset, pct in _CA_ES_DUE:
        statutory = datetime.date(tax_year + year_offset, m, d)
        date = _state_due(tax_year + year_offset, m, d, "CA")
        out.append(CalendarEvent(
            uid=f"ca540es-{label.lower()}-{tax_year}@{_UID_NAMESPACE}",
            summary=f"CA 540-ES {label} estimated tax due — {pct} (TY{tax_year})",
            date=date,
            description=(
                f"California estimated tax installment for tax year "
                f"{tax_year}: {pct} of the required annual payment. CA "
                f"weighting is 30%/40%/0%/30% — there is NO September "
                f"installment, unlike federal. Safe harbor is 90% of "
                f"current-year or 100%/110% of prior-year CA tax, EXCEPT "
                f"taxpayers with current-year CA AGI of $1,000,000 or more "
                f"($500,000 MFS) must use 90% of current year "
                f"(R&TC §19136.3). Pay via FTB Web Pay — mandatory e-pay "
                f"may apply once payments exceed $20,000 or total tax "
                f"exceeds $80,000 (R&TC §19011.5)."
                + _observed_note(statutory, date, state="CA")
            ),
            category="state_estimated_tax",
            quarter_label=label,
        ))
    return out


def events_extension(tax_year: int, *, state: "str | None" = None) -> list[CalendarEvent]:
    """Extension deadlines for a taxpayer who filed (or will file) Form 4868.

    Both events land in {tax_year + 1} — the filing season for tax_year.
    The April event is the one that matters financially: the extension
    extends the time to FILE, never the time to PAY — unpaid tax accrues
    interest plus the 0.5%/month late-payment penalty from April 15 even
    with a valid extension. For CA residents the April description carries
    the FTB 3519 nuance: CA's extension is AUTOMATIC (no form), but any CA
    balance due must be PAID by April 15 via FTB 3519 / Web Pay.

    For NY residents it carries the opposite nuance, and it is the one worth
    carrying: the federal Form 4868 does NOT extend a New York return.
    Form IT-370-I says so in as many words — "Do not submit a copy of the
    federal extension form. We will not accept it in place of Form IT-370 and
    your due date will not be extended" — so a taxpayer who extends federally
    and assumes New York follows has filed LATE, at 5% of the tax due per month
    up to 25%. Payment is a CONDITION of that extension rather than merely a
    way to avoid interest: "you must make full payment of the properly
    estimated tax balances due."
    """
    _validate_tax_year(tax_year)
    filing_year = tax_year + 1
    ca = (state == "CA")
    ny = (state == "NY")
    apr_desc = (
        f"Form 4868 for tax year {tax_year} must be filed AND the estimated "
        f"balance due PAID by this date — the extension extends the time to "
        f"file, NOT the time to pay; unpaid tax accrues interest plus the "
        f"0.5%/month late-payment penalty from today even with a valid "
        f"extension. Pay via IRS Direct Pay/EFTPS (an extension payment "
        f"marked 'Form 4868' files the extension automatically)."
    )
    if ca:
        apr_desc += (
            " California: the state extension is AUTOMATIC — no form — but "
            "any CA balance due must be paid TODAY via FTB Form 3519 (Web "
            "Pay; mandatory e-pay may apply per R&TC §19011.5)."
        )
    if ny:
        apr_desc += (
            " New York: the federal Form 4868 does NOT extend the New York "
            "return — the Department will not accept a copy of it and the due "
            "date is not extended. File Form IT-370 TODAY, WITH the payment: "
            "full payment of the properly estimated balance is a CONDITION of "
            "the extension, and an IT-370 filed after today is not accepted at "
            "all. The balance is IT-201 line 80, which covers the State tax, "
            "the New York City tax, the Yonkers surcharge and the MCTMT "
            "together. Filing IT-370 also extends Form Y-203."
        )
    if ca:
        extended_state_form = " and CA Form 540"
    elif ny:
        extended_state_form = " and NY Form IT-201"
    elif state == "WA":
        apr_desc += (" Washington capital-gains excise: pay by the original deadline and request a WA extension separately through My DOR, with a federal extension. A federal extension alone does NOT extend WA. No required quarterly estimates.")
        extended_state_form = " and WA capital-gains excise ONLY with a separately approved My DOR extension"
    elif state == "NC":
        apr_desc += (
            " North Carolina Form D-400: an automatic federal extension also "
            "extends NC ONLY if the Federal Extension circle is filled on "
            "D-400. Without a federal extension, file Form D-410 by the "
            "original deadline. Neither extends payment; pay the NC balance "
            "by the original due date. Confirm NC's observed date with NCDOR.")
        extended_state_form = " and NC Form D-400 (with a valid federal or D-410 extension)"
    else:
        extended_state_form = ""
    oct_desc = (
        f"Extended filing deadline for the tax year {tax_year} return "
        f"(federal Form 1040{extended_state_form}). No further "
        f"extension is available — file by today; with a valid extension "
        f"the late-FILING penalty (5%/month, up to 25%, offset by any "
        f"concurrent 0.5% late-payment penalty per §6651(c)(1)) BEGINS "
        f"after this date."
    )
    apr_stat, apr_obs = datetime.date(filing_year, 4, 15), _fed_due(filing_year, 4, 15)
    oct_stat, oct_obs = datetime.date(filing_year, 10, 15), _fed_due(filing_year, 10, 15)
    return [
        CalendarEvent(
            uid=f"extension-pay-{tax_year}@{_UID_NAMESPACE}",
            summary=f"Form 4868 + extension payment due (TY{tax_year})",
            date=apr_obs,
            description=apr_desc + _observed_note(apr_stat, apr_obs),
            category="extension",
        ),
        CalendarEvent(
            uid=f"extension-file-{tax_year}@{_UID_NAMESPACE}",
            summary=f"EXTENDED filing deadline (TY{tax_year})",
            date=oct_obs,
            description=oct_desc + _observed_note(oct_stat, oct_obs),
            category="extension",
        ),
    ]


def events_fbar(tax_year: int) -> list[CalendarEvent]:
    """FBAR (FinCEN 114) reminders if taxpayer has foreign accounts.

    The FBAR for calendar year {tax_year} is filed in {tax_year + 1}. The
    statutory deadline is April 15 with an automatic 6-month extension to
    October 15 (no form needed).
    """
    _validate_tax_year(tax_year)
    filed_year = tax_year + 1
    apr_stat, apr = datetime.date(filed_year, 4, 15), _fed_due(filed_year, 4, 15)
    oct_stat, octo = datetime.date(filed_year, 10, 15), _fed_due(filed_year, 10, 15)
    return [
        CalendarEvent(
            uid=f"fbar-statutory-{tax_year}@{_UID_NAMESPACE}",
            summary=f"FBAR statutory deadline (TY{tax_year})",
            date=apr,
            description=(
                f"FinCEN Form 114 (FBAR) reporting foreign financial accounts "
                f"held during {tax_year}. Statutory due date — automatic "
                f"6-month extension applies; no extension form needed."
                + _observed_note(apr_stat, apr)
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
                + _observed_note(oct_stat, octo)
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


def events_fl_property(tax_year: int, *, business_tpp: bool,
                       new_homestead: bool,
                       homestead_unfiled: bool = False) -> list[CalendarEvent]:
    """Florida COUNTY property-tax reminders (FL has no income tax; these
    are the FL-specific compliance dates an individual typically has).

    - Homestead exemption application (Form DR-501): FL homestead
      eligibility is determined as of JANUARY 1 (F.S. 196.011) — a taxpayer
      who newly establishes a FL homestead AFTER January 1 of tax_year does
      not own/occupy it as of Jan 1 of tax_year, so they are first eligible
      for exemption year tax_year + 1 and must file DR-501 by March 1,
      tax_year + 1. The interview flag (flags.fl_new_homestead, "newly
      established this year") is year-N semantics; the event itself is
      emitted for March 1 of tax_year + 1 — the same look-ahead-year
      pattern events_fbar uses (statutory deadline computed against
      tax_year + 1, the filing year, while the UID stays keyed to the
      generating tax_year for regeneration stability). Renewals are
      automatic in most counties. Round-10 P2: `/tax-setup`'s interview
      does NOT set this flag for an establishment date of exactly January 1
      of tax_year — that taxpayer already meets the January-1 test for
      tax_year itself, so it's routed to fl_homestead_unfiled below
      instead; this flag is for AFTER-January-1 establishment only.
    - Homestead exemption application, INITIAL filing NOT YET MADE
      (flags.fl_homestead_unfiled): a taxpayer who already owned and
      occupied a FL homestead as of January 1 of tax_year but has not yet
      filed DR-501 needs a CURRENT-year March 1 reminder — the gap
      fl_new_homestead's forward-looking event misses (a homestead
      established late in tax_year - 1, OR exactly ON January 1 of
      tax_year itself, is eligible NOW, in tax_year, not tax_year + 1).
      Distinct UID/flag from fl_new_homestead so both events can coexist
      without colliding (e.g. a taxpayer who forgot to file last
      year's newly-established homestead AND is asking about eligibility
      again this year).
    - Tangible personal property return (Form DR-405): due April 1 of
      tax_year with the county property appraiser (F.S. 193.062) for
      business tangible personal property physically located in Florida as
      of Jan 1 of tax_year; a TIMELY initial filing is required to claim the
      $25,000 exemption (F.S. 196.183) for tax_year >= _FL_TPP_EXEMPTION_YEAR
      (2008) — the exemption was created by Fla. Amendment 1 (approved Jan
      2008) and did not exist for earlier years, so a pre-2008 description
      omits the exemption promise and keeps only the filing deadline +
      penalties (round-4 finding D: DR-405 itself long predates 2008 and
      this module accepts tax_year back to _MIN_YEAR=2000, so an unqualified
      exemption sentence would misdescribe those years); late filing
      5%/month up to 25%. Round-7 finding D: the 2008+ description also
      notes the F.S. 196.183 CONTINUING-FILER WAIVER — after a timely
      INITIAL DR-405 establishes the exemption, subsequent annual returns
      are generally waived while value stays ≤ $25,000 (unless the
      appraiser requests one), so an otherwise-truthful reader shouldn't
      read the annual reminder as "you must file every single year." The
      calendar module has no per-taxpayer filing-history/value input, so it
      cannot decide whether THIS year's filing is actually required — that
      decision (and whether flags.fl_business_tangible_property is set at
      all) belongs to /tax-setup's interview; the reminder itself still
      fires every year the flag is set, as a "check whether you owe DR-405
      this year" prompt rather than an unconditional filing demand. Gated
      on flags.fl_business_tangible_property. DR-405 has no eligibility
      look-ahead, so it stays in tax_year.

    Unlike the L1 state convention (CA 540-ES / PTET roll past weekends),
    these COUNTY deadlines do NOT roll at all — the event stays ON the
    statutory date even when it falls on a weekend (Codex round-19 P2): no
    governing Florida rollover rule was verified for county property
    filings, and an alert moved to Monday would have a taxpayer in a
    non-rolling county file late. The weekend caveat lives in
    _fl_county_observed_note (NOT the shared _observed_note) — these are
    COUNTY filings, not federal acts, so citing §7503 here would be a
    false procedural claim.

    Gating (see build_events): ALL three sub-events are FLAG-driven,
    independent of the owner's address.state. DR-405 follows property
    SITUS (tangible personal property tax is assessed where the assets
    sit — a non-FL-resident with business equipment in Florida still owes
    the county filing). The two homestead events were residence-gated
    until Codex round-25 P2: F.S. 196.031 extends the homestead exemption
    to property maintained as the permanent residence of someone legally
    or naturally dependent upon the OWNER, so a non-FL-resident owner can
    hold an eligible FL homestead — the /tax-setup interview owns the
    eligibility screening and the calendar trusts the asserted flags,
    the same way PTET gates on entity state rather than residence (for a
    different reason — PTET is an entity-level election).
    """
    _validate_tax_year(tax_year)
    events: list[CalendarEvent] = []
    if new_homestead:
        elig_year = tax_year + 1
        stat = datetime.date(elig_year, 3, 1)
        events.append(CalendarEvent(
            uid=f"state-property-fl-homestead-{tax_year}@{_UID_NAMESPACE}",
            summary=f"FL homestead exemption application due ({elig_year})",
            date=stat,
            description=(
                f"Form DR-501 homestead exemption application for exemption "
                f"year {elig_year} is due March 1, {elig_year}, based on "
                f"ownership and occupancy as of January 1, {elig_year} "
                f"(F.S. 196.011). First-year filing; renewals are automatic "
                f"in most counties. Note: claiming FL homestead while "
                f"retaining an out-of-state residency-based benefit (e.g. "
                f"NY STAR) is disqualifying and a domicile-audit red flag."
                + _fl_county_observed_note(stat)
            ),
            category="state_property",
        ))
    if homestead_unfiled:
        stat = datetime.date(tax_year, 3, 1)
        events.append(CalendarEvent(
            uid=f"state-property-fl-homestead-initial-{tax_year}@{_UID_NAMESPACE}",
            summary=f"FL homestead exemption application due ({tax_year}) — initial filing",
            date=stat,
            description=(
                f"Form DR-501 homestead exemption application for exemption "
                f"year {tax_year} is due March 1, {tax_year}, based on "
                f"ownership and occupancy as of January 1, {tax_year} "
                f"(F.S. 196.011). This is an INITIAL filing for a homestead "
                f"already established as of January 1, {tax_year} (whether "
                f"established in a prior year or exactly ON January 1, "
                f"{tax_year} — both routes set this flag) that has not yet "
                f"been "
                f"claimed — file now to start the exemption; renewals are "
                f"automatic in most counties once filed. Note: claiming FL "
                f"homestead while retaining an out-of-state residency-based "
                f"benefit (e.g. NY STAR) is disqualifying and a "
                f"domicile-audit red flag."
                + _fl_county_observed_note(stat)
            ),
            category="state_property",
        ))
    if business_tpp:
        stat = datetime.date(tax_year, 4, 1)
        # Round-4 finding D: the $25,000 exemption (F.S. 196.183) took effect
        # for TY2008+ (Fla. Amendment 1, approved Jan 2008) — DR-405 itself
        # predates that, and this module accepts tax_year back to _MIN_YEAR
        # (2000), so an unqualified exemption promise misdescribes pre-2008
        # years. Pre-2008 keeps the filing deadline + penalties only.
        exemption_note = (
            " A timely initial filing is required to claim the $25,000 "
            "exemption (F.S. 196.183). Once that initial return is filed, "
            "subsequent annual returns are generally WAIVED while the "
            "property's value stays at or below $25,000 — unless the "
            "county property appraiser requests one (F.S. 196.183); this "
            "reminder recurs every year regardless, since eligibility for "
            "the waiver can change year to year."
        ) if tax_year >= _FL_TPP_EXEMPTION_YEAR else ""
        events.append(CalendarEvent(
            uid=f"state-property-fl-dr405-{tax_year}@{_UID_NAMESPACE}",
            summary=f"FL tangible personal property return due ({tax_year})",
            date=stat,
            description=(
                f"Form DR-405 tangible personal property return for business "
                f"tangible personal property located in Florida on January "
                f"1, {tax_year} is due April 1 with the county property "
                f"appraiser (F.S. 193.062). Applies based on where the "
                f"property SITS, not where you reside — you can owe this "
                f"even if you live outside Florida."
                + exemption_note +
                " Late filing: 5%/month penalty up to 25%; "
                "failure to file: 25%."
                + _fl_county_observed_note(stat)
            ),
            category="state_property",
        ))
    return events


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
    if code not in PTET_STATES:
        return []
    # Year-window gating: each state's PTE regime has an effective range.
    # Emitting reminders for a tax year outside the window would mislead
    # the user about an expired (or not-yet-enacted) regime — surface a
    # single info note instead.
    if not ptet_window_active(code, tax_year):
        first_ty, last_ty = PTET_WINDOWS[code]
        return [_ptet_year_out_of_range_note(tax_year, code, first_ty, last_ty)]
    out: list[CalendarEvent] = []
    if code == "CA":
        ca_pre_stat = datetime.date(tax_year, 6, 15)
        ca_pre_obs = _state_due(tax_year, 6, 15, "CA")
        out.append(CalendarEvent(
            uid=f"ptet-ca-prepay-{tax_year}@{_UID_NAMESPACE}",
            summary=f"CA PTET June prepayment due (TY{tax_year})",
            date=ca_pre_obs,
            description=(
                "California pass-through entity elective tax prepayment "
                "(R&TC §19904(a)). Pay the GREATER of $1,000 or 50% of the "
                "prior-year PTE elective tax. Underpayment consequences: "
                "for TY2022–2025, missing the payment forfeits the PTE "
                "election entirely for the year; for TY2026–2030, the "
                "election still stands but the qualified PTE credit is "
                "reduced by 12.5% of the underpaid amount — either way a "
                "high-cost SALT-cap workaround loss."
                + _observed_note(ca_pre_stat, ca_pre_obs, state="CA")
            ),
            category="ptet",
        ))
        ca_bal_stat = datetime.date(tax_year + 1, 3, 15)
        ca_bal_obs = _state_due(tax_year + 1, 3, 15, "CA")
        out.append(CalendarEvent(
            uid=f"ptet-ca-balance-{tax_year}@{_UID_NAMESPACE}",
            summary=f"CA PTET balance + election (TY{tax_year})",
            date=ca_bal_obs,
            description=(
                "California PTE balance due and Form 3804 election filing "
                f"for tax year {tax_year}. PTE election is made annually on "
                "the timely-filed pass-through return."
                + _observed_note(ca_bal_stat, ca_bal_obs, state="CA")
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
        ny_el_stat = datetime.date(tax_year, 3, 15)
        ny_el_obs = _state_due(tax_year, 3, 15, "NY")
        out.append(CalendarEvent(
            uid=f"ptet-ny-election-q1-{tax_year}@{_UID_NAMESPACE}",
            summary=f"NY PTET election + Q1 estimated payment (TY{tax_year})",
            date=ny_el_obs,
            description=(
                f"New York pass-through entity tax election for tax year "
                f"{tax_year} AND Q1 estimated payment. Election must be "
                f"made by the original return due date; quarterly estimates "
                f"follow on June 15, September 15, and December 15. Each "
                f"installment must be at least 25% of the required annual "
                f"PTET. See tax.ny.gov/bus/ptet/ for current procedures."
                + _observed_note(ny_el_stat, ny_el_obs, state="NY")
            ),
            category="ptet",
        ))
        for quarter, month in (("Q2", 6), ("Q3", 9), ("Q4", 12)):
            q_stat = datetime.date(tax_year, month, 15)
            q_obs = _state_due(tax_year, month, 15, "NY")
            out.append(CalendarEvent(
                uid=f"ptet-ny-{quarter.lower()}-{tax_year}@{_UID_NAMESPACE}",
                summary=f"NY PTET {quarter} estimated payment (TY{tax_year})",
                date=q_obs,
                description=(
                    f"New York PTET {quarter} estimated payment for tax "
                    f"year {tax_year}. At least 25% of the required annual "
                    f"PTET — pay via the Tax Department's PTET Web File "
                    f"application by ACH debit."
                    + _observed_note(q_stat, q_obs, state="NY")
                ),
                category="ptet",
            ))
        # Annual return + final payment: per NY Tax Department, the PTET
        # annual return is due March 15 after the close of the PTET
        # taxable year, and any unpaid PTET must be paid by then.
        ny_ar_stat = datetime.date(tax_year + 1, 3, 15)
        ny_ar_obs = _state_due(tax_year + 1, 3, 15, "NY")
        out.append(CalendarEvent(
            uid=f"ptet-ny-annual-return-{tax_year}@{_UID_NAMESPACE}",
            summary=f"NY PTET annual return + final payment (TY{tax_year})",
            date=ny_ar_obs,
            description=(
                f"New York PTET annual return for tax year {tax_year} is "
                f"due March 15, {tax_year + 1}. Any unpaid PTET must be "
                f"paid by this date — an extension of time to file is NOT "
                f"an extension of time to pay; penalties for unpaid tax "
                f"apply after the original due date."
                + _observed_note(ny_ar_stat, ny_ar_obs, state="NY")
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
            nj_stat = datetime.date(tax_year + year_offset, month, day)
            nj_obs = _state_due(tax_year + year_offset, month, day, "NJ")
            out.append(CalendarEvent(
                uid=f"ptet-nj-{quarter.lower()}-{tax_year}@{_UID_NAMESPACE}",
                summary=f"NJ BAIT {quarter} estimated payment (TY{tax_year})",
                date=nj_obs,
                description=(
                    f"New Jersey BAIT {quarter} estimated payment for tax "
                    f"year {tax_year}. PTE-150 quarterly installment — at "
                    f"least 25% of the required annual BAIT. Pay "
                    f"electronically via the NJ PTE/BAIT payment system."
                    + _observed_note(nj_stat, nj_obs, state="NJ")
                ),
                category="ptet",
            ))
        nj_el_stat = datetime.date(tax_year + 1, 3, 15)
        nj_el_obs = _state_due(tax_year + 1, 3, 15, "NJ")
        out.append(CalendarEvent(
            uid=f"ptet-nj-election-{tax_year}@{_UID_NAMESPACE}",
            summary=f"NJ BAIT election deadline (TY{tax_year})",
            date=nj_el_obs,
            description=(
                f"New Jersey Business Alternative Income Tax (BAIT) election "
                f"for tax year {tax_year}. Election is made on a timely-filed "
                f"Form PTE-100, due March 15, {tax_year + 1} for a calendar-"
                f"year entity (no extension of time to elect — extensions "
                f"file via PTE-200-T but do not move the election deadline)."
                + _observed_note(nj_el_stat, nj_el_obs, state="NJ")
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

    Per the state-specific window in the registry's `PTET_WINDOWS`, emit a single info
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
            "You reported K-1 income but `flags.k1_states` is empty. "
            "PTET is an entity-level state tax — the partnership / "
            "S-corp files in the state where it operates, NOT where "
            "the partner resides — so the calendar needs the entity "
            "state(s) to surface PTET deadlines. List every state "
            "where your K-1 income originates in taxpayer_profile.json "
            "under flags.k1_states (e.g. [\"CA\", \"NY\"]), then "
            "re-run /tax-calendar."
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

    # `_validate_tax_year` guards the CALLER's year; this is the profile's own,
    # read straight off a hand-edited file and never type-checked. It prints
    # only once it is known to be an integer year — a misplaced name or nested
    # object here would otherwise be echoed to the terminal, and `redact` masks
    # SSN and credential shapes only (AGENTS.md #1).
    profile_year = profile.get("tax_year")
    if profile_year is not None and profile_year != tax_year:
        raise CalendarError(
            f"taxpayer_profile.json is for {year_or_shape(profile_year)}, "
            f"not TY{tax_year}."
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
    # `flags.k1_states`. address.state IS used for the CA 540-ES events
    # below — personal estimated tax follows the RESIDENT state.
    events: list[CalendarEvent] = []
    events.extend(events_1040es(tax_year))

    resident_state = _strict_state_str(address, "state")
    if resident_state == "CA":
        events.extend(events_ca_es(tax_year))
    elif resident_state == "NY":
        events.extend(events_ny_es(tax_year))
    elif resident_state == "WA" and tax_year >= 2022:
        events.append(CalendarEvent(
            uid=f"wa-excise-{tax_year}@{_UID_NAMESPACE}",
            summary=f"WA capital-gains excise filing and payment (TY{tax_year})",
            date=_state_due(tax_year + 1, 4, 15, "WA"),
            description="Separate electronic My DOR return with federal copy. No required quarterly estimates. Pay by the original deadline; a federal extension alone is insufficient. Request WA extension separately through My DOR. Confirm WA holiday adjustment with DOR.",
            category="note"))
    elif resident_state == "NC":
        events.extend(events_nc_es(tax_year))

    # /tax-extend sets flags.filed_extension when the taxpayer extends —
    # the regenerated calendar then carries the April pay-by date and the
    # October extended filing deadline (both in tax_year + 1).
    if _strict_bool(flags, "filed_extension"):
        events.extend(events_extension(tax_year, state=resident_state))

    # Signature authority over someone else's foreign account (an employer's,
    # a parent's) triggers FBAR Part IV even with no financial interest — a
    # signature-authority-only taxpayer needs the same deadline reminders.
    if _strict_bool(flags, "has_foreign_accounts") \
            or _strict_bool(flags, "signature_authority_foreign_accounts"):
        events.extend(events_fbar(tax_year))

    events.extend(events_rmd(
        tax_year,
        primary_subject_to_rmd=_strict_bool(flags, "subject_to_rmd_primary"),
        spouse_subject_to_rmd=_strict_bool(flags, "subject_to_rmd_spouse"),
    ))

    # FL homestead deadlines follow the ASSERTED homestead flags, NOT the
    # owner's resident state (Codex round-25 P2 — this REPLACED the
    # pre-round-25 residence gate): F.S. 196.031 extends the exemption to
    # property maintained as the permanent residence of someone legally or
    # naturally dependent upon the owner, so a non-FL-resident owner whose
    # FL home is a dependent's permanent residence can be eligible — an
    # address.state == "FL" gate would suppress that owner's real DR-501
    # deadline. The flags are user-asserted at /tax-setup (like
    # subject_to_rmd_*), so the interview owns eligibility screening; the
    # calendar trusts the assertion — the same flag-driven pattern as
    # DR-405's situs gate below.
    events.extend(events_fl_property(
        tax_year,
        business_tpp=False,
        new_homestead=_strict_bool(flags, "fl_new_homestead"),
        homestead_unfiled=_strict_bool(flags, "fl_homestead_unfiled"),
    ))

    # FL tangible personal property (DR-405), by contrast, follows the
    # property's SITUS, not the taxpayer's residence — tangible personal
    # property tax is assessed where the assets physically sit, so a
    # non-FL-resident (e.g. a GA resident with FL business equipment) still
    # owes the county filing. Gated on flags.fl_business_tangible_property
    # ALONE, independent of resident_state — deliberately NOT folded into
    # the residence-gated block above.
    if _strict_bool(flags, "fl_business_tangible_property"):
        events.extend(events_fl_property(
            tax_year,
            business_tpp=True,
            new_homestead=False,
            homestead_unfiled=False,
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
        raise CalendarError(
            f"taxpayer_profile not found at {redact(profile_path)}")
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CalendarError(
            f"taxpayer_profile is not valid JSON: {redact(e)}") from e
    except UnicodeDecodeError as e:
        raise CalendarError(
            f"taxpayer_profile is not valid UTF-8 text: {redact(e)}"
        ) from e
    except OSError as e:  # PermissionError, IsADirectoryError, generic OSError
        raise CalendarError(
            f"could not read {redact(profile_path)}: {redact(e)}") from e

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
        print(f"Replacing existing {redact(out.name)}", file=sys.stderr)
    try:
        # Write bytes, not text, so the precise CRLF line endings produced
        # by to_ics() are preserved verbatim. Path.write_text() uses
        # text-mode I/O and translates `\n` to os.linesep on write, which
        # on Windows would turn our existing `\r\n` into `\r\r\n` —
        # malformed iCalendar. This bug is invisible on platforms where
        # `os.linesep == '\n'` (Linux, macOS), so cross-platform tests
        # assert against bytes-on-disk rather than text.
        #
        # ONE writer for every runtime artifact (V10, `secure_fs.atomic_write`):
        # created owner-only BEFORE the first byte rather than
        # written-then-chmod'd — the .ics carries the taxpayer's filing status,
        # state and payment schedule, and a file created at the umask default
        # would hold all of it before anything tightened it, on Windows with no
        # ACL at all until the PowerShell call returned. The temp file is
        # renamed on a clean exit and removed on any failure.
        with atomic_write(out, binary=True) as handle:
            handle.write(ics.encode("utf-8"))
    except OSError as e:
        raise CalendarError(f"failed to write {redact(out)}: {redact(e)}") from e
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agents.lib.tax_calendar")
    parser.add_argument("--profile", required=True, help="path to taxpayer_profile.json")
    add_workspace_argument(parser)
    parser.add_argument("--year", required=True, help="tax year (YYYY)")
    args = parser.parse_args(argv)

    try:
        year = _validate_year_arg(args.year)
        # `resolve_workspace` refuses a path that is not a usable directory,
        # so the local existence check that stood here is gone with it — one
        # answer to "is this a workspace", in the module that also decides
        # WHICH directory it is.
        workspace = resolve_workspace(args.workspace).resolve()
        # Relative to the WORKSPACE, not the process CWD — the skills run from
        # the checkout now, and `--profile taxpayer_profile.json` means the one
        # in the taxpayer's directory.
        out = write_calendar(resolve_under_workspace(args.profile, workspace),
                             workspace, tax_year=year)
    except (WorkspaceError, CalendarError, RolloverError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    print(f"Wrote {redact(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
